# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks the Hall (nanoborealis-hall) end to end: the real service on a spare port, driven as the
computer's own services (the admin token) and as two seats.

    python3 tests/hall_check.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILED = False


def check(ok: bool, what: str) -> None:
    global FAILED
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    FAILED |= not ok


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, base: str, token: str):
        self.base, self.token = base, token

    def __call__(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Authorization": f"Bearer {self.token}",
                                                  "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ, "NANOBOREALIS_HALL_STATE": tmp, "NANOBOREALIS_HALL_LISTEN": f"127.0.0.1:{port}"}
        service = subprocess.Popen([sys.executable, str(ROOT / "system_files/usr/libexec/nanoborealis-hall")], env=env)
        try:
            for _ in range(50):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
            run(base, (Path(tmp) / "admin.token").read_text().strip())
            check(oct((Path(tmp) / "admin.token").stat().st_mode & 0o777) == "0o600",
                  "the admin token is readable by its owner only")
        finally:
            service.terminate()
            service.wait(10)
    if FAILED:
        sys.exit(1)
    print("all Hall checks passed")


def run(base: str, admin_token: str) -> None:
    admin = Client(base, admin_token)
    check(Client(base, "nonsense")("GET", "/me")[0] == 401, "an unknown token gets nothing")
    check(admin("GET", "/me") == (200, {"admin": True}), "the admin token is the computer's own services")

    status, big = admin("POST", "/seats", {"id": "qwen-14b", "name": "Qwen", "model": "mlx-community/Qwen3-14B-4bit",
                                           "family": "qwen", "capability": 60})
    check(status == 200 and big.get("token") and big["trust"] == 1.0, "a seat is created, with its own token")
    _, small = admin("POST", "/seats", {"id": "gemma-12b", "name": "Gemma", "model": "mlx-community/gemma-3-12b-it-4bit",
                                        "family": "gemma", "capability": 30})
    check("token" not in admin("POST", "/seats", {"id": "gemma-12b", "name": "Gemma", "model": "m", "capability": 30})[1],
          "updating a seat doesn't hand out its token again")
    check(admin("POST", "/seats", {"id": "Bad Id", "name": "x", "model": "m"})[0] == 400, "a seat's id is checked")
    qwen, gemma = Client(base, big["token"]), Client(base, small["token"])
    check(qwen("GET", "/me")[1]["id"] == "qwen-14b", "a seat knows who it is")
    check(qwen("POST", "/seats", {"id": "evil", "name": "x", "model": "m"})[0] == 403, "a seat can't create seats")
    check(qwen("POST", "/settings", {"autonomy": "continuous"})[0] == 403, "a seat can't change the settings")

    settings = admin("GET", "/settings")[1]
    check(settings["autonomy"] == "on-task" and settings["escalation"] is True, "defaults: on tasks, escalation on")
    check(admin("POST", "/settings", {"autonomy": "continuous"})[1]["autonomy"] == "continuous",
          "autonomy can be continuous")
    check(admin("POST", "/settings", {"escalation_model": "vendor/some-paid-model"})[0] == 400,
          "the escalation model must be a free one")

    _, room = admin("POST", "/rooms", {"title": "Which database?"})
    admin("POST", f"/rooms/{room['id']}/messages", {"text": "SQLite or Postgres for the task board?"})
    qwen("POST", f"/rooms/{room['id']}/messages", {"text": "SQLite: one computer, one writer."})
    gemma("POST", f"/rooms/{room['id']}/messages", {"kind": "pass"})
    messages = admin("GET", f"/rooms/{room['id']}/messages")[1]["messages"]
    check([m["author"] for m in messages] == ["you", "qwen-14b", "gemma-12b"], "messages carry their nametapes")
    check(qwen("POST", f"/rooms/{room['id']}/messages", {"kind": "system", "text": "I win"})[0] == 403,
          "a seat can't post as the Hall")
    check(len(admin("GET", f"/rooms/{room['id']}/messages?after={messages[1]['id']}")[1]["messages"]) == 1,
          "a seat can read only what's new")

    _, vote = admin("POST", "/votes", {"question": "Which?", "options": ["SQLite", "Postgres"], "room": room["id"]})
    qwen("POST", f"/votes/{vote['id']}/ballot", {"choice": "SQLite", "reason": "simpler"})
    check(admin("POST", f"/votes/{vote['id']}/ballot", {"choice": "SQLite"})[0] == 403, "people don't vote as seats")
    check(gemma("POST", f"/votes/{vote['id']}/ballot", {"choice": "MySQL"})[0] == 400, "a ballot must be an option")
    _, done = gemma("POST", f"/votes/{vote['id']}/ballot", {"choice": "Postgres"})
    check(done["closed"] and done["leading"] == "SQLite", "the vote closes when every seat has voted; weight decides")
    check(abs(done["margin"] - (0.6 - 0.3) / 0.9) < 0.001 and not done["split"], "the margin is weighted")
    closing = admin("GET", f"/rooms/{room['id']}/messages")[1]["messages"][-1]
    check(closing["kind"] == "system" and "SQLite" in closing["text"], "the outcome is posted in the room")

    _, even = admin("POST", "/votes", {"question": "Tabs?", "options": ["yes", "no"]})
    admin("POST", "/seats", {"id": "gemma-12b", "name": "Gemma", "model": "m", "capability": 55})
    qwen("POST", f"/votes/{even['id']}/ballot", {"choice": "yes"})
    check(gemma("POST", f"/votes/{even['id']}/ballot", {"choice": "no"})[1]["split"], "a near tie is split")

    _, task = admin("POST", "/tasks", {"title": "Write the README", "detail": "Explain the Hall."})
    check(task["state"] == "open" and task["author"] == "you", "you post a task; it's open")
    check(admin("POST", f"/tasks/{task['id']}/claim")[0] == 403, "people don't claim tasks")
    check(qwen("POST", f"/tasks/{task['id']}/claim")[1]["state"] == "claimed", "a seat claims it")
    check(gemma("POST", f"/tasks/{task['id']}/claim")[0] == 409, "...and nobody else can")
    check(gemma("POST", f"/tasks/{task['id']}/submit", {"result": "x"})[0] == 409, "only the worker hands it in")
    qwen("POST", f"/tasks/{task['id']}/update", {"text": "outlined it"})
    check(qwen("POST", f"/tasks/{task['id']}/submit", {"result": "# The Hall"})[1]["state"] == "review",
          "handing it in puts it up for review")
    check(qwen("POST", f"/tasks/{task['id']}/review", {"verdict": "accept"})[0] == 403, "no seat reviews its own work")
    check(gemma("POST", f"/tasks/{task['id']}/review", {"verdict": "reject"})[0] == 400, "a rejection needs a reason")
    rejected = gemma("POST", f"/tasks/{task['id']}/review", {"verdict": "reject", "text": "too short"})[1]
    check(rejected["state"] == "claimed" and rejected["rejects"] == 1, "a rejection sends it back to its worker")
    check(qwen("GET", "/me")[1]["trust"] < 1.0, "...and costs the worker some trust")
    qwen("POST", f"/tasks/{task['id']}/submit", {"result": "# The Hall\nWhere seats meet."})
    accepted = admin("POST", f"/tasks/{task['id']}/review", {"verdict": "accept"})[1]
    check(accepted["state"] == "done" and [n["kind"] for n in accepted["notes"]] ==
          ["claim", "progress", "submit", "reject", "submit", "accept"], "you accept it; its history is kept")

    _, hard = admin("POST", "/tasks", {"title": "Prove P != NP"})
    gemma("POST", f"/tasks/{hard['id']}/claim")
    for _ in range(2):
        gemma("POST", f"/tasks/{hard['id']}/submit", {"result": "trust me"})
        last = qwen("POST", f"/tasks/{hard['id']}/review", {"verdict": "reject", "text": "no proof"})[1]
    check(last["state"] == "failed", "two rejections fail a task, so it can go to escalation")
    check(admin("POST", f"/tasks/{hard['id']}/reopen")[1]["state"] == "open", "you can open a failed task again")
    check([t["id"] for t in admin("GET", "/tasks?state=open")[1]["tasks"]] == [hard["id"]], "tasks filter by state")

    before = admin("GET", "/seats")[1]["seats"]
    admin("POST", "/feedback", {"seat": "gemma-12b", "kind": "thumbs_up", "reason": "good answer"})
    after = {s["id"]: s for s in admin("GET", "/seats")[1]["seats"]}
    check(after["gemma-12b"]["trust"] > next(s for s in before if s["id"] == "gemma-12b")["trust"],
          "your thumbs up raises a seat's trust, and with it its weight")
    admin("DELETE", "/seats/gemma-12b")
    check(gemma("GET", "/me")[0] == 401, "a removed seat's token stops working")


if __name__ == "__main__":
    main()
