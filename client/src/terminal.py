# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""A terminal on a paired NanoBorealis computer, and one-off commands there.

Both run as the person who approved this device at the computer's screen (see
/usr/libexec/nanoborealis-remote), over TLS pinned to the computer's certificate, with this
device's password. The terminal's traffic travels in frames: a kind byte (d data, r resize
"rows cols", x exit status), a 4-byte length, then the bytes. Nothing here imports Flet.
"""

from __future__ import annotations

import asyncio
import re

from pairing import DEVICE_HEADER, Machine, NotPaired, PairingError, call

# Escape sequences: CSI ones (cursor movement, erasing, colors) are read for the parts that change
# what a line shows; the rest (window titles, character sets, modes) are dropped.
_ESCAPES = re.compile(r"\x1b\[([0-9;?]*)[ -/]*([@-~])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>78DEHMc]")


# Questions programs ask their terminal, and a terminal's answers. Unanswered, a program waits:
# the login's welcome text asks for the background color and the cursor's position, and sat for
# five seconds on each. Colors are the app's own (theme.py: TEXT, and the console's SIDEBAR).
_QUERIES = re.compile(rb"\x1b\[([0-9]*)n|\x1b\[0?c|\x1b\](1[01]);\?(?:\x07|\x1b\\)")
_COLORS = {b"10": b"rgb:e6e6/eded/f7f7", b"11": b"rgb:0c0c/1313/2525"}


def answers(output: bytes) -> bytes:
    """What a terminal answers to the questions in this output: where the cursor is (the console
    keeps no screen, so the top left), what kind of terminal it is, and its colors."""
    reply = b""
    for match in _QUERIES.finditer(output):
        status, color = match.group(1), match.group(2)
        if status == b"6":
            reply += b"\x1b[1;1R"
        elif status == b"5":
            reply += b"\x1b[0n"
        elif status is None and color is None:
            reply += b"\x1b[?1;2c"
        elif color is not None:
            reply += b"\x1b]" + color + b";" + _COLORS[color] + b"\x1b\\"
    return reply


def _count(params: str) -> int:
    first = params.lstrip("?").split(";")[0]
    return int(first) if first.isdigit() and int(first) > 0 else 1


def plain(text: str) -> str:
    """Terminal output as plain text, for the app's console. It follows what changes a line in
    place: a carriage return, a backspace, and the cursor moves and erasing that progress bars
    redraw themselves with (several bars move up over their own lines), so a redrawn line replaces
    itself instead of piling up. Colors and everything else are dropped."""
    lines: list[list[str]] = [[]]
    row = column = 0

    def put(ch: str) -> None:
        nonlocal column
        cells = lines[row]
        if column < len(cells):
            cells[column] = ch
        else:
            cells.extend(" " * (column - len(cells)))
            cells.append(ch)
        column += 1

    position = 0
    for match in [*_ESCAPES.finditer(text), None]:
        for ch in text[position:match.start() if match else len(text)]:
            if ch == "\n":
                row, column = row + 1, 0
                if row == len(lines):
                    lines.append([])
            elif ch == "\r":
                column = 0
            elif ch == "\b":
                column = max(0, column - 1)
            elif ch >= " " or ch == "\t":
                put(ch)
        if match is None:
            break
        position = match.end()
        params, final = match.group(1), match.group(2)
        if final is None or params is None:
            continue
        mode = params.lstrip("?").split(";")[0]
        if final == "A":  # up
            row = max(0, row - _count(params))
        elif final == "B":  # down
            row += _count(params)
            lines.extend([] for _ in range(row + 1 - len(lines)))
        elif final == "C":  # right
            column += _count(params)
        elif final == "D":  # left
            column = max(0, column - _count(params))
        elif final == "G":  # to a column
            column = _count(params) - 1
        elif final == "K":  # erase in the line: to its end, from its start, or all of it
            cells = lines[row]
            if mode in ("", "0"):
                del cells[column:]
            elif mode == "1":
                cells[:column + 1] = " " * min(len(cells), column + 1)
            else:
                cells.clear()
        elif final == "J" and mode in ("", "0"):  # erase to the end of the screen
            del lines[row][column:]
            del lines[row + 1:]
    return "\n".join("".join(cells) for cells in lines)


def run_command(machine: Machine, command: str, timeout: int = 120) -> tuple[int | None, str]:
    """Run a command in a login shell there; returns (exit status, None if it timed out, output). Blocks."""
    status, data, _ = call(machine.host, machine.port, "POST", "/nanoborealis/command", context=machine.context(),
                           body={"command": command, "timeout": timeout}, headers={DEVICE_HEADER: machine.password},
                           timeout=timeout + 30)
    if status == 401:
        raise NotPaired("This computer doesn't know this device anymore. Pair it again.")
    if status != 200:
        raise PairingError(data.get("error") or f"The computer answered HTTP {status}.")
    return data.get("exit"), str(data.get("output", ""))


class Terminal:
    """One terminal session. `await open()`, then `send()` keys and read output with `read()`
    (b"" when the shell has ended; `exit_status` then holds its status)."""

    def __init__(self, machine: Machine, rows: int = 24, cols: int = 80, console: bool = False):
        self.machine, self.rows, self.cols, self.console = machine, rows, cols, console
        self.exit_status: int | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def open(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(asyncio.open_connection(
                self.machine.host, self.machine.port, ssl=self.machine.context(), server_hostname=None), 15)
        except (OSError, asyncio.TimeoutError) as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                raise NotPaired("The computer at this address isn't the one this app paired with.") from e
            raise PairingError(f"Can't reach {self.machine.name}: {e}") from e
        mode = "&mode=console&progress=1" if self.console else ""
        self._writer.write(
            f"GET /nanoborealis/terminal?rows={self.rows}&cols={self.cols}{mode} HTTP/1.1\r\n"
            f"Host: {self.machine.host}:{self.machine.port}\r\nUpgrade: nanoborealis-terminal\r\n"
            f"Connection: Upgrade\r\n{DEVICE_HEADER}: {self.machine.password}\r\n\r\n".encode())
        await self._writer.drain()
        head = await asyncio.wait_for(self._reader.readuntil(b"\r\n\r\n"), 20)
        status = head.split(b" ", 2)[1] if head.count(b" ") >= 2 else b""
        if status == b"101":
            return
        body = await self._reader.read(4096)
        self.close()
        if status == b"401":
            raise NotPaired("This computer doesn't know this device anymore. Pair it again.")
        message = re.search(rb'"error":\s*"([^"]*)"', body)
        raise PairingError(message.group(1).decode() if message else f"The computer answered {status.decode()}.")

    async def send(self, data: bytes | str) -> None:
        payload = data.encode() if isinstance(data, str) else data
        if self._writer is not None:
            self._writer.write(b"d" + len(payload).to_bytes(4, "big") + payload)
            await self._writer.drain()

    async def resize(self, rows: int, cols: int) -> None:
        self.rows, self.cols = rows, cols
        if self._writer is not None:
            payload = f"{rows} {cols}".encode()
            self._writer.write(b"r" + len(payload).to_bytes(4, "big") + payload)
            await self._writer.drain()

    async def read(self) -> bytes:
        """The next output, or b"" once the shell has ended or the connection closed."""
        while self._reader is not None:
            try:
                head = await self._reader.readexactly(5)
                payload = await self._reader.readexactly(int.from_bytes(head[1:], "big"))
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                return b""
            if head[:1] == b"d":
                if reply := answers(payload):
                    await self.send(reply)
                return payload
            if head[:1] == b"x":
                try:
                    self.exit_status = int(payload.decode() or 0)
                except ValueError:
                    self.exit_status = -1
                return b""
        return b""

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
