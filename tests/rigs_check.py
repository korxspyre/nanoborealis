# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks rigs (a team of seats as a file), the durable inbox with its delivery guard, the head agent, the
watchdog, and snapshot and restore: the pure logic in rigs.py, then the real Colony with stand-in seats.

    python3 tests/rigs_check.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "system_files/usr/share/nanoborealis"))
sys.path.insert(0, str(ROOT / "tests"))

import orchestrator_check as oc  # noqa: E402  (its stand-in seats and helpers)
import rigs  # noqa: E402

check, call, until, Seat, CALLS = oc.check, oc.call, oc.until, oc.Seat, oc.CALLS

SPEC = """
[rig]
name = "starter"
culture = "We make small, tested changes. We say when we are unsure."

[[pod]]
id = "dev"
guidance = "Builds and reviews the code."

[[seat]]
id = "build"
pod = "dev"
kind = "pool"
model = "m/rigbuild"
capability = 60
role = "builder"

[[seat]]
id = "claude"
pod = "dev"
kind = "outside"
head = 1
capability = 50

[[edge]]
from = "claude"
to = "build"
kind = "reviews"

[[edge]]
from = "build"
to = "claude"
kind = "reviews"
"""

# -- The pure logic ----------------------------------------------------------------------------------------

spec = rigs.parse(SPEC)
check(spec["name"] == "starter" and [s["address"] for s in spec["seats"]] == ["build@starter", "claude@starter"]
      and spec["seats"][1]["id"] == "starter-claude" and spec["edges"][0] == {"src": "starter-claude", "dst": "starter-build", "kind": "reviews"},
      "a spec gives each seat an id and an address, and its edges between them")
for broken, what in (('[rig]\nname = "Bad Name"', "a bad rig name"), (SPEC.replace('pod = "dev"', 'pod = "nope"', 1), "a seat in a pod that isn't there"),
                     (SPEC.replace('kind = "reviews"', 'kind = "loves"', 1), "an unknown edge kind"),
                     (SPEC.replace('model = "m/rigbuild"', ""), "a pool seat with no model"), ("not toml [", "text that isn't TOML")):
    try:
        rigs.parse(broken)
        ok = False
    except rigs.SpecError:
        ok = True
    check(ok, f"a spec with {what} is refused")
try:
    rigs.parse(SPEC.replace('id = "claude"', 'id = "build"'))
    ok = False
except rigs.SpecError as error:
    ok = "used once" in str(error)
check(ok, "a seat id used twice is refused")

plan = rigs.plan(spec, {"starter-build": {"rig": "starter", "model": "m/old", "capability": 60, "pod": "dev", "role": "builder", "policy": "inherit"},
                        "starter-old": {"rig": "starter", "address": "old@starter"}})
check(plan["create"] == ["claude@starter"] and plan["update"] == ["build@starter"] and plan["remove"] == ["old@starter"],
      "a plan says what would be made, changed and removed")
words = rigs.briefing({"name": "starter", "culture": "Be small."}, {"id": "dev", "guidance": "Build."},
                      {"rig": "starter", "address": "build@starter", "role": "builder", "guidance": ""})
check(words.index("Be small.") < words.index("Build.") < words.index("builder"), "a seat is told the culture first, then its pod, then its role")

seats = [{"id": "c", "head_rank": 1, "port": None, "weight": 0.5}, {"id": "p", "head_rank": 2, "port": 9, "weight": 0.4},
         {"id": "q", "head_rank": None, "port": 8, "weight": 0.9}, {"id": "chair", "chair": True, "weight": 0.1, "port": None}]
head = lambda beats, now=1000.0: rigs.pick_head(seats, beats, now)["head"]  # noqa: E731
check(head({"c": {"at": 990.0, "status": "ok"}}) == "c" and head({}) == "p" and head({"c": {"at": 990.0, "status": "limited"}}) == "p"
      and head({"c": {"at": 100.0, "status": "ok"}}) == "p", "the head is the first in line that is here and not worn out")
