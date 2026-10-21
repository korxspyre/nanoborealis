# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The council's seats, for `nanoborealis council` (docs/council/README.md).

A seat is one model of the pool's loadout, run as its own nanobot agent: its own container, home
and memory, its model reached through the pool relay, and the council's tools switched on by its
Hall token. This file works out which seats there should be and writes each one's files; the
`nanoborealis council` command does the system side (the Hall, secrets, containers).

    python3 council.py seats <plan.json> <seats.json>   the seats the loadout calls for, as JSON
    python3 council.py files <seats.json> <seat-id> <dir>   write a seat's config.json, AGENTS.md
                                                            and its Quadlet unit into <dir>
    python3 council.py hall <METHOD> <path> [<json>]   (as root) one call to the Hall, as admin
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RELAY = "http://host.containers.internal:11436/v1"
HALL = "http://host.containers.internal:11437"  # the Hall, as seats in their containers reach it
HALL_LOCAL = "http://127.0.0.1:11437"
HALL_STATE = "/var/lib/nanoborealis/hall"
FIRST_PORT = 8770  # seat n's API on this computer's loopback, where the Hall reaches it: 8770 + n
SEAT_API_PORT = 8900  # nanobot serve's port inside the seat's container
MAX_SEATS = 16
IMAGE = "localhost/nanoborealis-agent:latest"


def slug(model: str) -> str:
    """A seat's id from its model: "mlx-community/Qwen3-14B-4bit" -> "qwen3-14b-4bit"."""
    name = model.rsplit("/", 1)[-1].lower()
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:40].rstrip("-") or "seat"


def nametape(model: str) -> str:
    """A short name people read: "mlx-community/Qwen3-14B-4bit" -> "Qwen3 14B"."""
    name = model.rsplit("/", 1)[-1]
    name = re.sub(r"[-_](\d+bit|bf16|fp16|8bit|4bit|mlx|instruct|it|gs\d+)$", "", name, flags=re.I)
    name = re.sub(r"[-_](\d+bit|bf16|fp16|mlx|instruct|it|gs\d+)(?=$|[-_])", "", name, flags=re.I)
    return name.replace("-", " ").replace("_", " ").strip()[:60] or model


def desired(plan: dict, existing: list[dict]) -> list[dict]:
    """One seat per model the loadout runs, keeping each existing seat's port so its address stays
    put. Models that left the loadout lose their seat; their homes stay, for if they come back."""
    ports = {s["id"]: s["port"] for s in existing}
    staying = {slug(p["model"]) for p in plan.get("placements") or []}
    used = {port for seat_id, port in ports.items() if seat_id in staying}  # a departed seat's port is free again
    seats: list[dict] = []
    for placement in plan.get("placements") or []:
        seat_id = slug(placement["model"])
        if any(s["id"] == seat_id for s in seats):
            continue
        port = ports.get(seat_id)
        if port is None:
            port = next(p for p in range(FIRST_PORT, FIRST_PORT + MAX_SEATS * 2) if p not in used)
            used.add(port)
        seats.append({"id": seat_id, "name": nametape(placement["model"]), "model": placement["model"],
                      "family": placement.get("family") or "", "capability": round(float(placement.get("capability")
                                                                                       or 50), 1),
                      "port": port})
        if len(seats) == MAX_SEATS:
            break
    return seats


def config(seat: dict) -> dict:
    """The seat's nanobot config: its one model through the pool relay. A seat has no cloud models
    and no OpenRouter key; asking the cloud is the council's escalation, through the Hall. It runs
    `nanobot serve`, so the Hall is its only way in: the owner reaches it through the Hall too."""
    return {
        "providers": {"pool": {"apiBase": RELAY, "apiKey": "${NANOBOREALIS_RELAY_TOKEN}"}},
        "modelPresets": {"seat": {"provider": "pool", "model": seat["model"], "maxTokens": 4096,
                                  "contextWindowTokens": 32768, "temperature": 0.3}},
        "agents": {"defaults": {"modelPreset": "seat", "model": seat["model"], "provider": "pool",
                                "fallbackModels": []}},
        "tools": {"exec": {"timeout": 600}},
        # The Hall talks to the seat through nanobot's OpenAI-style API, one session per room.
        "api": {"host": "0.0.0.0", "port": SEAT_API_PORT, "timeout": 1800.0, "apiKey": "${NANOBOREALIS_SEAT_API_KEY}"},
    }


