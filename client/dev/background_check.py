# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The Background pane: when a background subagent has reported, read from a chat's history as nanobot keeps it
(rows copied from a real chat on 2026-11-09)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import panels  # noqa: E402

failed = 0


def check(ok: bool, what: str) -> None:
    global failed
    print(("ok   " if ok else "FAIL ") + what)
    failed += not ok


spawned = [
    {"role": "user", "content": "Use the spawn tool..."},
    {"role": "assistant", "content": ""},
    {"role": "tool", "kind": "trace", "toolEvents": [{"name": "spawn", "result": "Subagent [pong reply] started (id: ed7ede41). "
                                                                                  "I'll notify you when it completes."}]},
    {"role": "assistant", "content": "Started. The subagent will reply with \"pong\" when it completes."},
]
check(not panels.subagent_reported(spawned, "ed7ede41"), "just started: the acknowledgement isn't the report")
reported = spawned + [{"role": "assistant", "content": "Done — the subagent replied with \"pong\"."}]
check(panels.subagent_reported(reported, "ed7ede41"), "the agent speaking again afterwards is the report")
moved_on = spawned + [{"role": "user", "content": "something else"}, {"role": "assistant", "content": "ok"}]
check(not panels.subagent_reported(moved_on, "ed7ede41"), "a reply to your next message isn't the report")
check(not panels.subagent_reported(reported, "other"), "another subagent's id isn't this one")
print("background checks passed" if not failed else f"{failed} background check(s) failed")
sys.exit(1 if failed else 0)
