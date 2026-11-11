# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks the colony's orchestrator end to end: the real Colony, two stand-in seats that answer on their
API the way nanobot serve does (and use the colony's API the way their council tools would), and a
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
    """A stand-in seat: `a` is keen and capable, `b` is a strict reviewer who passes in the colony."""

    def __init__(self, name: str, colony: str):
        self.name, self.colony, self.token, self.api_key = name, colony, "", ""
        self.port = free_port()

    def colony_call(self, method: str, path: str, body: dict | None = None) -> dict:
        return call(self.colony, self.token, method, path, body)[1]

    def turn(self, session: str, prompt: str) -> str:
        CALLS.append((self.name, session, prompt))
        if session.startswith("direct-"):
            return f"direct: {prompt}"
        if session.startswith("colony-"):
            return "I'd use SQLite." if self.name == "a" else "PASS"
        if match := re.search(r"Task (\d+) is open", prompt):
            if self.name != "a":
                return "PASS"
            task = int(match.group(1))
            self.colony_call("POST", f"/tasks/{task}/claim")
            self.colony_call("POST", f"/tasks/{task}/submit", {"result": "done by a"})
            return "claimed and handed in"
        if match := re.search(r"handed in task (\d+) \(([^)]*)\)", prompt):
            hard = "hard" in match.group(2)
            self.colony_call("POST", f"/tasks/{match.group(1)}/review",
                           {"verdict": "reject", "text": "not good enough"} if hard else {"verdict": "accept"})
            return "reviewed"
        if match := re.search(r"Your work on task (\d+) was sent back", prompt):
            self.colony_call("POST", f"/tasks/{match.group(1)}/submit", {"result": "tried again"})
            return "resubmitted"
        if "Mail from" in prompt:
            return f"{self.name} read the mail"
        if match := re.search(r"\(vote (\d+)\).*\nOptions: (.*)\n", prompt):
            options = match.group(2).split(" / ")
            self.colony_call("POST", f"/votes/{match.group(1)}/ballot",
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
            body = ADVICE[-1]
            text = "\n".join(str(m.get("content")) for m in body["messages"])
            echoed = re.findall(r"<<[A-Za-z_]+_[0-9a-f]{10,16}>>", text)[:1]  # a model that repeats a name it was given
            out = json.dumps({"choices": [{"message": {"content": "Advice: go with the simpler one."
                                                                  + (f" About {echoed[0]}." if echoed else "")}}]}).encode()
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
        env = {**os.environ, "NANOBOREALIS_COLONY_STATE": tmp, "NANOBOREALIS_COLONY_LISTEN": f"127.0.0.1:{port}",
               "NANOBOREALIS_COLONY_OPENROUTER_URL": f"http://127.0.0.1:{advisor_port}/api/v1/chat/completions",
               "NANOBOREALIS_COLONY_SEAT_TIMEOUT": "30",
               "NANOBOREALIS_TOOLS": str(ROOT / "system_files/usr/share/nanoborealis")}
        colony = subprocess.Popen([sys.executable, str(ROOT / "system_files/usr/libexec/nanoborealis-colony")], env=env)
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
            colony.terminate()
            colony.wait(10)
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
          "a seat with an API gets the colony's key to it, shown once")
    kept = admin("POST", "/seats", {"id": "b", "name": "B", "model": "m/b", "capability": 40})
    check(kept["port"] == b.port, "updating a seat without its port keeps the port")
    admin("POST", "/seats", {"id": "chair", "name": "Chair", "model": "cloud", "chair": True})

    room = admin("POST", "/rooms", {"title": "Which database?"})["id"]
    admin("POST", f"/rooms/{room}/messages", {"text": "SQLite or Postgres?"})
    said = until(lambda: [m for m in admin("GET", f"/rooms/{room}/messages")["messages"] if m["author"] in ("a", "b")]
                 if len(admin("GET", f"/rooms/{room}/messages")["messages"]) >= 3 else None)
    check(said is not None and [(m["author"], m["kind"]) for m in said] == [("a", "say"), ("b", "pass")],
          "Colony chat: every seat answers in turn, weightiest first, and a seat can pass")
    check(not any(c[0] == "chair" for c in CALLS), "...the chair (no API) isn't asked through the colony")
    first = next(c for c in CALLS if c[0] == "b" and c[1] == f"colony-{room}")
    check("I'd use SQLite." in first[2], "...and each seat sees what the seats before it said")

    direct = admin("POST", "/rooms", {"title": "Just b", "kind": "direct", "seat": "b"})["id"]
    before = len(CALLS)
    admin("POST", f"/rooms/{direct}/messages", {"text": "ping"})
    reply = until(lambda: next((m for m in admin("GET", f"/rooms/{direct}/messages")["messages"] if m["author"] == "b"), None))
    check(reply is not None and reply["text"].startswith("direct: ") and reply["text"].endswith("ping"), "one-on-one: only that seat answers, in its own session")
    check(all(c[0] == "b" and c[1] == f"direct-{direct}" for c in CALLS[before:]), "...and no other seat is asked")

    trust_before = admin("GET", "/seats")["seats"]
    task = admin("POST", "/tasks", {"title": "Write the README"})["id"]
    done = until(lambda: admin("GET", f"/tasks/{task}") if admin("GET", f"/tasks/{task}")["state"] == "done" else None)
    check(done is not None and done["claimed_by"] == "a" and [n["author"] for n in done["notes"]][-1] == "b",
          "a task is offered, claimed and done by one seat, and reviewed by another")
    trust_a = lambda seats: next(s["trust"] for s in seats if s["id"] == "a")  # noqa: E731
    check(trust_a(admin("GET", "/seats")["seats"]) == trust_a(trust_before)
          and next(s for s in admin("GET", "/seats")["seats"] if s["id"] == "a")["record"].get("praised", 0) >= 1,
          "...and the accepted work goes on its record, while trust stays what its benchmarks say")

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

    # Mixed escalation: several free models of different makes, tokenized.
    secret = "alice.smith@example.com"
    admin("POST", "/settings", {"escalation": True, "escalation_mix": True})
    asked = len(ADVICE)
    admin("POST", f"/rooms/{room}/messages", {"kind": "escalation", "text": f"Should we mail {secret} the plan?"})
    posted = until(lambda: next((m for m in admin("GET", f"/rooms/{room}/messages")["messages"][::-1]
                                 if m["author"] == "advisor" and secret in m["text"]), None))
    models = {r["model"] for r in ADVICE[asked:]}
    # The free models are rate-limited and earlier escalations in this run have used some of the quota, so how many
    # answer varies; what matters is that it's several, of more than one make.
    check(len(models) >= 2 and len({m.split("/")[0] for m in models}) >= 2,
          f"escalation asks several free models of different makes, not one ({len(models)} here: {sorted(models)})")
    check(all(r["model"].endswith(":free") and r["provider"]["max_price"]["prompt"] == 0 for r in ADVICE[asked:]),
          "...all of them free, with a price limit of 0")
    check(not any(secret in json.dumps(r) for r in ADVICE[asked:]) and any("<<EMAIL_" in json.dumps(r) for r in ADVICE[asked:]),
          "...and none of them was sent the email address, only a placeholder for it")
    check(posted is not None and secret in posted["text"], "...while the advice posted to the colony has the real address back in it")
    reviews = [r for r in ADVICE[asked:] if r["messages"][0]["content"].startswith("You review")]
    check(bool(reviews) and "You advise" not in json.dumps(reviews[0]["messages"][1:]),
          "...a model of another make reviews the combined answer, seeing only the question and the answer")

    admin("POST", "/settings", {"escalation_mix": False})
    asked = len(ADVICE)
    admin("POST", f"/rooms/{room}/messages", {"kind": "escalation", "text": "Again?"})
    until(lambda: len(ADVICE) > asked)
    time.sleep(1)
    check(len(ADVICE) == asked + 1, "with escalation_mix off, one model is asked, as before")

    # Claude's outside seat: it joins over the API, sees the colony tokenized, and nothing waits for it.
    made = admin("POST", "/seats", {"id": "claude", "name": "Claude", "model": "an outside agent", "family": "outside",
                                    "capability": 50, "external": True})
    me = lambda method, path, body=None: call(base, made["token"], method, path, body)  # noqa: E731
    check(made["external"] and "api_key" not in made and me("GET", "/me")[1]["external"],
          "an outside seat is made with a token, and no API of its own")
    mail = "bob.jones@example.com"
    admin("POST", f"/rooms/{room}/messages", {"text": f"Please ask {mail} for the figures."})
    seen = [m for m in me("GET", f"/rooms/{room}/messages")[1]["messages"] if "figures" in m["text"]]
    holder = re.search(r"<<EMAIL_[0-9a-f]{10,16}>>", seen[0]["text"]) if seen else None
    check(seen and mail not in json.dumps(me("GET", f"/rooms/{room}/messages")[1]) and holder is not None,
          "the outside seat reads the colony with the email address swapped for a placeholder")
    me("POST", f"/rooms/{room}/messages", {"text": f"Agreed: write to {holder.group(0)} today."})
    spoke = next((m for m in admin("GET", f"/rooms/{room}/messages")["messages"] if m["author"] == "claude"), None)
    check(spoke is not None and mail in spoke["text"], "...and what it writes back with the placeholder reaches the council with the real address")
    check(me("GET", "/rooms")[1] and not any(mail in json.dumps(r) for r in me("GET", "/tasks")[1]["tasks"]),
          "...in every part of the colony, tasks included")

    vote = admin("POST", "/votes", {"question": "Ship it?", "options": ["yes", "no"]})["id"]
    until(lambda: admin("GET", f"/votes/{vote}")["closed"])
    check("claude" not in admin("GET", f"/votes/{vote}")["waiting_for"], "a vote doesn't wait for an outside seat")
    vote = admin("POST", "/votes", {"question": "Rebase or merge?", "options": ["rebase", "merge"]})["id"]
    status, _ = me("POST", f"/votes/{vote}/ballot", {"choice": "merge", "reason": "history"})
    check(status == 200 and any(b["seat"] == "claude" for b in admin("GET", f"/votes/{vote}")["ballots"]),
          "...but it can cast a ballot, which counts with its weight")

    work = admin("POST", "/tasks", {"title": "Check the figures", "detail": f"Email {mail}"})["id"]
    status, claimed = me("POST", f"/tasks/{work}/claim", {})
    if status == 200 and claimed.get("claimed_by") == "claude":
        status, handed = me("POST", f"/tasks/{work}/submit", {"result": "Figures checked."})
        check(status == 200 and handed["claimed_by"] == "claude", "an outside seat can claim a task and hand it in")
        until(lambda: admin("GET", f"/tasks/{work}")["state"] != "review")  # a pool seat reviews it
    else:  # a pool seat took it first: the board is shared
        check(True, "an outside seat can claim a task and hand it in (a pool seat took this one first)")
    check(me("DELETE", "/seats/a")[0] == 403, "an outside seat can't manage seats")
    # Benchmarks: the objective half of trust, and the scorecard in what the seats are told.
    trust_before = next(s["trust"] for s in admin("GET", "/seats")["seats"] if s["id"] == "a")
    run_a = {"model": "m/a", "suite": "nanobench-1", "seed": 3, "results": {
        "reasoning": {"passed": 14, "n": 15}, "tools": {"passed": 15, "n": 15}, "review": {"passed": 9, "n": 15}}}
    status, posted = call(base, admin_token, "POST", "/benchmarks", run_a)
    seat_a = next(s for s in admin("GET", "/seats")["seats"] if s["id"] == "a")
    check(status == 200 and seat_a["scorecard"]["overall"]["n"] == 45 and seat_a["scorecard"]["tools"]["score"] == 100.0
          and seat_a["scorecard"]["review"]["lo"] < seat_a["scorecard"]["review"]["score"] < seat_a["scorecard"]["review"]["hi"],
          "a benchmark run gives its seats a scorecard, each score with its 95% interval")
    check(seat_a["trust"] > trust_before and posted["trust_term"] > 0, "...a good result raises trust, from the low end of the interval")
    unbenched = next(s for s in admin("GET", "/seats")["seats"] if s["id"] == "b")
    check(unbenched["scorecard"] == {} and unbenched["trust"] <= 1.0 + 0.2, "...a seat nobody has benchmarked has no scorecard and no benchmark trust")
    trust_b = next(s["trust"] for s in admin("GET", "/seats")["seats"] if s["id"] == "b")
    status, _ = call(base, admin_token, "POST", "/benchmarks", {**run_a, "results": {"reasoning": {"passed": 2, "n": 5}}, "model": "m/b"})
    now_b = next(s for s in admin("GET", "/seats")["seats"] if s["id"] == "b")
    check(status == 200 and now_b["trust"] == trust_b and now_b["scorecard"]["overall"]["n"] == 5,
          "...a few items give a scorecard but move nothing yet")
    bad = call(base, admin_token, "POST", "/benchmarks", {**run_a, "results": {"reasoning": {"passed": 9, "n": 3}}})[0]
    check(bad == 400 and call(base, admin_token, "POST", "/benchmarks", {**run_a, "results": {"cooking": {"passed": 1, "n": 1}}})[0] == 400,
          "...impossible results and unknown areas are refused")
    check(call(base, a.token, "POST", "/benchmarks", run_a)[0] == 403, "...and a seat can't post its own benchmarks")
    low = {"model": "m/b", "suite": "nanobench-1", "seed": 4, "results": {"reasoning": {"passed": 8, "n": 30}}}
    call(base, admin_token, "POST", "/benchmarks", low)
    check(next(s for s in admin("GET", "/seats")["seats"] if s["id"] == "b")["trust"] < 1.0,
          "...a weak result over enough items lowers it")

    admin("POST", f"/rooms/{room}/messages", {"text": "Which of us should take the next build?"})
    until(lambda: any(c[0] == "b" and c[1] == f"colony-{room}" and "scorecard" in c[2] for c in CALLS[-6:]))
    told = next((c[2] for c in reversed(CALLS) if c[0] == "b" and c[1] == f"colony-{room}"), "")
    check("scorecard" in told and "- a: reasoning" in told and "tools 100" in told and "- b: reasoning" in told,
          "every seat is told the council's scorecard before it speaks, so it can weigh the others' words")
    spoken = [m for m in admin("GET", f"/rooms/{room}/messages")["messages"] if m["author"] == "a"]
    check(spoken and "benchmark" in spoken[-1]["card"] and "sure >=" in spoken[-1]["card"],
          "a seat's messages carry its benchmark, for anyone reading them")
    outside = [m for m in me("GET", f"/rooms/{room}/messages")[1]["messages"] if m["author"] == "a"]
    check(outside and "benchmark" in outside[-1]["card"], "...an outside seat sees them too")

    shown = subprocess.run([sys.executable, str(ROOT / "system_files/usr/share/nanoborealis/colonyseat.py"), "read", str(room)],
                           capture_output=True, text=True, env={**os.environ, "NANOBOREALIS_COLONY_URL": base,
                                                                "NANOBOREALIS_COLONY_TOKEN": made["token"]})
    check(shown.returncode == 0 and "<<EMAIL_" in shown.stdout and mail not in shown.stdout,
          "the colonyseat client reads the colony as an outside seat, tokenized")


if __name__ == "__main__":
    main()
