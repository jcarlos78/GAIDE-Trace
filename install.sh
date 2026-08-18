#!/usr/bin/env bash
# GAIDE-Trace connector — opt-in installation into any Claude Code project.
#
# Usage:
#   ./install.sh /path/to/target-project [--project]
#
# By default the hooks are written to .claude/settings.local.json in the
# target project (personal, gitignored — each user chooses to connect).
# Pass --project to write to .claude/settings.json instead (shared, committed:
# every contributor of that repo gets tracing).
set -euo pipefail

TARGET="${1:?Usage: ./install.sh /path/to/target-project [--project]}"
SCOPE="${2:-local}"
TRACE_ROOT="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d "$TARGET" ]; then
  echo "error: target project '$TARGET' does not exist" >&2
  exit 1
fi

mkdir -p "$TARGET/.claude/hooks"
cp "$TRACE_ROOT/hooks/trace_hook.py" "$TARGET/.claude/hooks/trace_hook.py"
chmod +x "$TARGET/.claude/hooks/trace_hook.py"

if [ "$SCOPE" = "--project" ]; then
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

# Keep raw traces out of the target repo's git history by default.
# (Researchers who WANT them versioned can remove this line or use a
# dedicated data repo — see docs/ARCHITECTURE.md.)
GITIGNORE="$TARGET/.gitignore"
if [ -f "$GITIGNORE" ] && ! grep -q "^\.gaide-trace/" "$GITIGNORE"; then
  printf "\n# GAIDE-Trace local store (connect to a data repo if you want it versioned)\n.gaide-trace/\n" >> "$GITIGNORE"
fi

echo "GAIDE-Trace connected to $TARGET"
echo "Traces will accumulate in $TARGET/.gaide-trace/ (events/ + transcripts/)"
echo "Disable anytime: export GAIDE_TRACE_DISABLE=1, or run ./uninstall.sh $TARGET"
