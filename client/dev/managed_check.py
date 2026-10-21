# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks the remote-access service managing a real agent: its rootless podman, its files inside
its container, its key, and restarts. Run as root, next to the service installed with its real
systemd sandbox and the agent's Quadlet unit (the "managed" job in .github/workflows/test-agent.yml):

    python dev/managed_check.py before 127.0.0.1:8766 /run/nanoborealis/test-pin paired.json
    ... start the agent ...
    python dev/managed_check.py after paired.json

"before" pairs and asks for what needs the agent's podman while the agent isn't running yet, as
the app can right after boot; the agent must still start afterwards. "after" goes through the
agent's files, its WebUI through the service, the owner's commands (in their own session, not the
service's sandbox), a new key (which restarts it) and a restart.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import admin  # noqa: E402
from agent_link import AgentLink, LinkError  # noqa: E402
from checks import check, pair  # noqa: E402
from pairing import Machine, split_address  # noqa: E402
from terminal import run_command  # noqa: E402


def refused(fn, *args) -> str:
    try:
        fn(*args)
    except admin.AdminError as e:
        return str(e)
    return ""


async def no_events(_event: dict) -> None:
    pass


def webui_answers(machine: Machine, seconds: float = 180) -> bool:
    """Whether the agent's WebUI answers through the service within `seconds` (it takes a while to
    start after its container does)."""
    deadline = time.time() + seconds
    while True:
        try:
            return bool(asyncio.run(AgentLink(machine, no_events).bootstrap()).ws_token)
        except LinkError as e:
            if time.time() > deadline:
                print(f"     the WebUI didn't answer: {e}")
                return False
            time.sleep(3)


def before(address: str, pin_file: str, saved: str) -> None:
    host, port = split_address(address)
    machine = pair(host, port, pin_file, "Managed agent check")
    with open(saved, "w") as f:
        json.dump(machine.to_json(), f)
    check(admin.supported(machine), "the computer offers the control center")
    check(not admin.overview(machine)["agent"]["running"], "the agent isn't running yet")
    try:
        spent = admin.usage(machine)
        check("no OpenRouter key" not in str(spent.get("error", "")), f"the agent's key is read before it runs: {spent}")
    except admin.AdminError as e:  # the placeholder key: OpenRouter turns it down, so it was read
        check("OpenRouter" in str(e), f"the agent's key is read before it runs ({e})")
    check("isn't running" in refused(admin.list_files, machine), "its files wait for it to run")


def after(saved: str) -> None:
    with open(saved) as f:
        machine = Machine.from_json(json.load(f))
    info = admin.overview(machine)["agent"]
    check(info["running"], f"the agent runs ({info['state']})")
    check(webui_answers(machine), "its WebUI answers through the service (the WebUI password read from its secrets)")

    memory = f"{admin.WORKSPACE}/memory/MEMORY.md"
    admin.write_file(machine, memory, "# Memory\n- likes tea\n")
    check(admin.read_file(machine, memory) == "# Memory\n- likes tea\n", "a file in its container round-trips")
    check(any(f["path"].endswith("memory/MEMORY.md") for f in admin.list_files(machine)), "its files are listed")
    check(any(f["path"].endswith("USER.md") for f in admin.list_files(machine)), "...its seeded files too")
    admin.delete_file(machine, memory)
    check(refused(admin.read_file, machine, memory) != "", "a file can be deleted")

    status, output = run_command(machine, "findmnt -no OPTIONS /sys/fs/cgroup | cut -d, -f1; "
                                          "touch /tmp/nanoborealis-terminal-check; cat /proc/self/cgroup")
    words = output.split()
    check(status == 0 and words[:1] == ["rw"] and "/user@" in output,
          f"the owner's commands run in their own session, not the service's sandbox ({' '.join(words)})")

    admin.set_key(machine, "sk-or-v1-" + "0" * 64)
    info = admin.overview(machine)["agent"]
    check(info["running"], f"a new key restarts it, and it runs ({info['state']})")
    info = admin.restart(machine)
    check(info["running"], f"a restart brings it back ({info['state']})")
    check(webui_answers(machine), "its WebUI answers again")
    print("all managed agent checks passed")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "before" and len(sys.argv) == 5:
        before(*sys.argv[2:])
    elif len(sys.argv) == 3 and sys.argv[1] == "after":
        after(sys.argv[2])
    else:
        raise SystemExit(__doc__)
