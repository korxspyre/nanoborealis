# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks that the app finds a paired computer again after the router gives it a new address
(discovery.locate), that a live link follows it there, and that a sweep of this device's networks
finds computers where mDNS doesn't get through (discovery.sweep), against the remote-access service:

    python dev/locate_check.py paired.json 10.254.254.2 10.254.254.1

paired.json is a pairing with the service at 127.0.0.1. The first address is where the computer
"was" (nothing answers there any more); the second is where the service answers, in the same
/24. See .github/workflows/test-client.yml for the stand-in network.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import discovery  # noqa: E402
from agent_link import AgentLink  # noqa: E402
from checks import check  # noqa: E402
from pairing import Machine  # noqa: E402


async def follow(machine: Machine) -> list[str]:
    events: list[str] = []
    up = asyncio.Event()

    async def on_event(event: dict) -> None:
        events.append(event["event"])
        if event["event"] == "link_up":
            up.set()

    link = AgentLink(machine, on_event, locate=discovery.locate)
    task = asyncio.create_task(link.run())
    try:
        await asyncio.wait_for(up.wait(), 120)
    except asyncio.TimeoutError:
        pass
    finally:
        await link.close()
        task.cancel()
    return events


def main(path: str, old: str, new: str) -> None:
    with open(path) as f:
        paired = Machine.from_json(json.load(f))
    moved = Machine(**{**paired.to_json(), "host": old})
    start = time.time()
    found = discovery.locate(moved)
    check(found == new, f"a computer with a new address is found there ({found}, {time.time() - start:.1f} s)")
    check(discovery.locate(paired) is None, "the app's own computer (127.0.0.1) is never looked for elsewhere")
    events = asyncio.run(follow(moved))
    check(moved.host == new and "moved" in events and "link_up" in events,
          f"a link to it follows it there ({' '.join(events)})")

    # Where mDNS doesn't get through: the service answers at two of this network's addresses.
    home = new.rsplit(".", 1)[0] + "."
    start = time.time()
    found = [f for f in discovery.sweep(paired.port) if f.address.startswith(home)]
    check(len(found) == 1 and found[0].name.startswith("NanoBorealis on "),
          f"a sweep of this device's networks finds the computer, once ({found}, {time.time() - start:.1f} s)")
    print("all locate checks passed")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
