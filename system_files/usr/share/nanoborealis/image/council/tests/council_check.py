# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks the council's tools inside nanobot: nanobot's own loader finds them through the
`nanobot.tools` entry points, they're off in an agent that isn't a seat, and in a seat they work
against the real Hall (nanoborealis-hall on a spare port).

Needs nanobot and this package installed in the running Python (pip install nanobot-ai==<version>
the council package):

    python3 system_files/usr/share/nanoborealis/image/council/tests/council_check.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[7]
FAILED = False
TOOLS = {"hall_read", "hall_post", "task_list", "task_claim", "task_update", "task_submit", "task_review", "vote",
         "escalate"}


def check(ok: bool, what: str) -> None:
    global FAILED
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    FAILED |= not ok


def admin_call(base: str, token: str, method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def discovered() -> dict:
    from nanobot.agent.tools.loader import ToolLoader
    return ToolLoader()._discover_plugins()


async def as_seat(tools: dict, base: str, admin: str) -> None:
    def tool(name: str):
        return tools[name]()

    call = lambda method, path, body=None: admin_call(base, admin, method, path, body)  # noqa: E731
    room = call("POST", "/rooms", {"title": "Naming the app"})["id"]
    call("POST", f"/rooms/{room}/messages", {"text": "What should the Hall's icon be?"})
    task = call("POST", "/tasks", {"title": "Draft the council README", "detail": "Two paragraphs."})["id"]

    overview = await tool("hall_read").execute()
    check("You are Qwen" in overview and "Naming the app" in overview and f"task {task} [open]" in overview,
          "hall_read: who I am, the rooms, and open tasks")
    check("What should the Hall's icon be?" in await tool("hall_read").execute(room=room),
          "hall_read: a room's messages, with nametapes")
    check("posted" in await tool("hall_post").execute(room=room, text="A lantern."), "hall_post: speaking in a room")
    await tool("hall_post").execute(room=room, **{"pass": True})
    said = call("GET", f"/rooms/{room}/messages")["messages"]
    check([(m["author"], m["kind"]) for m in said][-2:] == [("qwen", "say"), ("qwen", "pass")],
          "hall_post: said and passed under my own nametape")

    check("claimed" in await tool("task_claim").execute(task=task), "task_claim")
    await tool("task_update").execute(task=task, text="outlined")
    check("waiting for review" in await tool("task_submit").execute(task=task, result="The Hall is where seats meet."),
          "task_submit")
    own = await tool("task_review").execute(task=task, verdict="accept")
    check(getattr(own, "is_error", False) and "own work" in own, "task_review: my own work is refused, as an error")
    check("review" in await tool("task_list").execute(state="review"), "task_list: by state")
    check("outlined" in await tool("task_list").execute(task=task), "task_list: one task in full")

    opened = await tool("vote").execute(question="Lantern or star?", options=["lantern", "star"], room=room)
    vote_id = int(opened.split("vote ")[1].split(" ")[0])
    check("waiting for your ballot" in opened, "vote: asking the council")
    check("waiting for your ballot" in await tool("hall_read").execute(), "hall_read: votes waiting for me")
    cast = await tool("vote").execute(vote=vote_id, choice="lantern", reason="warm")
    check("closed" in cast and "leading: lantern" in cast, "vote: my ballot, and it closes when all have voted")

    check("asked for advice" in await tool("escalate").execute(room=room, question="Is a lantern too old-fashioned?"),
          "escalate: the question goes to the room for the escalation model")
    call("POST", "/settings", {"escalation": False})
    off = await tool("escalate").execute(room=room, question="again?")
    check(getattr(off, "is_error", False), "escalate: refused when escalation is off")


def main() -> None:
    os.environ.pop("NANOBOREALIS_HALL_URL", None)
    os.environ.pop("NANOBOREALIS_HALL_TOKEN", None)
    tools = discovered()
    check(TOOLS <= set(tools), f"nanobot finds the council's tools ({', '.join(sorted(set(tools) & TOOLS))})")
    check(not any(tools[n].enabled(None) for n in TOOLS), "...and leaves them off in an agent that isn't a seat")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ, "NANOBOREALIS_HALL_STATE": tmp, "NANOBOREALIS_HALL_LISTEN": f"127.0.0.1:{port}"}
        hall = subprocess.Popen([sys.executable, str(ROOT / "system_files/usr/libexec/nanoborealis-hall")], env=env)
        try:
            for _ in range(50):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
            admin = (Path(tmp) / "admin.token").read_text().strip()
            seat = admin_call(base, admin, "POST", "/seats", {"id": "qwen", "name": "Qwen", "model": "q", "capability": 60})
            os.environ.update(NANOBOREALIS_HALL_URL=base, NANOBOREALIS_HALL_TOKEN=seat["token"])
            check(all(tools[n].enabled(None) for n in TOOLS), "in a seat, the tools are on")
            asyncio.run(as_seat(tools, base, admin))
            os.environ["NANOBOREALIS_HALL_TOKEN"] = "wrong"
            refused = asyncio.run(tools["hall_read"]().execute())
            check(getattr(refused, "is_error", False) and "Unknown token" in refused,
                  "a wrong token comes back as a tool error, not a crash")
        finally:
            hall.terminate()
            hall.wait(10)
    if FAILED:
        sys.exit(1)
    print("all council tool checks passed")


if __name__ == "__main__":
    main()
