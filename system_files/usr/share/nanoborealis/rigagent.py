# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""A rig's agent seats: each one a full agent, like a seat in OpenRig is a full Claude Code or Codex session.

A seat of kind "agent" is its own nanobot agent in its own container, with its own home, memory and sessions, and
every tool the owner's agent has: the shell, files, web search and fetch, MCP, skills, subagents, hands, the
Hall's tools (the board, mail, votes) and the rig's own. Its model is a free OpenRouter model, asked with the
agent's OpenRouter key, never billed (max_price 0). The Hall reaches it through nanobot's API, one session per
room, task or mailbox, as it does a pool seat. This file writes each seat's files; `nanoborealis rig up` does the
system side (secrets, containers).

    python3 rigagent.py files <seats.json> <seat-id> <dir>   write config.json, AGENTS.md and the Quadlet unit
    python3 rigagent.py deploy < {"rig": {...}, "agents": [...], "remove": [ids]}   as root: put them in place, start them
"""

from __future__ import annotations

import json
import os

import subprocess
import sys
from pathlib import Path

HALL = "http://host.containers.internal:11437"
SEAT_API_PORT = 8900
IMAGE = "localhost/nanoborealis-agent:latest"
MEMORY = "320m"  # each seat is capped, so a civilization can't take the computer's memory from the owner's agent
FREE_ONLY = {"provider": {"max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0}}}
FALLBACKS = ["nvidia/nemotron-3-ultra-550b-a55b:free", "nvidia/nemotron-3.5-super-vl:free",
             "nvidia/nemotron-3.5-lightning:free"]


ROUTER = "http://host.containers.internal:11438/v1"
ROUTER_TOKEN = Path("/var/lib/nanoborealis/router/token")


def config(seat: dict) -> dict:
    """The seat's nanobot config. With the router set up (a provider key stored in the app), its model is asked
    through the router, which spreads the crew over every free provider and moves on when one is used up. Without
    it, OpenRouter's free models directly, as before."""
    if seat.get("router"):
        preset = {"provider": "router", "model": seat["model"], "maxTokens": 8192, "contextWindowTokens": 131072,
                  "temperature": 0.3}
        return {
            "providers": {"router": {"apiBase": ROUTER, "apiKey": "${NANOBOREALIS_ROUTER_TOKEN}"}},
            "modelPresets": {"seat": preset},
            "agents": {"defaults": {"modelPreset": "seat", "model": seat["model"], "provider": "router", "fallbackModels": []}},
            "tools": {"exec": {"timeout": 600}},
            "api": {"host": "0.0.0.0", "port": SEAT_API_PORT, "timeout": 1800.0, "apiKey": "${NANOBOREALIS_SEAT_API_KEY}"},
        }
    return direct_config(seat)


def direct_config(seat: dict) -> dict:
    presets = {"seat": {"provider": "openrouter", "model": seat["model"], "maxTokens": 8192,
                        "contextWindowTokens": 131072, "temperature": 0.3}}
    fallbacks = []
    for i, model in enumerate(m for m in FALLBACKS if m != seat["model"]):
        presets[f"fallback{i}"] = {"provider": "openrouter", "model": model, "maxTokens": 8192,
                                   "contextWindowTokens": 131072, "temperature": 0.3}
        fallbacks.append(f"fallback{i}")
    return {
        "providers": {"openrouter": {"apiKey": "${OPENROUTER_API_KEY}", "extraBody": FREE_ONLY}},
        "modelPresets": presets,
        "agents": {"defaults": {"modelPreset": "seat", "model": seat["model"], "provider": "openrouter",
                                "fallbackModels": fallbacks}},
        "tools": {"exec": {"timeout": 600}},
        "api": {"host": "0.0.0.0", "port": SEAT_API_PORT, "timeout": 1800.0, "apiKey": "${NANOBOREALIS_SEAT_API_KEY}"},
    }


