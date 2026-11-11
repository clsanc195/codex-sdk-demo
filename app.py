"""Change-risk assessment service.

The Codex SDK is the agent runtime; our internal tools reach it through MCP
servers declared in .codex/config.toml.

  DEMO_MODE=live    (default) a real Codex turn. Needs Codex auth.
  DEMO_MODE=replay  no model call. Tool calls are REAL calls to the MCP server in
                    .codex/config.toml; the verdict comes from a scripted rule check.
                    Every event is marked simulated and the page says so.
"""
import asyncio
import json
import os
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai_codex import ApprovalMode, Codex, Sandbox
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
MODE = "replay" if os.environ.get("DEMO_MODE") == "replay" else "live"


# --- 1. Configuration: one file, .codex/config.toml --------------------------
def load_mcp_servers() -> dict:
    """Return the [mcp_servers] tables from .codex/config.toml as a dict."""
    config = tomllib.loads((ROOT / ".codex" / "config.toml").read_text())
    servers = config.get("mcp_servers", {})
    for spec in servers.values():
        # The agent runs in ./workspace, so make relative paths absolute.
        if spec.get("command", "").startswith("."):
            spec["command"] = str(ROOT / spec["command"])
        spec["args"] = [str(ROOT / a) if a.endswith(".py") and not os.path.isabs(a) else a for a in spec.get("args", [])]
    return servers


# --- 2. The contract for the answer (structured output) ----------------------
ASSESSMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["change_id", "risk_level", "summary", "findings", "required_actions"],
    "properties": {
        "change_id": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["policy_id", "status", "evidence"],
                "properties": {
                    "policy_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["compliant", "violation", "needs_info"]},
                    "evidence": {"type": "string"},
                },
            },
        },
        "required_actions": {"type": "array", "items": {"type": "string"}},
    },
}


PROMPT_TEMPLATE = (ROOT / "prompts" / "assess_change.md").read_text()


def prompt(change_id: str) -> str:
    """The task for one turn: prompts/assess_change.md with the change id filled in."""
    return PROMPT_TEMPLATE.replace("{change_id}", change_id).strip()


# --- 3. Codex SDK integration ---------------------------------------------------
STREAMED = {"turn/started", "item/started", "item/completed", "thread/tokenUsage/updated", "turn/completed", "error"}


def assess_with_codex(change_id: str):
    with Codex() as codex:
        # Auth: the saved `codex login` if there is one, otherwise an API key from the environment.
        api_key = os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if codex.account().account is None and api_key:
            codex.login_api_key(api_key)

        thread = codex.thread_start(
            # Tool wiring: the MCP servers this thread can use. Codex starts each
            # server, lists its tools and applies enabled_tools and the approval mode.
            config={"mcp_servers": load_mcp_servers(), "web_search": "disabled"},
            sandbox=Sandbox.read_only,
            approval_mode=ApprovalMode.deny_all,
            cwd=str(ROOT / "workspace"),
            model=os.environ.get("CODEX_MODEL"),
        )
        yield {"type": "thread.started", "thread_id": thread.id}

        turn = thread.turn(prompt(change_id), output_schema=ASSESSMENT_SCHEMA)
        for note in turn.stream():
            if note.method in STREAMED:
                payload = note.payload
                body = payload.model_dump(mode="json", by_alias=True, exclude_none=True) if isinstance(payload, BaseModel) else {}
                yield {"type": note.method.replace("/", "."), **body}


# --- 4. No-model mode: real MCP calls, scripted verdict --------------------------
async def assess_without_model(change_id: str):
    from mcp import Client, StdioServerParameters

    name, spec = next(iter(load_mcp_servers().items()))
    delay = float(os.environ.get("REPLAY_DELAY_MS", "700")) / 1000
    counter = 0
    async with Client(StdioServerParameters(command=spec["command"], args=spec["args"])) as client:

        async def call(tool: str, args: dict):
            nonlocal counter
            base = {"id": f"item_{counter}", "type": "mcp_tool_call", "server": name, "tool": tool, "arguments": args}
            counter += 1
            started = {"type": "item.started", "item": {**base, "status": "in_progress"}}
            await asyncio.sleep(delay)
            result = await client.call_tool(tool, args)
            content = [c.model_dump(mode="json") for c in result.content]
            failed = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
            done = {"type": "item.completed", "item": {**base, "status": "failed" if failed else "completed", "result": {"content": content}}}
            return [started, done], (None if failed else json.loads(content[0]["text"]))

        yield {"type": "thread.started", "thread_id": "replay-not-a-real-thread"}
        yield {"type": "turn.started"}
        events, change = await call("get_change_request", {"change_id": change_id})
        for e in events:
            yield e
        if change is None:
            yield {"type": "error", "message": f"Unknown change {change_id}"}
            return
        events, service = await call("get_service_profile", {"service": change["service"]})
        for e in events:
            yield e
        policies = {}
        for query in (change["type"], "freeze deploy", "review approval"):
            events, hits = await call("search_policies", {"query": query})
            for e in events:
                yield e
            policies.update({p["id"]: p for p in hits or []})
        await asyncio.sleep(delay)
        verdict = scripted_verdict(change, service, policies)
        yield {"type": "item.completed", "item": {"id": f"item_{counter}", "type": "agent_message", "text": json.dumps(verdict)}}
        yield {"type": "turn.completed", "usage": None}


