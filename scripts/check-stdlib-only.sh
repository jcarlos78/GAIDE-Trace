#!/bin/bash
# Portable check, specific to this project: the capture and server path must
# import nothing outside the Python standard library (see AGENTS.md, Tech
# stack). A third-party import in hooks/, tools/, server/ or schema/ breaks
# the deployment story — a hook is copied verbatim into a target project and a
# server is expected to run on a bare VPS with no package manager. `analysis/`
# is exempt by design (pandas).
# Exit 1 with the offending imports on stderr; exit 0 otherwise. Skipped
# silently on Python < 3.10, which has no sys.stdlib_module_names.
#   usage: scripts/check-stdlib-only.sh [file ...]   (default: the whole path)
set -uo pipefail
cd "$(dirname "$0")/.."

FILES=("$@")
if [ ${#FILES[@]} -eq 0 ]; then
  while IFS= read -r f; do FILES+=("$f"); done < <(find hooks tools server schema -name '*.py' -not -path '*/__pycache__/*' 2>/dev/null)
fi
[ ${#FILES[@]} -eq 0 ] && exit 0

python3 - "${FILES[@]}" <<'PY'
import ast, sys
from pathlib import Path

stdlib = getattr(sys, "stdlib_module_names", None)
if stdlib is None:
    sys.exit(0)

EXEMPT_DIRS = {"analysis"}
violations = []

for arg in sys.argv[1:]:
    path = Path(arg)
    if path.suffix != ".py" or not path.is_file():
        continue
    parts = path.resolve().relative_to(Path.cwd()).parts
    if not parts or parts[0] not in {"hooks", "tools", "server", "schema"} or parts[0] in EXEMPT_DIRS:
        continue
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        continue  # check-file.sh reports syntax errors; not this check's job
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            roots = [node.module.split(".")[0]] if node.level == 0 and node.module else []
        else:
            continue
        for root in roots:
            if root not in stdlib and not (path.parent / f"{root}.py").exists():
                violations.append(f"{path}:{node.lineno}: imports '{root}' (not stdlib)")

if violations:
    print("Blocked: third-party import on the capture/server path.", file=sys.stderr)
    print("This path must stay standard-library only (AGENTS.md, Tech stack): hooks are copied", file=sys.stderr)
    print("into target projects and servers run without a package manager. Changing this needs an ADR.", file=sys.stderr)
    for v in violations:
        print(f"  {v}", file=sys.stderr)
    sys.exit(1)
PY
