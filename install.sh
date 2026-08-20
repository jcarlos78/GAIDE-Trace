#!/usr/bin/env bash
# GAIDE-Trace connector — opt-in installation into any Claude Code project.
#
# Usage:
#   ./install.sh /path/to/target-project [--project] \
#                [--server URL --token KEY] [--name PROJECT_NAME]
#
# By default the hooks are written to .claude/settings.local.json in the
# target project (personal, gitignored — each user chooses to connect).
# Pass --project to write to .claude/settings.json instead (shared, committed:
# every contributor of that repo gets tracing).
#
# --server/--token connect the store to a GAIDE-Trace team server (see
# docs/SERVER.md): capture stays local-first, and every record is also
# shipped to the server with offline-safe retry. The token is stored in
# <target>/.gaide-trace/config.json (gitignored with the store).
set -euo pipefail

TARGET="${1:?Usage: ./install.sh /path/to/target-project [--project] [--server URL --token KEY] [--name NAME]}"
shift
TRACE_ROOT="$(cd "$(dirname "$0")" && pwd)"

SCOPE="local"
SERVER=""
TOKEN=""
PROJECT_NAME=""
while [ $# -gt 0 ]; do
  case "$1" in
    --project) SCOPE="project"; shift ;;
    --server)  SERVER="${2:?--server needs a URL}"; shift 2 ;;
    --token)   TOKEN="${2:?--token needs a key}"; shift 2 ;;
    --name)    PROJECT_NAME="${2:?--name needs a value}"; shift 2 ;;
    *) echo "error: unknown option '$1'" >&2; exit 1 ;;
  esac
done

if [ ! -d "$TARGET" ]; then
  echo "error: target project '$TARGET' does not exist" >&2
  exit 1
fi
if [ -n "$SERVER" ] && [ -z "$TOKEN" ]; then
  echo "error: --server requires --token (create one on the server: key create)" >&2
  exit 1
fi

mkdir -p "$TARGET/.claude/hooks"
cp "$TRACE_ROOT/hooks/trace_hook.py" "$TARGET/.claude/hooks/trace_hook.py"
chmod +x "$TARGET/.claude/hooks/trace_hook.py"

if [ "$SCOPE" = "project" ]; then
  SETTINGS="$TARGET/.claude/settings.json"
else
  SETTINGS="$TARGET/.claude/settings.local.json"
fi

python3 - "$SETTINGS" <<'PY'
import json, sys
from pathlib import Path

settings_path = Path(sys.argv[1])
settings = {}
if settings_path.is_file():
    settings = json.loads(settings_path.read_text() or "{}")

CMD = "python3 \"${CLAUDE_PROJECT_DIR}/.claude/hooks/trace_hook.py\""
EVENTS = [
    "SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure",
    "Stop", "SubagentStart", "SubagentStop", "PreCompact", "SessionEnd",
]

hooks = settings.setdefault("hooks", {})
for ev in EVENTS:
    entries = hooks.setdefault(ev, [])
    already = any(
        CMD in json.dumps(e) for e in entries
    )
    if not already:
        entries.append({"hooks": [{"type": "command", "command": CMD, "timeout": 15}]})

settings_path.parent.mkdir(parents=True, exist_ok=True)
settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
print(f"hooks registered in {settings_path}")
PY

# Team server connection: token lives with the (gitignored) store, not in
# committed settings.
if [ -n "$SERVER" ]; then
  mkdir -p "$TARGET/.gaide-trace"
  SERVER="$SERVER" TOKEN="$TOKEN" PROJECT_NAME="$PROJECT_NAME" TARGET="$TARGET" python3 - <<'PY'
import json, os
from pathlib import Path

cfg_path = Path(os.environ["TARGET"]) / ".gaide-trace" / "config.json"
cfg = {}
if cfg_path.is_file():
    cfg = json.loads(cfg_path.read_text() or "{}")
cfg["server"] = os.environ["SERVER"].rstrip("/")
cfg["token"] = os.environ["TOKEN"]
if os.environ.get("PROJECT_NAME"):
    cfg["project"] = os.environ["PROJECT_NAME"]
cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
os.chmod(cfg_path, 0o600)
print(f"server connection written to {cfg_path}")
PY
fi

# Keep raw traces out of the target repo's git history by default.
# (Researchers who WANT them versioned can remove this line or use a
# dedicated data repo — see docs/ARCHITECTURE.md.)
GITIGNORE="$TARGET/.gitignore"
if [ -f "$GITIGNORE" ] && ! grep -q "^\.gaide-trace/" "$GITIGNORE"; then
  printf "\n# GAIDE-Trace local store (connect to a data repo if you want it versioned)\n.gaide-trace/\n" >> "$GITIGNORE"
fi

echo "GAIDE-Trace connected to $TARGET"
echo "Traces will accumulate in $TARGET/.gaide-trace/ (events/ + transcripts/)"
if [ -n "$SERVER" ]; then
  echo "Shipping to team server: $SERVER (offline-safe: outbox retries until delivered)"
  echo "Backfill existing local data: python3 $TRACE_ROOT/tools/backfill.py $TARGET/.gaide-trace"
fi
echo "Disable anytime: export GAIDE_TRACE_DISABLE=1, or run ./uninstall.sh $TARGET"
