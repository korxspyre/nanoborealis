# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks the Hall's orchestrator end to end: the real Hall, two stand-in seats that answer on their
API the way nanobot serve does (and use the Hall's API the way their council tools would), and a
stand-in OpenRouter for escalation.

    python3 tests/orchestrator_check.py
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILED = False
CALLS: list[tuple[str, str, str]] = []  # (seat, session, prompt) for every turn a seat was asked
ADVICE: list[dict] = []  # every request the stand-in OpenRouter got


def check(ok: bool, what: str) -> None:
    global FAILED
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    FAILED |= not ok


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base: str, token: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


class Seat:
    """A stand-in seat: `a` is keen and capable, `b` is a strict reviewer who passes in the Hall."""

    def __init__(self, name: str, hall: str):
        self.name, self.hall, self.token, self.api_key = name, hall, "", ""
        self.port = free_port()

    def hall_call(self, method: str, path: str, body: dict | None = None) -> dict:
        return call(self.hall, self.token, method, path, body)[1]

    def turn(self, session: str, prompt: str) -> str:
        CALLS.append((self.name, session, prompt))
        if session.startswith("direct-"):
            return f"direct: {prompt}"
        if session.startswith("hall-"):
            return "I'd use SQLite." if self.name == "a" else "PASS"
        if match := re.search(r"Task (\d+) is open", prompt):
            if self.name != "a":
                return "PASS"
            task = int(match.group(1))
            self.hall_call("POST", f"/tasks/{task}/claim")
            self.hall_call("POST", f"/tasks/{task}/submit", {"result": "done by a"})
            return "claimed and handed in"
        if match := re.search(r"handed in task (\d+) \(([^)]*)\)", prompt):
            hard = "hard" in match.group(2)
            self.hall_call("POST", f"/tasks/{match.group(1)}/review",
                           {"verdict": "reject", "text": "not good enough"} if hard else {"verdict": "accept"})
            return "reviewed"
        if match := re.search(r"Your work on task (\d+) was sent back", prompt):
            self.hall_call("POST", f"/tasks/{match.group(1)}/submit", {"result": "tried again"})
            return "resubmitted"
        if match := re.search(r"\(vote (\d+)\).*\nOptions: (.*)\n", prompt):
            options = match.group(2).split(" / ")
            self.hall_call("POST", f"/votes/{match.group(1)}/ballot",
                           {"choice": options[0] if self.name == "a" else options[1], "reason": "because"})
            return "voted"
        return "PASS"

    def serve(self) -> None:
        seat = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.headers.get("Authorization") != f"Bearer {seat.api_key}":
                    self.send_response(401)
                    self.end_headers()
                    return
                text = seat.turn(body["session_id"], body["messages"][-1]["content"])
                out = json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()


def openrouter(port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_POST(self) -> None:
            ADVICE.append({**json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                           "_key": self.headers.get("Authorization")})
            out = json.dumps({"choices": [{"message": {"content": "Advice: go with the simpler one."}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever, daemon=True).start()


def until(what, seconds: float = 30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        found = what()
        if found:
            return found
        time.sleep(0.2)
    return None


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        port, advisor_port = free_port(), free_port()
        base = f"http://127.0.0.1:{port}"
        openrouter(advisor_port)
        env = {**os.environ, "NANOBOREALIS_HALL_STATE": tmp, "NANOBOREALIS_HALL_LISTEN": f"127.0.0.1:{port}",
               "NANOBOREALIS_HALL_OPENROUTER_URL": f"http://127.0.0.1:{advisor_port}/api/v1/chat/completions",
               "NANOBOREALIS_HALL_SEAT_TIMEOUT": "30"}
        hall = subprocess.Popen([sys.executable, str(ROOT / "system_files/usr/libexec/nanoborealis-hall")], env=env)
        try:
            def listening() -> bool:
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    return Path(tmp, "admin.token").exists()
                except OSError:
                    return False
            until(listening, 10)
            run(base, Path(tmp, "admin.token").read_text().strip())
        finally:
            hall.terminate()
            hall.wait(10)
    if FAILED:
        sys.exit(1)
    print("all orchestrator checks passed")


def run(base: str, admin_token: str) -> None:
    def admin(method: str, path: str, body: dict | None = None) -> dict:
        return call(base, admin_token, method, path, body)[1]

    a, b = Seat("a", base), Seat("b", base)
    for seat, capability in ((a, 70), (b, 40)):
        made = admin("POST", "/seats", {"id": seat.name, "name": seat.name.upper(), "model": f"m/{seat.name}",
                                        "capability": capability, "port": seat.port})
        seat.token, seat.api_key = made["token"], made.get("api_key", "")
        seat.serve()
    check(bool(a.api_key) and "api_key" not in admin("GET", "/seats")["seats"][0],
          "a seat with an API gets the Hall's key to it, shown once")
    kept = admin("POST", "/seats", {"id": "b", "name": "B", "model": "m/b", "capability": 40})
    check(kept["port"] == b.port, "updating a seat without its port keeps the port")
    admin("POST", "/seats", {"id": "chair", "name": "Chair", "model": "cloud", "chair": True})

    room = admin("POST", "/rooms", {"title": "Which database?"})["id"]
    admin("POST", f"/rooms/{room}/messages", {"text": "SQLite or Postgres?"})
    said = until(lambda: [m for m in admin("GET", f"/rooms/{room}/messages")["messages"] if m["author"] in ("a", "b")]
                 if len(admin("GET", f"/rooms/{room}/messages")["messages"]) >= 3 else None)
    check(said is not None and [(m["author"], m["kind"]) for m in said] == [("a", "say"), ("b", "pass")],
          "Hall chat: every seat answers in turn, weightiest first, and a seat can pass")
    check(not any(c[0] == "chair" for c in CALLS), "...the chair (no API) isn't asked through the Hall")
    first = next(c for c in CALLS if c[0] == "b" and c[1] == f"hall-{room}")
    check("I'd use SQLite." in first[2], "...and each seat sees what the seats before it said")

    direct = admin("POST", "/rooms", {"title": "Just b", "kind": "direct", "seat": "b"})["id"]
    before = len(CALLS)
    admin("POST", f"/rooms/{direct}/messages", {"text": "ping"})
    reply = until(lambda: next((m for m in admin("GET", f"/rooms/{direct}/messages")["messages"] if m["author"] == "b"), None))
    check(reply is not None and reply["text"] == "direct: ping", "one-on-one: only that seat answers, in its own session")
    check(all(c[0] == "b" and c[1] == f"direct-{direct}" for c in CALLS[before:]), "...and no other seat is asked")

    trust_before = admin("GET", "/seats")["seats"]
    task = admin("POST", "/tasks", {"title": "Write the README"})["id"]
    done = until(lambda: admin("GET", f"/tasks/{task}") if admin("GET", f"/tasks/{task}")["state"] == "done" else None)
    check(done is not None and done["claimed_by"] == "a" and [n["author"] for n in done["notes"]][-1] == "b",
          "a task is offered, claimed and done by one seat, and reviewed by another")
    trust_a = lambda seats: next(s["trust"] for s in seats if s["id"] == "a")  # noqa: E731
    check(trust_a(admin("GET", "/seats")["seats"]) > trust_a(trust_before), "...and the accepted work earns trust")

    call(base, admin_token, "POST", "/escalation-key", {"key": "sk-or-v1-" + "0" * 40})
    hard = admin("POST", "/tasks", {"title": "A hard one"})["id"]
    failed = until(lambda: admin("GET", f"/tasks/{hard}") if any(n["kind"] == "advice" for n in
                                                                  admin("GET", f"/tasks/{hard}")["notes"]) else None)
    check(failed is not None and failed["state"] == "failed" and
          [n["kind"] for n in failed["notes"]].count("submit") == 2,
          "rejected work goes back to its seat, and fails after two rejections")
    check(failed is not None and failed["notes"][-1]["author"] == "advisor", "...then the advisor's advice is added")

    admin("POST", "/seats", {"id": "b", "name": "B", "model": "m/b", "capability": 70})
    vote = admin("POST", "/votes", {"question": "Tabs or spaces?", "options": ["tabs", "spaces"], "room": room})["id"]
    closed = until(lambda: admin("GET", f"/votes/{vote}") if admin("GET", f"/votes/{vote}")["closed"] else None)
    check(closed is not None and len(closed["ballots"]) == 2, "a vote: the seats are asked for their ballots")
    check(closed is not None and closed["split"], "...evenly matched seats split it")
    advice = until(lambda: next((m for m in admin("GET", f"/rooms/{room}/messages")["messages"]
                                 if m["author"] == "advisor"), None))
    check(advice is not None and "simpler" in advice["text"], "a split vote goes to the advisor, whose answer is posted")
    check(bool(ADVICE) and all(r["model"].endswith(":free") and r["provider"]["max_price"]["prompt"] == 0
                               and r["_key"] == "Bearer sk-or-v1-" + "0" * 40 for r in ADVICE),
          "escalation asks a free model only, with a price limit of 0, using the saved key")

    admin("POST", "/settings", {"escalation": False})
    asked = len(ADVICE)
    admin("POST", f"/rooms/{room}/messages", {"kind": "escalation", "text": "Anyone?"})
    note = until(lambda: next((m for m in admin("GET", f"/rooms/{room}/messages")["messages"][::-1]
                               if m["author"] == "advisor"), None) if len(ADVICE) == asked else None)
    time.sleep(1)
    check(len(ADVICE) == asked and note is not None, "with escalation off, nothing is sent to OpenRouter")


if __name__ == "__main__":
    main()
