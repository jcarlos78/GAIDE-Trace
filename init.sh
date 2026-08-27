#!/bin/bash
# Session bootstrap — run at the start of every working session, BEFORE any new
# work. The goal: the code compiles, the invariants hold, the tests are green
# and the server actually answers. A failure here means the previous session
# left the project broken; fix that first (see the session protocol in
# AGENTS.md).
#
# GAIDE-Trace has no dependencies to install on the capture/server path (stdlib
# only, by design), so bring-up is: syntax -> invariants -> tests -> live
# health check against a throwaway server instance.
set -euo pipefail
cd "$(dirname "$0")"

echo "== GAIDE-Trace init: bring-up + health check =="

# 1. Interpreter: the floor the install docs promise.
python3 - <<'PY'
import sys
if sys.version_info < (3, 9):
    sys.exit(f"python3 >= 3.9 required, found {sys.version.split()[0]}")
print(f"python {sys.version.split()[0]}")
PY

# 2. Syntax: every module compiles.
find hooks tools server analysis -name '*.py' -not -path '*/__pycache__/*' -print0 \
  | xargs -0 python3 -m py_compile
echo "syntax OK"

# 3. Invariant: nothing outside the stdlib on the capture/server path.
scripts/check-stdlib-only.sh
echo "stdlib-only OK"

# 4. Event schema: the contract every adapter meets must itself be valid JSON.
python3 -m json.tool schema/event.schema.json > /dev/null
echo "event schema OK"

# 5. Test suite — red at session start means broken inherited state.
if command -v pytest >/dev/null 2>&1 && [ -d tests ]; then
  pytest -q || [ $? -eq 5 ]  # exit 5 = no tests collected yet
fi

# 6. Health check — start the server on a throwaway data dir and hit it as a
# client would. Proves ingest is reachable, not just that the file parses.
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
DATA_DIR="$(mktemp -d)"
# `wait` inside the trap keeps bash from printing its own "Terminated" notice.
cleanup() { { [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" && wait "$SERVER_PID"; } 2>/dev/null || true; rm -rf "$DATA_DIR"; }
trap cleanup EXIT

python3 server/gaide_trace_server.py --data "$DATA_DIR" serve --host 127.0.0.1 --port "$PORT" >"$DATA_DIR/server.log" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    echo "server healthz OK (port $PORT)"
    break
  fi
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "server died on start:" >&2; cat "$DATA_DIR/server.log" >&2; exit 1; }
  sleep 0.25
done
curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null || { echo "server did not become healthy:" >&2; cat "$DATA_DIR/server.log" >&2; exit 1; }

echo "== init OK =="
