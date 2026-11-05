# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""`nanoborealis rig`: teams of seats as a file, their mail, the head agent, snapshots. Talks to the Hall as the owner.

    rigctl.py up <spec.toml> [--plan]     make the rig the file describes (--plan only says what it would do)
    rigctl.py ps [name]                   the rigs, or one rig's seats, who is here, and mail waiting
    rigctl.py down <name> [--no-snapshot] take a rig down, after a snapshot
    rigctl.py snapshot <name> [label...]  capture a rig and the work in flight
    rigctl.py restore <name> [--snapshot ID]   bring it back, saying honestly what came back
    rigctl.py send <to> <text...>         mail a seat (id or name@rig), "rig:name", "pod:rig/pod" or "head"
    rigctl.py inbox [seat] [--state S]    your mail, or a seat's
    rigctl.py head                        who leads now, and the line to lead

The Hall's URL and the admin token: NANOBOREALIS_HALL_URL (default http://127.0.0.1:11437) and
NANOBOREALIS_HALL_ADMIN_TOKEN_FILE (default /var/lib/nanoborealis/hall/admin.token). Tokens of outside seats made new are
kept root-only in NANOBOREALIS_SEATS_DIR (default /etc/nanoborealis/seats), never printed.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ.get("NANOBOREALIS_HALL_URL", "http://127.0.0.1:11437").rstrip("/")
TOKEN_FILE = Path(os.environ.get("NANOBOREALIS_HALL_ADMIN_TOKEN_FILE", "/var/lib/nanoborealis/hall/admin.token"))
SEATS_DIR = Path(os.environ.get("NANOBOREALIS_SEATS_DIR", "/etc/nanoborealis/seats"))


def hall(method: str, path: str, body: dict | None = None):
    try:
        token = TOKEN_FILE.read_text().strip()
    except OSError:
        sys.exit(f"can't read the Hall's admin token ({TOKEN_FILE}): run this with sudo, on the computer that hosts the council")
    request = urllib.request.Request(URL + path, data=json.dumps(body).encode() if body else None, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        sys.exit(f"the Hall said {error.code}: {json.loads(error.read() or b'{}').get('error', '')}")
    except urllib.error.URLError as error:
        sys.exit(f"the Hall didn't answer ({error.reason}): start it with: nanoborealis council apply")


def keep_tokens(tokens: dict) -> list[str]:
    """Outside seats' new tokens go to files only root can read; their addresses are returned."""
    kept = []
    if tokens:
        SEATS_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(SEATS_DIR, 0o700) if os.name != "nt" else None
    for address, token in tokens.items():
        path = SEATS_DIR / f"{address.replace('@', '-at-')}.token"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
        kept.append(f"{address} -> {path}")
    return kept


def show_outcomes(outcomes: list[dict]) -> None:
    for o in outcomes:
        print(f"  {o['seat']:<24} {o['outcome']:<19} {o['note']}")


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    action, args = argv[0], argv[1:]
    if action == "up" and args:
        spec = Path(args[0]).read_text(encoding="utf-8")
        answer = hall("POST", "/rigs", {"spec": spec, "dry_run": "--plan" in args})
        plan = answer["plan"]
        print(("Plan for " if answer.get("dry_run") else "Rig ") + answer["rig"] + ":")
        for key in ("create", "update", "keep", "remove"):
            if plan[key]:
                print(f"  {key:<7} {', '.join(plan[key])}")
        for line in keep_tokens(answer.get("tokens") or {}):
            print(f"  token   {line}")
        if answer.get("tokens"):
            print("Seats the pool runs need their agent started: nanoborealis council apply")
    elif action == "ps":
        if args:
            rig = hall("GET", f"/rigs/{args[0]}")
            print(f"{rig['name']}: {rig['culture'] or '(no culture written)'}")
            for seat in rig["seats"]:
                here = "here" if seat["here"] else "away"
                lead = f"  head #{seat['head_rank']}" if seat.get("head_rank") else ""
                print(f"  {seat['address'] or seat['id']:<24} {here:<5} {('mail ' + str(seat['mail_waiting'])) if seat['mail_waiting'] else '':<8}"
                      f" trust {seat['trust']}{lead}")
            for edge in rig["edges"]:
                print(f"  {edge['src']} {edge['kind']} {edge['dst']}")
        else:
            rigs = hall("GET", "/rigs")["rigs"]
            print("\n".join(f"  {r['name']}: {r['seats']} seat(s)" for r in rigs) or "No rigs. Make one with: nanoborealis rig up spec.toml")
    elif action == "down" and args:
        answer = hall("DELETE", f"/rigs/{args[0]}", {"snapshot": False} if "--no-snapshot" in args else {})
        print(f"{args[0]} is down" + (f" (snapshot {answer['snapshot']}; bring it back with: rig restore {args[0]})" if answer["snapshot"] else ""))
    elif action == "snapshot" and args:
        answer = hall("POST", f"/rigs/{args[0]}/snapshot", {"label": " ".join(args[1:])})
        print(f"snapshot {answer['id']}: {answer['seats']} seat(s), {answer['tasks']} task(s) and {answer['mail']} piece(s) of mail in flight")
    elif action == "restore" and args:
        body: dict = {}
        if "--snapshot" in args and args.index("--snapshot") + 1 < len(args):
            body["snapshot"] = int(args[args.index("--snapshot") + 1])
        try:  # which models the pool has now, so a seat whose model is gone awaits your decision instead of being guessed at
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import loadout
            import pool
            body["models"] = [m.id for m in loadout.models_from_list(pool.call("GET", "/models"))]
        except Exception:
            pass
        answer = hall("POST", f"/rigs/{args[0]}/restore", body)
        print(f"Restored {args[0]} from snapshot {answer['snapshot']}:")
        show_outcomes(answer["outcomes"])
        for line in keep_tokens(answer.get("tokens") or {}):
            print(f"  token   {line}")
        if answer["tasks_reopened"]:
            print(f"  back on the board: task(s) {', '.join(map(str, answer['tasks_reopened']))}")
    elif action == "send" and len(args) >= 2:
        answer = hall("POST", "/inbox", {"to": args[0], "text": " ".join(args[1:])})
        print(f"sent to {', '.join(answer['to'])}")
    elif action == "inbox":
        seat = args[0] if args and not args[0].startswith("--") else None
        state = args[args.index("--state") + 1] if "--state" in args else None
        mail = hall("GET", "/inbox" + "?" + "&".join(x for x in ((f"seat={seat}" if seat else ""), (f"state={state}" if state else "")) if x))["mail"]
        for m in mail:
            print(f"  [{m['id']}] {m['state']:<9} {m['kind']:<8} from {m['sender']}: {m['text'][:100]}")
        if not mail:
            print("  no mail")
    elif action == "head":
        answer = hall("GET", "/head")
        print(f"Head agent: {answer['head']} ({answer['reason']})")
        for entry in answer["line"]:
            print(f"  #{entry['rank']} {entry['seat']}: {entry['state']}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
