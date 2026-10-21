# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Pair with a NanoBorealis computer and run commands on it from a terminal, the way the app does.

    python dev/remote.py pair 192.168.1.20 laptop.json     # shows a PIN on its screen, asks for it
    python dev/remote.py pair-start 192.168.1.20 laptop.json   # or in two steps, for scripts:
    python dev/remote.py pair-finish laptop.json 123456        # the PIN is good for five minutes
    python dev/remote.py run laptop.json 'journalctl -b -1 -k | tail'
    python dev/remote.py push laptop.json src '~/.cache/nb-dev'  # copy files there, as the owner

The paired computer's certificate and this device's password end up in the JSON file: keep it
private. `nanoborealis devices remove <id>` on the computer unpairs it.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shlex
import sys
import tarfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pairing import Machine, Pairing, device_name, split_address  # noqa: E402
from terminal import plain, run_command  # noqa: E402


def save(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)


def start(address: str, path: str) -> Pairing:
    host, port = split_address(address)
    session = Pairing(host, port, f"{device_name()} terminal"[:48])
    session.start()
    session.reveal()
    save(path, {"pairing": {"host": host, "port": port, "name": session.name, "machine": session.machine,
                            "client_nonce": session._client_nonce.hex(), "server_nonce": session._server_nonce.hex(),
                            "certificate": session._certificate.hex(), "request": session._request}})
    print(f"A PIN is on {session.machine}'s screen.")
    return session


def finish(path: str, pin: str, session: Pairing | None = None) -> None:
    if session is None:
        with open(path) as f:
            state = json.load(f)["pairing"]
        session = Pairing(state["host"], int(state["port"]), state["name"])
        session.machine = state["machine"]
        session._client_nonce = bytes.fromhex(state["client_nonce"])
        session._server_nonce = bytes.fromhex(state["server_nonce"])
        session._certificate = bytes.fromhex(state["certificate"])
        session._request = state["request"]
    machine = session.finish(pin)
    save(path, machine.to_json())
    print(f"Paired with {machine.name} as device {machine.device_id}.")


def push(machine: Machine, local: str, remote_dir: str) -> None:
    """Copy a file or folder into remote_dir there (made if missing), through commands: each carries
    a slice of a gzipped tar, small enough for one command line."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(local, arcname=os.path.basename(os.path.normpath(local)),
                filter=lambda i: None if "__pycache__" in i.name or i.name.endswith(".pyc")
                or "/assets/" in i.name + "/" and os.environ.get("PUSH_SKIP_ASSETS") else i)
    data = base64.b64encode(buffer.getvalue()).decode()
    target = remote_dir if remote_dir.startswith("~") else shlex.quote(remote_dir)
    staging = "~/.cache/nb-push.b64"
    run_command(machine, f"mkdir -p ~/.cache && : > {staging}")
    step = 3_500  # the computer takes commands up to 4 KB
    for start in range(0, len(data), step):
        status, output = run_command(machine, f"printf %s {data[start:start + step]} >> {staging}")
        if status != 0:
            raise SystemExit(f"copying failed: {plain(output)}")
    status, output = run_command(machine, f"mkdir -p {target} && base64 -d {staging} | tar -xz -C {target} "
                                          f"&& rm -f {staging}")
    if status != 0:
        raise SystemExit(f"unpacking failed: {plain(output)}")
    print(f"copied {local} to {remote_dir} ({len(buffer.getvalue())} bytes packed)")


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "pair":
        session = start(argv[1], argv[2])
        finish(argv[2], input("PIN: ").strip(), session)
    elif len(argv) == 3 and argv[0] == "pair-start":
        start(argv[1], argv[2])
    elif len(argv) == 3 and argv[0] == "pair-finish":
        finish(argv[1], argv[2])
    elif len(argv) == 4 and argv[0] == "push":
        with open(argv[1]) as f:
            machine = Machine.from_json(json.load(f))
        if machine is None:
            raise SystemExit(f"{argv[1]} doesn't hold a paired computer")
        push(machine, argv[2], argv[3])
    elif len(argv) >= 3 and argv[0] == "run":
        with open(argv[1]) as f:
            machine = Machine.from_json(json.load(f))
        if machine is None:
            raise SystemExit(f"{argv[1]} doesn't hold a paired computer")
        status, output = run_command(machine, " ".join(argv[2:]), timeout=600)
        sys.stdout.write(plain(output))
        if status is None:
            print("\n(stopped: it ran for ten minutes)")
            return 124
        return status
    else:
        raise SystemExit(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