check(rigs.pick_head([seats[2], seats[3]], {}, 0)["head"] == "q" and rigs.pick_head([seats[3]], {}, 0)["head"] == "chair"
      and rigs.pick_head([], {}, 0)["head"] is None, "...else the strongest seat the colony can ask, else the chair")

again, dead = rigs.overdue_mail([{"state": "new", "created": 0, "tries": 1}, {"state": "new", "created": 0, "tries": 3},
                                 {"state": "new", "created": 99, "tries": 0}, {"state": "done", "created": 0, "tries": 0}], 100, 50, 3)
check(len(again) == 1 and len(dead) == 1, "the watchdog retries overdue mail and gives up on mail tried three times")
nudge, reopen = rigs.stale_tasks([{"state": "claimed", "last_word": 0, "nudges": 0}, {"state": "claimed", "last_word": 0, "nudges": 2},
                                  {"state": "claimed", "last_word": 99, "nudges": 0}, {"state": "review", "last_word": 0, "nudges": 0}], 100, 50, 2)
check(len(nudge) == 1 and len(reopen) == 1, "...nudges a quiet task, and reopens one that stayed quiet")
out = rigs.restore_outcomes({"seats": [{"id": "x", "address": "x@r", "model": "m1", "kind": "pool"}, {"id": "y", "address": "y@r", "model": "m2", "kind": "pool"},
                                       {"id": "z", "address": "z@r", "model": "outside", "kind": "outside"}]},
                            {"x": {"model": "m1"}}, {"m3"})
check([o["outcome"] for o in out] == ["resumed", "awaiting-decision", "fresh-primed"],
      "restore says honestly what came back: resumed, awaiting a decision, or made again")

# -- The colony --------------------------------------------------------------------------------------------------


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        port, advisor = oc.free_port(), oc.free_port()
        base = f"http://127.0.0.1:{port}"
        oc.openrouter(advisor)
        env = {**os.environ, "NANOBOREALIS_COLONY_STATE": tmp, "NANOBOREALIS_COLONY_LISTEN": f"127.0.0.1:{port}",
               "NANOBOREALIS_COLONY_OPENROUTER_URL": f"http://127.0.0.1:{advisor}/api/v1/chat/completions",
               "NANOBOREALIS_COLONY_SEAT_TIMEOUT": "30", "NANOBOREALIS_TOOLS": str(ROOT / "system_files/usr/share/nanoborealis"),
               "NANOBOREALIS_COLONY_WATCHDOG_EVERY": "1", "NANOBOREALIS_COLONY_MAIL_AFTER": "2", "NANOBOREALIS_COLONY_TASK_AFTER": "3"}
        colony = subprocess.Popen([sys.executable, str(ROOT / "system_files/usr/libexec/nanoborealis-colony")], env=env, stderr=subprocess.DEVNULL)
        try:
            def listening() -> bool:
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    return Path(tmp, "admin.token").exists()
                except OSError:
                    return False
            until(listening, 10)
            run(base, Path(tmp, "admin.token").read_text().strip(), tmp)
        finally:
            colony.terminate()
            colony.wait(10)
    if oc.FAILED:
        sys.exit(1)
    print("rigs checks passed")


