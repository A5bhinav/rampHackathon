#!/usr/bin/env python3
"""The `boundaries` CI check (design, "CI jobs"): no imports between apps.

Each app's src/ may import shared packages (packages/*) but never another app. Tests may
(core's tests run the Ramp mock in process on purpose). Run from the repository root:

    python3 scripts/ci/boundaries.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY_APPS = {"core": "dialback_core", "voice": "dialback_voice", "ramp-mock": "dialback_ramp_mock"}
TS_IMPORT = re.compile(r"""(?:from|import)\s*\(?\s*["']([^"']+)["']""")


def python_violations() -> list[str]:
    found = []
    for app, own in PY_APPS.items():
        others = {mod for name, mod in PY_APPS.items() if name != app}
        for path in (ROOT / "apps" / app / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else []
                for name in names:
                    if name.split(".")[0] in others:
                        found.append(f"{path.relative_to(ROOT)}:{node.lineno}: {own} imports {name}")
    return found


def web_violations() -> list[str]:
    found = []
    for path in (ROOT / "apps" / "web" / "src").rglob("*.ts*"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for target in TS_IMPORT.findall(line):
                if re.search(r"(^|/)apps/(core|voice|ramp-mock)(/|$)", target) or target.startswith("../../../apps"):
                    found.append(f"{path.relative_to(ROOT)}:{lineno}: imports {target}")
    return found


def main() -> int:
    problems = python_violations() + web_violations()
    for p in problems:
        print(p)
    print("boundaries ok" if not problems else f"{len(problems)} import(s) cross an app boundary")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
