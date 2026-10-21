# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks a paired device's terminal and commands on the remote-access service, end to end.

The service must run as root (terminals run as the person who approved the device, through
runuser), in test mode with NANOBOREALIS_REMOTE_TEST_OWNER naming that person's account:

    sudo env NANOBOREALIS_REMOTE_PORT=18868 NANOBOREALIS_REMOTE_STATE=/tmp/remote3 \
        NANOBOREALIS_REMOTE_TEST_SECRET=x NANOBOREALIS_REMOTE_TEST_PIN_FILE=/tmp/pin3 \
        NANOBOREALIS_REMOTE_TEST_OWNER=$USER python3 system_files/usr/libexec/nanoborealis-remote &
    python dev/terminal_check.py 127.0.0.1:18868 /tmp/pin3 $USER [/tmp/local3.sock]

With the service's NANOBOREALIS_REMOTE_LOCAL_SOCKET as a last argument, it also checks how the
app on the computer itself pairs, with no PIN. Exits non-zero on the first failed check.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pairing import Machine, NotPaired, pair_here, split_address  # noqa: E402
from pairing_check import check, pair  # noqa: E402
from terminal import Terminal, plain, run_command  # noqa: E402


async def read_until(term: Terminal, needle: str, timeout: float = 20) -> str:
    seen = ""
    while needle not in plain(seen):
        chunk = await asyncio.wait_for(term.read(), timeout)
        if not chunk:
            break
        seen += chunk.decode(errors="replace")
    return plain(seen)


async def main(address: str, pin_file: str, owner: str, here_socket: str = "") -> None:
    host, port = split_address(address)
    machine = await asyncio.to_thread(pair, host, port, pin_file, "Terminal check")

    status, output = await asyncio.to_thread(run_command, machine, "id -un; echo $((6 * 7))")
    check(status == 0 and output.split() == [owner, "42"], f"a command runs as {owner} ({output.split()})")
    status, output = await asyncio.to_thread(run_command, machine, "exit 3")
    check(status == 3, "a command's exit status comes back")
    status, output = await asyncio.to_thread(run_command, machine, "sleep 30", 2)
    check(status is None, "a command that runs too long is stopped")

    term = Terminal(machine, rows=30, cols=100, console=True)
    await term.open()
    await term.send("echo terminal-$((7 * 6)) $TERM $(tput cols 2>/dev/null || stty size)\r")
    text = await read_until(term, "terminal-42")
    check("terminal-42 xterm" in text, "a console terminal answers, as a terminal progress bars draw on")
    await term.resize(40, 132)
    await term.send("stty size\r")
    text = await read_until(term, "40 132")
    check("40 132" in text, "the terminal follows the window's size")
    await term.send("exit 5\r")
    while await asyncio.wait_for(term.read(), 20):
        pass
    check(term.exit_status == 5, f"the terminal reports how the shell ended ({term.exit_status})")
    term.close()

    stranger = Machine(machine.host, machine.port, machine.name, machine.certificate, "unknown-" * 6, "0000")
    try:
        await Terminal(stranger).open()
        check(False, "an unknown device gets no terminal")
    except NotPaired:
        check(True, "an unknown device gets no terminal")

    if here_socket:
        here = await asyncio.to_thread(pair_here, here_socket)
        check(here is not None and here.host == "127.0.0.1" and here.port == port,
              "the app on this computer pairs with it by itself, no PIN")
        status, output = await asyncio.to_thread(run_command, here, "id -un")
        check(status == 0 and output.split() == [owner], f"...and works as {owner} ({output.split()})")
        again = await asyncio.to_thread(pair_here, here_socket)
        status, output = await asyncio.to_thread(run_command, again, "true")
        check(status == 0 and again.password != here.password, "pairing again gives it a new password")
        try:
            await asyncio.to_thread(run_command, here, "true")
            check(False, "...which replaces the old one, instead of piling up devices")
        except NotPaired:
            check(True, "...which replaces the old one, instead of piling up devices")
    print("all terminal checks passed")


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        raise SystemExit(__doc__)
    asyncio.run(main(*sys.argv[1:]))
