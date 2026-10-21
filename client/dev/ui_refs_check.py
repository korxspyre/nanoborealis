# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks that the app's screens only use what the app object has: every `app.<name>` and
`self.app.<name>` in the UI modules is a method or attribute of NanoBorealisApp (main.py). A leftover
reference to something removed only fails when that screen is drawn, which linting doesn't catch.

    python dev/ui_refs_check.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
UI = ["workspace.py", "control.py", "council_view.py", "panels.py", "transcript.py", "model_picker.py"]


def main() -> None:
    source = (SRC / "main.py").read_text(encoding="utf-8")
    app = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "NanoBorealisApp")
    known = {f.name for f in app.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
    known |= set(re.findall(r"self\.(\w+)\s*(?::[^=\n]*)?=", source))
    missing = []
    for name in UI:
        path = SRC / name
        if not path.exists():
            continue
        for attribute in sorted(set(re.findall(r"\b(?:self\.)?app\.(\w+)", path.read_text(encoding="utf-8")))):
            if attribute not in known:
                missing.append(f"{name}: app.{attribute}")
    if missing:
        print("FAIL the app has no:\n  " + "\n  ".join(missing))
        sys.exit(1)
    print("ok   every screen uses only what the app has")


if __name__ == "__main__":
    main()
