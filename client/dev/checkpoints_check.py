# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Undo for agents' jobs on a device: a job that may change something gets a checkpoint, and restoring it puts the
folder back (keeping what was there beside it). A job that only reads takes none."""

import os
import sys
import tempfile

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()  # checkpoints and the hands log go here
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import checkpoints  # noqa: E402
import hands  # noqa: E402
from permissions import Log, Policy  # noqa: E402

failed = 0


def check(ok: bool, what: str) -> None:
    global failed
    print(("ok   " if ok else "FAIL ") + what)
    failed += not ok


work = tempfile.mkdtemp()
with open(os.path.join(work, "a.txt"), "w") as f:
    f.write("before\n")
os.makedirs(os.path.join(work, "node_modules", "x"))
with open(os.path.join(work, "node_modules", "x", "big.js"), "w") as f:
    f.write("rebuilt anyway\n")

policy = Policy(mode="scoped")
log = Log(hands.home() / "hands.log")
change = ("Set-Content -Path a.txt -Value after" if os.name == "nt" else "echo after > a.txt")
result = hands.handle({"id": "j1", "command": change, "cwd": work, "task": {"request": "edit a", "folders": [work]}},
                      policy, lambda *a: "deny", log)
check(result.get("exit") == 0 and "[checkpoint " in result["output"], "a job that changes something gets a checkpoint first")
check(open(os.path.join(work, "a.txt")).read().strip() == "after", "...and then runs")

points = checkpoints.entries()
check(len(points) == 1 and points[0]["copied"] == [work], "the checkpoint lists the folder it copied")
check(not os.path.exists(checkpoints.base() / points[0]["id"] / "0" / "node_modules"), "what is rebuilt anyway isn't copied")

done = checkpoints.restore(points[0]["id"])
check(done == [work] and open(os.path.join(work, "a.txt")).read().strip() == "before", "undo puts the folder back")
aside = [n for n in os.listdir(os.path.dirname(work)) if n.startswith(os.path.basename(work) + ".before-undo-")]
check(len(aside) == 1, "...keeping what was there beside it, so the undo can be undone too")

read = "Get-Content a.txt" if os.name == "nt" else "cat a.txt"
hands.handle({"id": "j2", "command": read, "cwd": work, "task": {"request": "read a", "folders": [work]}},
             policy, lambda *a: "deny", log)
check(len(checkpoints.entries()) == 1, "a job that only reads takes no checkpoint")

print("checkpoint checks passed" if not failed else f"{failed} checkpoint check(s) failed")
sys.exit(1 if failed else 0)
