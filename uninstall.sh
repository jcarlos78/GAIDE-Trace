#!/usr/bin/env bash
# Disconnect GAIDE-Trace from a project. Trace data in .gaide-trace/ is kept.
set -euo pipefail

TARGET="${1:?Usage: ./uninstall.sh /path/to/target-project}"

python3 - "$TARGET" <<'PY'
import json, sys
from pathlib import Path

target = Path(sys.argv[1])
for name in ("settings.local.json", "settings.json"):
    p = target / ".claude" / name
    if not p.is_file():
        continue
    settings = json.loads(p.read_text() or "{}")
    hooks = settings.get("hooks", {})
    changed = False
    for ev in list(hooks):
        kept = [e for e in hooks[ev] if "trace_hook.py" not in json.dumps(e)]
        if len(kept) != len(hooks[ev]):
            changed = True
        if kept:
            hooks[ev] = kept
        else:
            del hooks[ev]
    if changed:
        p.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
        print(f"removed trace hooks from {p}")

hook = target / ".claude" / "hooks" / "trace_hook.py"
if hook.is_file():
    hook.unlink()
    print(f"removed {hook}")
PY

echo "GAIDE-Trace disconnected. Data in $TARGET/.gaide-trace/ was preserved."
