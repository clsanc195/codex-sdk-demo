#!/bin/zsh
# Double-click in Finder, or run ./run-live.command in Terminal.
# Starts the Python demo in LIVE mode (a real Codex turn) on port 4318 and opens the page.
cd "$(dirname "$0")" || exit 1
mkdir -p logs
if [[ -z "$OPENAI_API_KEY" && -z "$CODEX_API_KEY" ]]; then
  for f in ~/.zshenv ~/.zprofile ~/.zshrc; do [[ -f $f ]] && source $f >/dev/null 2>&1; done
fi
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v $c >/dev/null && $c -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then PY=$c; break; fi
done
{
  echo "started: $(date)"
  echo "python: ${PY:-none found (need 3.11+)} $($PY --version 2>&1)"
  # Names only. Values are never printed or written anywhere.
  for name in OPENAI_API_KEY CODEX_API_KEY OPENAI_BASE_URL; do
    if [[ -n "${(P)name}" ]]; then echo "env $name: set"; else echo "env $name: not set"; fi
  done
  if [[ -f ~/.codex/auth.json ]]; then echo "saved codex login file: yes"; else echo "saved codex login file: no"; fi
} > logs/launch.log 2>&1
cat logs/launch.log
if [[ -z "$PY" ]]; then echo "Python 3.11 or later not found."; read -k1; exit 1; fi
if [[ ! -x .venv/bin/python ]]; then echo "Creating .venv ..."; $PY -m venv .venv || { echo "venv failed"; read -k1; exit 1; }; fi
if ! .venv/bin/python -c 'import openai_codex, mcp, fastapi, uvicorn' 2>/dev/null; then
  echo "Installing dependencies (first run only) ..."
  .venv/bin/python -m pip install -q -r requirements.txt 2>&1 | tee logs/pip-install.log
fi
.venv/bin/python -c 'import openai_codex, mcp; print("openai-codex", openai_codex.__version__)' >> logs/launch.log 2>&1
(sleep 4; open "http://localhost:4318") &
echo "Starting live mode on http://localhost:4318  (Ctrl+C to stop)"
DEMO_MODE=live .venv/bin/python -m uvicorn app:app --port 4318 2>&1 | tee logs/server.log
