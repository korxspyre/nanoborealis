# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Agents opening things on a lent device's screen: what opens at once, what asks, what never does."""

import os
import sys
import tempfile

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import hands  # noqa: E402
from permissions import Log, Policy  # noqa: E402

failed = 0
opened: list[str] = []
hands.open_native = lambda target: (opened.append(target), {"exit": 0, "timed_out": False, "output": f"opened {target}"})[1]


def check(ok: bool, what: str) -> None:
    global failed
    print(("ok   " if ok else "FAIL ") + what)
    failed += not ok


work = tempfile.mkdtemp()
page = os.path.join(work, "report.html")
open(page, "w").write("<h1>hi</h1>")
log = Log(hands.home() / "hands.log")
asked: list[str] = []


def approver(answer):
    def approve(command, cwd, task, decision, job_id=""):
        asked.append(decision.reason)
        return answer
    return approve


def job(target):
    return {"id": "j", "open": target, "task": {"request": "show me the report", "folders": [work]}}


r = hands.handle(job(page), Policy(mode="scoped"), approver("deny"), log)
check(r["exit"] == 0 and opened[-1] == page and not asked, "a file inside the request's folders opens at once")
r = hands.handle(job("https://example.com"), Policy(mode="scoped"), approver("deny"), log)
check(r.get("refused") and asked and "web page" in asked[-1], "a web page asks first, and a no means it doesn't open")
r = hands.handle(job("notepad"), Policy(mode="scoped"), approver("once"), log)
check(r["exit"] == 0 and opened[-1] == "notepad", "a program opens once you say yes")
r = hands.handle(job("https://example.com"), Policy(mode="trust"), approver("deny"), log)
check(r["exit"] == 0, "trust mode opens without asking")
r = hands.handle(job(page), Policy(mode="plan"), approver("once"), log)
check(r.get("refused"), "plan mode opens nothing")
r = hands.handle({"id": "j", "open": page}, Policy(mode="trust"), approver("once"), log)
check(r.get("refused"), "a job without the request it serves doesn't open")
print("hands open checks passed" if not failed else f"{failed} hands open check(s) failed")
sys.exit(1 if failed else 0)