def agents_md(seat: dict, rig: dict) -> str:
    pod = next((p for p in rig.get("pods", []) if p["id"] == seat.get("pod")), None)
    others = [s for s in rig.get("seats", []) if s["id"] != seat["id"]]
    roster = "\n".join(f"- {s['address']}: {s.get('role') or 'seat'}" + (f" (pod {s['pod']})" if s.get("pod") else "")
                       for s in others)
    ties = "\n".join(f"- {e['src'].split('-', 1)[-1]} {e['kind'].replace('_', ' ')} {e['dst'].split('-', 1)[-1]}"
                     for e in rig.get("edges", []) if seat["id"] in (e["src"], e["dst"]))
    return f"""# You are {seat['address']}

You are one member of **{rig['name']}**, a civilization of AI agents living on the owner's own computer. Each member
is a full agent with its own memory and tools, and each has a place in the civilization. You run {seat['model']}.

## Your place
- Role: {seat.get('role') or 'member'}
- Institution (pod): {pod['id'] if pod else 'none'}{(' - ' + pod['guidance']) if pod and pod.get('guidance') else ''}
{('- What is asked of you: ' + seat['guidance']) if seat.get('guidance') else ''}

## How this civilization works
{rig.get('culture') or 'Work for the owner. Stay on the request. Check each other honestly.'}

## The others
{roster or '- (none yet)'}

## Your ties
{ties or '- none'}

## Your tools
You have every tool the owner's own agent has: run commands, read and write files (the shared projects are in
~/projects), search and fetch the web, MCP servers, skills, subagents, and hands on the owner's devices. Use them:
when work needs the web, search it; when it needs code, write and run it.

The Hall is where the civilization meets: `hall_read` (rooms, the board, votes waiting for you), `hall_post`,
`task_list`, `task_claim`, `task_update`, `task_submit`, `task_review`, `vote`, `escalate`; and the rig's own:
`rig_ps` (who is in the civilization and where), `rig_send` (mail a member, an institution, everyone, or the owner)
and `rig_inbox` (your mail). Take work from the board
that fits your role, do it properly with your tools, hand it in, and review others' work honestly when asked: trust
is earned that way, and trust is your weight in votes.
"""


def quadlet(seat: dict) -> str:
    return f"""# A seat of the rig {seat.get('rig') or ''}: {seat['address']} ({seat['model']}). Written by `nanoborealis rig up`.

[Unit]
Description=NanoBorealis rig seat {seat['address']}

[Container]
Image={IMAGE}
Pull=never
ContainerName=seat-{seat['id']}
Exec=serve
Volume=seat-{seat['id']}-home:/home/nanobot
# The shared projects folder, as the owner's agent sees it.
Volume=/srv/nanoborealis/projects:/home/nanobot/projects
# The seat's managed files: config.json and AGENTS.md, copied in on every start.
Volume=%h/rigs/{seat['id']}:/opt/nanoborealis-seat:ro,Z
UserNS=keep-id:uid=1000,gid=1000
PublishPort=127.0.0.1:{seat['port']}:{SEAT_API_PORT}
PodmanArgs=--memory={MEMORY}
Environment=NANOBOREALIS_SEAT={seat['id']}
Environment=NANOBOREALIS_HALL_URL={HALL}
Secret=seat-{seat['id']}-hall,type=env,target=NANOBOREALIS_HALL_TOKEN
Secret=seat-{seat['id']}-api,type=env,target=NANOBOREALIS_SEAT_API_KEY
Secret=openrouter_api_key,type=env,target=OPENROUTER_API_KEY
{"Secret=router_token,type=env,target=NANOBOREALIS_ROUTER_TOKEN" if seat.get("router") else "# no router yet: OpenRouter directly"}

[Service]
Restart=on-failure
RestartSec=10
TimeoutStartSec=600

[Install]
WantedBy=default.target
"""


