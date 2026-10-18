#!/usr/bin/env bash
# ODT-CC — start everything for the demo, in order, each health-checked:
#   1. Nemotron NIM (docker container "nemotron", GPU 1, port 8001)
#   2. brain service (uvicorn odtcc.server:app, port 8000; it creates usd/twin_live.usda)
#   3. Kit Base Editor with the command-center panel (GPU 0)
# Anything already running is left alone. Logs: ~/server.log, ~/editor.log
set -uo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT"

nim_ready() { curl -sf -m 3 localhost:8001/v1/health/ready >/dev/null; }
server_ready() { curl -sf -m 3 localhost:8000/health | grep -q ready; }

echo "1/3 Nemotron NIM"
if ! nim_ready; then
  docker start nemotron >/dev/null && echo "    started container 'nemotron', waiting (about 2 min)..."
  for _ in $(seq 1 60); do nim_ready && break; sleep 5; done
fi
if nim_ready; then echo "    ready"; else echo "    NOT READY: cards will fall back to the template (still works)"; fi

echo "2/3 Brain service"
if server_ready; then
  echo "    already running (restart it yourself to load new code)"
else
  nohup .venv/bin/uvicorn odtcc.server:app --port 8000 --host 0.0.0.0 > "$HOME/server.log" 2>&1 &
  for _ in $(seq 1 30); do server_ready && break; sleep 1; done
  if server_ready; then echo "    ready"; else echo "    FAILED: see ~/server.log"; exit 1; fi
fi

echo "3/3 Kit editor"
if pgrep -f "release/kit/kit .*my_editor" >/dev/null; then
  echo "    already running"
else
  scripts/launch_editor.sh
fi
echo "Done. In Kit: pick a scenario, press Play."