def run(base: str, token: str, state: str) -> None:
    admin = lambda method, path, body=None: call(base, token, method, path, body)  # noqa: E731

    status, answer = admin("POST", "/rigs", {"spec": SPEC, "dry_run": True})
    check(status == 200 and answer["dry_run"] and sorted(answer["plan"]["create"]) == ["build@starter", "claude@starter"]
          and admin("GET", "/rigs")[1]["rigs"] == [], "`rig up --plan` says what it would do and does nothing")
    check(admin("POST", "/rigs", {"spec": '[rig]\nname = "X"'})[0] == 400, "a bad spec is refused with what's wrong")

    status, made = admin("POST", "/rigs", {"spec": SPEC})
    tokens = made["tokens"]
    check(status == 200 and set(tokens) == {"build@starter", "claude@starter"}, "`rig up` makes the seats and shows their tokens once")
    claude = lambda method, path, body=None: call(base, tokens["claude@starter"], method, path, body)  # noqa: E731
    build = Seat("rb", base)
    seat = admin("POST", "/seats", {"id": "starter-build", "name": "build@starter", "model": "m/rigbuild", "capability": 60,
                                    "port": build.port, "rotate": True})[1]
    build.token, build.api_key = seat["token"], seat["api_key"]
    build.serve()
    rig = admin("GET", "/rigs/starter")[1]
    by_id = {s["id"]: s for s in rig["seats"]}
    check(rig["culture"].startswith("We make small") and rig["pods"][0]["id"] == "dev" and len(rig["edges"]) == 2
          and by_id["starter-build"]["role"] == "builder" and by_id["starter-claude"]["head_rank"] == 1 and by_id["starter-claude"]["external"],
          "the rig shows its culture, pods, edges and each seat's place")
    check(admin("POST", "/rigs", {"spec": SPEC})[1]["tokens"] == {}, "applying the same spec again changes nothing and shows no tokens")

    # Mail, and the culture a seat is told.
    status, sent = admin("POST", "/inbox", {"to": "build@starter", "text": "Please write the README."})
    until(lambda: any(c[0] == "rb" and "Mail from you" in c[2] for c in CALLS))
    told = next((c[2] for c in CALLS if c[0] == "rb" and "Mail from you" in c[2]), "")
    check(status == 200 and told.index("We make small, tested changes") < told.index("Builds and reviews") < told.index("Your role: builder")
          < told.index("Mail from you"), "mail for a seat the colony can ask is delivered with its team's culture, pod and role first")
    reply = until(lambda: next((m for m in admin("GET", "/inbox")[1]["mail"] if m["kind"] == "reply"), None))
    done = admin("GET", "/inbox?seat=starter-build&state=done")[1]["mail"]
    check(reply is not None and reply["text"] == "rb read the mail" and reply["sender"] == "starter-build" and len(done) == 1,
          "...and its answer comes back to the sender's inbox, the mail marked done")

    # The delivery guard.
    admin("POST", "/rigs", {"spec": SPEC.replace('name = "starter"', 'name = "twin"')})
    status, clash = admin("POST", "/inbox", {"to": "build", "text": "which one?"})
    check(status == 409 and "build@starter" in clash["error"] and "build@twin" in clash["error"], "a name that fits two seats is refused, naming both")
    check(admin("POST", "/inbox", {"to": "nobody", "text": "x"})[0] == 404 and admin("POST", "/inbox", {"to": "build@twin", "text": "x"})[0] == 200,
          "...an unknown name is refused, a full address is delivered")
    admin("DELETE", "/rigs/twin", {"snapshot": False})

    # An outside seat takes its mail from its inbox, tokenized.
    mail = "bob.jones@example.com"
    admin("POST", "/inbox", {"to": "claude@starter", "text": f"Ask {mail} about the figures."})
    box = claude("GET", "/inbox")[1]["mail"]
    check(len(box) == 1 and mail not in json.dumps(box) and "<<EMAIL_" in box[0]["text"] and box[0]["state"] == "new",
          "an outside seat's mail waits in its inbox, with personal details swapped for placeholders")
    check(claude("POST", f"/inbox/{box[0]['id']}/ack", {"state": "done"})[1]["state"] == "done" and claude("GET", "/inbox")[1]["mail"] == [],
          "...and is acknowledged as done")
    other = admin("POST", "/inbox", {"to": "starter-build", "text": "private"})[1]["sent"][0]
    check(claude("POST", f"/inbox/{other}/ack", {"state": "done"})[0] == 403, "...but it can't acknowledge another seat's mail")
    sent = admin("POST", "/inbox", {"to": "rig:starter", "text": "Standup in five."})[1]
    check(len(sent["sent"]) == 2 and len(admin("POST", "/inbox", {"to": "pod:starter/dev", "text": "Pod note."})[1]["sent"]) == 2,
          "mail to a rig or a pod reaches each of its seats")

    # The head agent.
    head = admin("GET", "/head")[1]
    check(head["head"] != "starter-claude" and "steps in" in head["reason"] and head["line"][0]["state"] == "away",
          "the head agent is the next-strongest seat while the first in line isn't here")
    check(claude("POST", "/presence", {"status": "ok"})[1]["head"] == "starter-claude", "...and the first in line once it says it is here")
    check(claude("POST", "/presence", {"status": "limited"})[1]["head"] != "starter-claude" and admin("POST", "/presence", {"status": "ok"})[0] == 403,
          "...it steps aside when its usage is spent, and only a seat can send its own heartbeat")
    claude("POST", "/presence", {"status": "ok"})

    # Edges say who reviews whom.
    def own_task(caller) -> int:
        for _ in range(8):  # the other seats are offered every new task too: take one before they do
            task = admin("POST", "/tasks", {"title": "A rig task"})[1]["id"]
            if caller("POST", f"/tasks/{task}/claim")[0] == 200:
                return task
        raise SystemExit("couldn't claim a task")

    by_build = lambda method, path, body=None: call(base, build.token, method, path, body)  # noqa: E731
    task = own_task(by_build)
    by_build("POST", f"/tasks/{task}/submit", {"result": "built"})
    asked = until(lambda: next((m for m in claude("GET", "/inbox")[1]["mail"] if "Please review" in m["text"] and f"task {task}" in m["text"]), None))
    check(asked is not None and not any(f"handed in task {task}" in c[2] for c in CALLS),
          "a named reviewer the colony can't ask (Claude's seat) is sent the review as mail")
    check(claude("POST", f"/tasks/{task}/review", {"verdict": "accept"})[1]["state"] == "done", "...and its review closes the task")
    task = own_task(claude)
    claude("POST", f"/tasks/{task}/submit", {"result": "claude's work"})
    until(lambda: any(c[0] == "rb" and f"handed in task {task}" in c[2] for c in CALLS))
    check(until(lambda: admin("GET", f"/tasks/{task}")[1]["state"] == "done") and admin("GET", f"/tasks/{task}")[1]["notes"][-1]["author"] == "starter-build",
          "a named reviewer the colony can ask (a pool seat) is asked, and reviews it")

    # The watchdog.
    quiet = own_task(claude)
    nudged = until(lambda: next((m for m in claude("GET", "/inbox")[1]["mail"] if m["kind"] == "nudge"), None), 20)
    check(nudged is not None and f"Task {quiet}" in nudged["text"], "a task held and quiet is nudged")
    reopened = until(lambda: admin("GET", f"/tasks/{quiet}")[1]["claimed_by"] != "starter-claude", 30)
    kinds = [n["kind"] for n in admin("GET", f"/tasks/{quiet}")[1]["notes"]]
    check(reopened and kinds.count("nudge") == 2 and "reopen" in kinds, "...twice, and then put back on the board for someone else")
    claude("POST", "/presence", {"status": "ok"})
    admin("POST", "/seats", {"id": "ghost", "name": "Ghost", "model": "m/ghost", "capability": 10, "port": oc.free_port()})
    ghost = admin("POST", "/inbox", {"to": "ghost", "text": "anyone home?"})[1]["sent"][0]
    check(until(lambda: next((m for m in admin("GET", "/inbox?seat=ghost&state=dead")[1]["mail"] if m["id"] == ghost), None), 30) is not None
          and until(lambda: any("never taken" in m["text"] for m in claude("GET", "/inbox")[1]["mail"]), 10),
          "mail a seat never takes is given up on, and the head is told")
    admin("DELETE", "/seats/ghost")

    # The command line.
    Path(state, "starter.toml").write_text(SPEC.replace('name = "starter"', 'name = "cli"'), encoding="utf-8")
    cli_env = {**os.environ, "NANOBOREALIS_COLONY_URL": base, "NANOBOREALIS_COLONY_ADMIN_TOKEN_FILE": str(Path(state, "admin.token")),
               "NANOBOREALIS_SEATS_DIR": str(Path(state, "seats"))}

    def rigctl(*args: str) -> str:
        done = subprocess.run([sys.executable, str(ROOT / "system_files/usr/share/nanoborealis/rigctl.py"), *args], capture_output=True,
                              text=True, env=cli_env)
        return done.stdout + done.stderr

    shown = rigctl("up", str(Path(state, "starter.toml")), "--plan")
    check("Plan for cli" in shown and "claude@cli" in shown and admin("GET", "/rigs/cli")[0] == 404, "`nanoborealis rig up --plan` shows the plan and makes nothing")
    shown = rigctl("up", str(Path(state, "starter.toml")))
    kept = list(Path(state, "seats").glob("*.token"))
    check("Rig cli" in shown and len(kept) == 2 and all(t.read_text().strip() not in shown for t in kept),
          "`rig up` makes it and keeps the new tokens in files, never printing them")
    shown = rigctl("ps", "cli")
    check("build@cli" in shown and "head #1" in shown and "claude@cli" in shown and "reviews" in shown, "`rig ps NAME` shows the seats, who leads and the edges")
    check("sent to starter-claude" in rigctl("send", "claude@starter", "hello") and "hello" in rigctl("inbox", "starter-claude")
          and "Head agent:" in rigctl("head") and "cli: 2 seat(s)" in rigctl("ps"), "`rig send`, `inbox`, `head` and `ps` work")
    check("is down" in rigctl("down", "cli") and "restored" not in rigctl("ps").lower() and "cli:" not in rigctl("ps"), "`rig down` takes it down")
    shown = rigctl("restore", "cli")
    check("Restored cli" in shown and shown.count("fresh-primed") == 2, "`rig restore` says what came back, seat by seat")
    admin("DELETE", "/rigs/cli", {"snapshot": False})

    # Snapshot and restore.
    snap = admin("POST", "/rigs/starter/snapshot", {"label": "before the weekend"})[1]
    check(snap["seats"] == 2 and "id" in snap, "a snapshot captures the rig")
    admin("POST", "/inbox", {"to": "claude@starter", "text": "work in flight"})
    down = admin("DELETE", "/rigs/starter", {})[1]
    check(down["snapshot"] and admin("GET", "/rigs/starter")[0] == 404 and not [s for s in admin("GET", "/seats")[1]["seats"] if s.get("rig") == "starter"],
          "`rig down` takes a snapshot, then removes the rig's seats")
    status, back = admin("POST", "/rigs/starter/restore", {"models": ["m/rigbuild"]})
    outcomes = {o["seat"]: o["outcome"] for o in back["outcomes"]}
    check(status == 200 and outcomes == {"build@starter": "fresh-primed", "claude@starter": "fresh-primed"} and "claude@starter" in back["tokens"],
          "`rig restore` makes the seats again, saying so honestly, with a new token for the outside seat")
    again = call(base, back["tokens"]["claude@starter"], "GET", "/inbox")[1]["mail"]
    check(any(m["text"] == "work in flight" for m in again) and claude("GET", "/me")[0] == 401,
          "...the work in flight is waiting in its inbox, and its old token no longer works")
    check({o["outcome"] for o in admin("POST", "/rigs/starter/restore", {"models": ["m/rigbuild"]})[1]["outcomes"]} == {"resumed"},
          "restoring seats that are all there again resumes them")
    admin("DELETE", "/rigs/starter", {})
    waiting = admin("POST", "/rigs/starter/restore", {"models": []})[1]
    check({o["seat"]: o["outcome"] for o in waiting["outcomes"]}["build@starter"] == "awaiting-decision"
          and "build@starter" not in waiting["tokens"], "a seat whose model isn't in the pool is not guessed at: it awaits your decision")


if __name__ == "__main__":
    main()