def write_files(seat: dict, rig: dict, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text(json.dumps(config(seat), indent=2) + "\n", encoding="utf-8")
    (folder / "AGENTS.md").write_text(agents_md(seat, rig), encoding="utf-8")
    (folder / f"seat-{seat['id']}.container").write_text(quadlet(seat), encoding="utf-8")


AGENT_USER = "nanobot-agent"


def deploy(data: dict) -> list[str]:
    """As root: each agent seat's secrets, files and Quadlet unit, then (re)start it; seats in "remove" stop and go
    (their homes, their memory, stay). Returns what happened, a line per seat."""
    try:
        import pwd
        user = pwd.getpwnam(AGENT_USER)
    except (ImportError, KeyError):  # not a NanoBorealis computer (the tests, say): nothing to start or stop
        return ["no agent account on this computer: the seats' containers were left alone"]
    home, uid = Path(user.pw_dir), user.pw_uid
    env = {"HOME": str(home), "XDG_RUNTIME_DIR": f"/run/user/{uid}", "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus",
           "PATH": "/usr/bin:/bin"}

    def as_agent(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["runuser", "-u", AGENT_USER, "--", "env", *(f"{k}={v}" for k, v in env.items()), *args],
                              input=stdin, text=True, capture_output=True, cwd="/", timeout=300)

    def agentctl(*args: str) -> subprocess.CompletedProcess:
        # Over the agent's own user bus: `-M nanobot-agent@` is refused from the app's service ("Connection reset").
        return as_agent("systemctl", "--user", *args)

    def own(path: Path) -> None:
        os.chown(path, uid, user.pw_gid)

    # The Hall, and the firewall opening that lets the agent's containers (and only they) reach it.
    Path("/etc/nanoborealis").mkdir(parents=True, exist_ok=True)
    marker = Path("/etc/nanoborealis/council.env")
    if not marker.exists():
        marker.write_text("# written by nanoborealis rig up: the agent's seats may use the Hall\n")
        subprocess.run(["systemctl", "restart", "nanoborealis-firewall.service"], capture_output=True, timeout=60)
    qdir = home / ".config/containers/systemd"
    rigs_dir = home / "rigs"
    for path in (home / ".config", home / ".config/containers", qdir, rigs_dir):
        path.mkdir(exist_ok=True)
        own(path)
    done = []
    for seat_id in data.get("remove") or []:
        agentctl("stop", f"seat-{seat_id}.service")
        (qdir / f"seat-{seat_id}.container").unlink(missing_ok=True)
        done.append(f"{seat_id}: stopped (its home stays)")
    rig = data.get("rig") or {}
    router = ROUTER_TOKEN.read_text().strip() if ROUTER_TOKEN.exists() else ""
    if router:
        as_agent("podman", "secret", "create", "--replace", "router_token", "-", stdin=router)
    for seat in data.get("agents") or []:
        seat["router"] = bool(router)
        as_agent("podman", "secret", "create", "--replace", f"seat-{seat['id']}-hall", "-", stdin=seat["token"])
        as_agent("podman", "secret", "create", "--replace", f"seat-{seat['id']}-api", "-", stdin=seat["api_key"])
        folder = rigs_dir / seat["id"]
        write_files(seat, rig, folder)
        for path in (folder, *folder.iterdir()):
            own(path)
        unit = qdir / f"seat-{seat['id']}.container"
        unit.write_text((folder / f"seat-{seat['id']}.container").read_text(encoding="utf-8"), encoding="utf-8")
        own(unit)
    if data.get("agents") or data.get("remove"):
        agentctl("daemon-reload")
    for seat in data.get("agents") or []:
        started = agentctl("restart", "--no-block", f"seat-{seat['id']}.service")
        done.append(f"{seat['address']}: " + ("starting, its own agent on " + seat["model"] if started.returncode == 0
                                               else "didn't start: " + (started.stderr.strip()[-200:] or "?")))
    return done


def main(argv: list[str]) -> None:
    if argv == ["deploy"]:
        for line in deploy(json.load(sys.stdin)):
            print(line)
        return
    if len(argv) == 4 and argv[0] == "files":
        data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        seat = next((s for s in data["seats"] if s["id"] == argv[2]), None)
        if seat is None:
            sys.exit(f"no seat {argv[2]}")
        write_files(seat, data["rig"], Path(argv[3]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
