# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks pairing and the remote-access service (/usr/libexec/nanoborealis-remote) end to end.

Run the service in test mode in front of a gateway (the real one, or dev/stub_gateway.py):

    NANOBOREALIS_REMOTE_PORT=18766 NANOBOREALIS_REMOTE_UPSTREAM_PORT=18765 \
    NANOBOREALIS_REMOTE_STATE=/tmp/remote NANOBOREALIS_REMOTE_TEST_SECRET=test-secret \
    NANOBOREALIS_REMOTE_TEST_PIN_FILE=/tmp/pin python system_files/usr/libexec/nanoborealis-remote &
    python dev/pairing_check.py 127.0.0.1:18766 /tmp/pin /tmp/paired.json

It pairs, checks that wrong PINs, unpaired devices, other certificates and floods are turned away,
and saves one paired computer to the last file for dev/smoke.py. Exits non-zero on the first failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_link import AgentLink, AuthError  # noqa: E402
from pairing import Machine, Pairing, PairingError, WrongPin, split_address, unpair  # noqa: E402
from checks import check, pair, read_pin  # noqa: E402,F401  (terminal_check imports them from here)


def other_certificate() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", os.path.join(tmp, "k.pem"), "-out", os.path.join(tmp, "c.pem"),
                        "-days", "1", "-subj", "/CN=Impostor"], check=True, capture_output=True)
        return open(os.path.join(tmp, "c.pem")).read()


async def on_event(event: dict) -> None:
    events.put_nowait(event)


events: asyncio.Queue[dict] = asyncio.Queue()


async def main(address: str, pin_file: str, save_to: str) -> None:
    host, port = split_address(address)

    # Pairing, with a typo first: caught here, so it costs none of the computer's tries.
    session = Pairing(host, port, "Pairing check")
    session.start()
    asked = time.time() - 1
    session.reveal()
    pin = read_pin(pin_file, asked)
    check(len(pin) == 6 and pin.isdigit(), "the computer shows a 6-digit PIN")
    check(pin == session.expected_pin(), "the app derives the same PIN from what it saw")
    wrong = f"{(int(pin) + 1) % 1_000_000:06d}"
    try:
        session.finish(wrong)
        check(False, "a mistyped PIN is caught before it's sent")
    except WrongPin:
        check(True, "a mistyped PIN is caught before it's sent")
    try:  # the same mistake sent anyway, as a stranger guessing would
        session._post("/nanoborealis/pair/finish", {"request": session._request, "pin": wrong})
        check(False, "the computer turns a wrong PIN down")
    except WrongPin as e:
        check("Wrong PIN" in str(e), "the computer turns a wrong PIN down")
    machine = session.finish(pin)
    check(len(machine.password) >= 40 and bool(machine.device_id), "paired: a long password for this device")
    try:
        session._post("/nanoborealis/pair/finish", {"request": session._request, "pin": pin})
        check(False, "a pairing request is good once")
    except PairingError:
        check(True, "a pairing request is good once")

    # Through the service to the agent.
    link = AgentLink(machine, on_event)
    boot = await link.bootstrap()
    check(boot.ws_url.startswith("wss://") and f":{port}/" in boot.ws_url, f"bootstrap through TLS ({boot.ws_url})")
    chats = await link.list_chats()
    check(isinstance(chats, list), f"the agent's REST API answers ({len(chats)} chats)")
    runner = asyncio.create_task(link.run())
    try:
        while (await asyncio.wait_for(events.get(), 30))["event"] != "link_up":
            pass
        check(True, "WebSocket connected over TLS")
        chat_id = await link.new_chat()
        check(bool(chat_id), f"new chat {chat_id}")
    finally:
        await link.close()
        runner.cancel()

    # What must be turned away.
    request = urllib.request.Request(f"https://{host}:{port}/webui/bootstrap")
    try:
        urllib.request.urlopen(request, context=machine.context(), timeout=10)
        check(False, "no device password, no agent")
    except urllib.error.HTTPError as e:
        check(e.code == 401, "no device password, no agent")
    stranger = Machine(host, port, machine.name, machine.certificate, "not-a-real-password-" * 3, "0000")
    try:
        await AgentLink(stranger, on_event).bootstrap()
        check(False, "an unknown device password is refused")
    except AuthError:
        check(True, "an unknown device password is refused")
    impostor = Machine(host, port, machine.name, other_certificate(), machine.password, machine.device_id)
    try:
        await AgentLink(impostor, on_event).bootstrap()
        check(False, "a computer with another certificate isn't trusted")
    except AuthError as e:
        check("isn't the one" in str(e), "a computer with another certificate isn't trusted")

    # A device that unpairs itself is gone.
    second = pair(host, port, pin_file, "Second device")
    await AgentLink(second, on_event).bootstrap()
    unpair(second)
    try:
        await AgentLink(second, on_event).bootstrap()
        check(False, "an unpaired device is refused")
    except AuthError:
        check(True, "an unpaired device is refused")

    # Floods of pairing requests are cut off.
    refused = False
    for _ in range(8):
        try:
            Pairing(host, port, "Flood").start()
        except PairingError as e:
            refused = "Too many" in str(e)
            break
    check(refused, "floods of pairing requests are cut off")

    with open(save_to, "w") as f:
        json.dump(machine.to_json(), f)
    print(f"saved the paired computer to {save_to}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    asyncio.run(main(*sys.argv[1:]))