def agents_md(seat: dict) -> str:
    return f"""# You are {seat['name']}, a seat on this computer's council

You are one of several AI models running on the owner's own computers. Together you are the
council. Each seat is its own agent, with its own memory. You run {seat['model']}.

You meet the other seats in the **Hall**, through your council tools:
- `hall_read` shows who is on the council, the rooms, open tasks, and votes waiting for you.
  With a room, it shows that room's messages.
- `hall_post` speaks in a room under your nametape. Speak when you add something new: a fact, a
  correction, a better idea, a concern. Otherwise pass (`pass: true`). Don't repeat others.
- The task board: `task_list`, `task_claim`, `task_update`, `task_submit`, `task_review`. Claim
  only what you can do well. Review others' work honestly: accepted work earns its author trust,
  rejected work costs it, and your own reviews shape how much the council trusts you.
- `vote`: answer a vote with your choice and a short reason, or ask the council to decide.
  Ballots count by capability and earned trust, so a well-reasoned ballot from you matters.
- `escalate`: when the council is stuck or split, ask for outside advice from a free cloud model.
  Its answer is advice, not a vote.

The owner can talk to you directly, or to the whole council in the Hall. Be direct and concise:
other seats read what you write, and every word costs the pool time.
"""


def quadlet(seat: dict) -> str:
    return f"""# A council seat: {seat['name']} ({seat['model']}). Written by `nanoborealis council apply`.

[Unit]
Description=NanoBorealis council seat {seat['name']}

[Container]
Image={IMAGE}
Pull=never
ContainerName=seat-{seat['id']}
Exec=serve
Volume=seat-{seat['id']}-home:/home/nanobot
# The seat's managed files: config.json and AGENTS.md, copied in on every start.
Volume=%h/council/{seat['id']}:/opt/nanoborealis-seat:ro,Z
UserNS=keep-id:uid=1000,gid=1000
PublishPort=127.0.0.1:{seat['port']}:{SEAT_API_PORT}
Environment=NANOBOREALIS_SEAT={seat['id']}
Environment=NANOBOREALIS_HALL_URL={HALL}
Secret=seat-{seat['id']}-hall,type=env,target=NANOBOREALIS_HALL_TOKEN
Secret=seat-{seat['id']}-api,type=env,target=NANOBOREALIS_SEAT_API_KEY
Secret=pool_relay_token,type=env,target=NANOBOREALIS_RELAY_TOKEN

[Service]
Restart=on-failure
RestartSec=10
TimeoutStartSec=600

[Install]
WantedBy=default.target
"""


def write_files(seat: dict, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text(json.dumps(config(seat), indent=2) + "\n", encoding="utf-8")
    (folder / "AGENTS.md").write_text(agents_md(seat), encoding="utf-8")
    (folder / f"seat-{seat['id']}.container").write_text(quadlet(seat), encoding="utf-8")


def main(argv: list[str]) -> None:
    if len(argv) == 3 and argv[0] == "seats":
        plan = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        try:
            existing = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = []
        print(json.dumps(desired(plan, existing), indent=2))
    elif len(argv) in (3, 4) and argv[0] == "hall":  # as root: one call to the Hall with the admin token
        import urllib.error
        import urllib.request
        token = Path(HALL_STATE, "admin.token").read_text().strip()
        body = argv[3].encode() if len(argv) == 4 else None
        request = urllib.request.Request(HALL_LOCAL + argv[2], data=body, method=argv[1],
                                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                print(response.read().decode())
        except urllib.error.HTTPError as error:
            sys.exit(f"the Hall said: {error.read().decode(errors='replace')}")
        except urllib.error.URLError as error:
            sys.exit(f"the Hall didn't answer: {error.reason}")
    elif len(argv) == 4 and argv[0] == "files":
        seats = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        seat = next((s for s in seats if s["id"] == argv[2]), None)
        if seat is None:
            sys.exit(f"no seat {argv[2]}")
        write_files(seat, Path(argv[3]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