def scripted_verdict(c: dict, s: dict, policies: dict) -> dict:
    """Plain rule check used ONLY in replay mode. It is not a model and not the product."""
    findings, actions = [], []

    def add(policy_id, status, evidence, action=None):
        if policy_id in policies:
            findings.append({"policy_id": policy_id, "status": status, "evidence": evidence})
            if action:
                actions.append(action)

    tier1 = s["tier"] == 1
    when = datetime.fromisoformat(c["scheduled_for"].replace("Z", "+00:00"))
    parse = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00"))
    freeze = next((f for f in s["freeze_windows"] if parse(f["start"]) <= when < parse(f["end"])), None)
    if "schema" in c["type"]:
        no_rollback = not c["rollback_plan"]
        stale = "drop" in c["title"].lower() and (c["last_backup_hours_ago"] or 10**9) > 24
        bad = no_rollback or stale
        add("CHG-POL-001", "violation" if bad else "compliant",
            f"rollback_plan={json.dumps(c['rollback_plan'])}, last_backup_hours_ago={c['last_backup_hours_ago']}, tier={s['tier']}",
            "Attach a tested rollback script and take a verified backup within 24 hours of the change." if bad else None)
    if tier1:
        blocked = bool(freeze) and not c["emergency"]
        add("CHG-POL-004", "violation" if blocked else "compliant",
            f"scheduled_for={c['scheduled_for']} falls inside \"{freeze['name']}\" and emergency={str(c['emergency']).lower()}" if freeze
            else f"scheduled_for={c['scheduled_for']} is outside every freeze window",
            f"Move the change outside \"{freeze['name']}\" or get emergency approval." if blocked else None)
    if "deploy" in c["type"]:
        add("CHG-POL-007", "needs_info" if tier1 else "compliant",
            "Tier-1 deploy; the request does not describe a canary step." if tier1
            else f"tier={s['tier']}; the canary rule covers tier-1 only. Description: \"{c['description']}\"",
            "Describe the canary step." if tier1 else None)
    if "flag" in c["type"]:
        add("CHG-POL-011", "compliant" if re.search("default off", c["title"] + c["description"], re.I) else "needs_info", f"\"{c['description']}\"")
    add("CHG-POL-015", "needs_info",
        f"reviewers={json.dumps(c['reviewers'])}; the request does not say which team each reviewer is on (tier={s['tier']}).",
        "Confirm one reviewer is outside the author's team." if tier1 else None)
    bad = sum(f["status"] == "violation" for f in findings)
    where = f"{c['id']} on {c['service']} (tier {s['tier']})"
    return {
        "change_id": c["id"],
        "risk_level": "high" if bad >= 2 else "medium" if bad == 1 else "low",
        "summary": f"{bad} policy violation{'s' if bad > 1 else ''} found for {where}." if bad else f"No policy violations found for {where}.",
        "findings": findings,
        "required_actions": actions,
    }


# --- 5. HTTP: list changes, stream one assessment as server-sent events --------
app = FastAPI(title="Change Risk Desk")


class AssessRequest(BaseModel):
    change_id: str


@app.get("/api/meta")
def meta():
    servers = load_mcp_servers()
    return {"mode": MODE, "sdk": "python", "mcp_servers": {
        name: {"transport": "streamable-http" if "url" in spec else "stdio", "enabled_tools": spec.get("enabled_tools")}
        for name, spec in servers.items()}}


@app.get("/api/changes")
def changes():
    """Convenience for the picker only. The agent never uses this route; it goes through MCP."""
    return json.loads((ROOT / "data" / "changes.json").read_text())


@app.post("/api/assess")
def assess(request: AssessRequest):
    change_id = request.change_id
    if not re.fullmatch(r"CHG-\d+", change_id):
        raise HTTPException(400, "change_id must look like CHG-1042")
    (ROOT / "logs").mkdir(exist_ok=True)
    log_file = ROOT / "logs" / f"{MODE}-{change_id}.jsonl"
    log_file.write_text("")

    def record(event: dict) -> str:
        """Keep a record of each run (events only, never credentials) and format it for the browser."""
        if MODE == "replay":
            event = {**event, "simulated": True}
        with log_file.open("a") as f:
            f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **event}) + "\n")
        return f"data: {json.dumps(event)}\n\n"

    def codex_events():
        try:
            for event in assess_with_codex(change_id):
                yield record(event)
        except Exception as error:  # surface the failure on the page
            yield record({"type": "error", "message": f"{type(error).__name__}: {error}"})

    async def no_model_events():
        try:
            async for event in assess_without_model(change_id):
                yield record(event)
        except Exception as error:
            yield record({"type": "error", "message": f"{type(error).__name__}: {error}"})

    return StreamingResponse(codex_events() if MODE == "live" else no_model_events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="public")
