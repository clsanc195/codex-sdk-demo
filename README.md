# Change Risk Desk: a Codex SDK demo (Python)

A small web app with the Codex Python SDK (`openai-codex`) as its agent runtime
and an MCP server supplying internal knowledge. Pick a change request, and the
agent checks it against change policies and returns a structured verdict.

All change requests, services and policies are fictional.

## What is in here

| Path | What it is | Code level |
| --- | --- | --- |
| `.codex/config.toml` | Declares the MCP server, its tool allow list and approval mode | Config only |
| `app.py` | FastAPI service. `assess_with_codex()` is the SDK integration | Low-code |
| `public/index.html` | The page: picker, live event stream, verdict | Code |
| `mcp_server/change_policy_server.py` | Stub internal MCP server over STDIO, three read-only tools | Code |
| `data/*.json` | Fictional change requests, services, policies | Data |

## Run it

Requires Python 3.11 or later.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# Replay mode: no model call, no sign-in needed.
DEMO_MODE=replay .venv/bin/python -m uvicorn app:app --port 4318

# Live mode: a real Codex turn. Uses the saved `codex login` if there is one,
# otherwise OPENAI_API_KEY or CODEX_API_KEY from the environment.
.venv/bin/python -m uvicorn app:app --port 4318
```

Open http://localhost:4318. `run-live.command` and `run-replay.command` do the
same from Finder; they log which key variable names are set, never the values.

Optional: `CODEX_MODEL=<model>` to pick a model.

Replay mode makes real calls to the MCP server declared in
`.codex/config.toml`, then produces the verdict with a scripted rule check
(`scripted_verdict` in `app.py`). The page labels this as simulated.

## How the pieces connect

- `app.py` reads `[mcp_servers]` from `.codex/config.toml` and passes them to
  the thread: `codex.thread_start(config={"mcp_servers": ...})`.
- `thread_start(sandbox=Sandbox.read_only, approval_mode=ApprovalMode.deny_all)`
  starts the conversation; `thread.turn(prompt, output_schema=...)` runs it.
- `turn.stream()` yields notifications; the service forwards item, usage and
  completion events to the page as server-sent events and logs them to
  `logs/<mode>-<change>.jsonl`.

## Point it at a real internal MCP server

Edit `.codex/config.toml`. For a remote server over Streamable HTTP:

```toml
[mcp_servers.change_policy]
url = "https://mcp.internal.example/change-policy/mcp"
bearer_token_env_var = "CHANGE_POLICY_MCP_TOKEN"
enabled_tools = ["get_change_request", "get_service_profile", "search_policies"]
default_tools_approval_mode = "writes"
```

If the tool names differ, update `enabled_tools` and the prompt in `app.py`.
Replay mode only supports a STDIO server.

## Status: what was and was not verified

Verified (openai-codex 0.167.1, mcp 2.3.0; live run on macOS with Python 3.13, Nov 10 2026):

- The Python MCP server answers real tool calls and reports its tools read-only.
- Replay mode runs end to end for all three change requests.
- A real Codex turn on CHG-1042 completed in 22.0 seconds, with the MCP servers
  passed through `thread_start(config=...)`. Both a saved Codex login and
  `OPENAI_API_KEY` were present; the service does not record which one
  authenticated the run.
- All five MCP tool calls in that turn ran unattended (`ApprovalMode.deny_all`
  plus `default_tools_approval_mode = "approve"`).
- The final answer was valid JSON matching `ASSESSMENT_SCHEMA`: high risk,
  violations on CHG-POL-001 and CHG-POL-004, needs_info on CHG-POL-015 and
  CHG-POL-007.
- Usage for that turn: 74,959 input tokens (41,856 cached), 757 output.

Not verified:

- Verdict quality and consistency. Three live runs on CHG-1042 agreed on high
  risk and both violations, but differed on whether CHG-POL-007 was raised.
- CHG-1043 and CHG-1044 in live mode.
- Which credential path ran (saved login or the `login_api_key` fallback).
- Any real internal MCP server, and the remote (Streamable HTTP) transport.
- Cost in dollars.
- The sandbox and approval limits under stress.

## Video

`video/codex-sdk-showcase.mp4` (5:55) walks through the SDK, this code and the
recorded run. Narration is synthetic speech generated with Google AI Studio.

## Sources

- Codex SDK: https://developers.openai.com/codex/sdk
- Codex MCP: https://developers.openai.com/codex/mcp
- Python SDK source, read from the installed `openai_codex` package
