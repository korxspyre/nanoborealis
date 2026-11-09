# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""A small xterm screen: enough of VT100/xterm for full-screen programs (top, htop, nano, less, vim, mc) in the
app's terminal. Feed it the bytes a PTY sends; read back the screen's lines and the cursor.

It keeps the characters, not their colors: the app draws the screen as monospaced text. What it handles: printing
with wrap, CR LF BS TAB, cursor moves (CUU CUD CUF CUB CNL CPL CHA VPA CUP HVP, save and restore), erasing (ED, EL,
ECH), inserting and deleting characters and lines (ICH DCH IL DL), scrolling (SU SD, scroll regions, IND RI NEL),
the alternate screen (1049, 47, 1047), and it skips what it doesn't draw: colors (SGR), OSC titles, DCS strings,
charset selection and the rest of the private modes. Also keys: what a key on the app's keyboard sends.
"""

from __future__ import annotations

import codecs


class Screen:
    def __init__(self, rows: int = 24, cols: int = 80):
        self.rows, self.cols = rows, cols
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.main = self.blank_lines(rows)
        self.alternate: list[list[str]] | None = None
        self.lines = self.main
        self.x = self.y = 0
        self.saved = (0, 0)
        self.top, self.bottom = 0, rows - 1  # the scroll region
        self.wrap_next = False
        self.state = "text"
        self.params = ""
        self.cursor_visible = True

    def blank_lines(self, n: int) -> list[list[str]]:
        return [[" "] * self.cols for _ in range(n)]

    # -- Reading --------------------------------------------------------------------------------------------

    def display(self) -> list[str]:
        return ["".join(line).rstrip() for line in self.lines]

    def text_with_cursor(self, cursor: str = "▌") -> str:
        out = [list(line) for line in self.lines]
        if self.cursor_visible and 0 <= self.y < self.rows and 0 <= self.x < self.cols:
            out[self.y][self.x] = cursor
        return "\n".join("".join(line).rstrip() for line in out)

    def resize(self, rows: int, cols: int) -> None:
        def fit(lines: list[list[str]]) -> list[list[str]]:
            lines = [(line + [" "] * cols)[:cols] for line in lines]
            if len(lines) > rows:
                return lines[len(lines) - rows:]
            return lines + [[" "] * cols for _ in range(rows - len(lines))]
        self.main = fit(self.main)
        if self.alternate is not None:
            self.alternate = fit(self.alternate)
        self.lines = self.alternate if self.alternate is not None else self.main
        self.rows, self.cols = rows, cols
        self.top, self.bottom = 0, rows - 1
        self.x, self.y = min(self.x, cols - 1), min(self.y, rows - 1)

    # -- Writing --------------------------------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        for ch in self.decoder.decode(data):
            self.char(ch)

    def char(self, ch: str) -> None:
        state = self.state
        if state == "text":
            if ch == "\x1b":
                self.state = "esc"
            elif ch >= " " and ch != "\x7f":
                self.put(ch)
            else:
                self.control(ch)
        elif state == "esc":
            self.escape(ch)
        elif state == "csi":
            if "0" <= ch <= "?" :  # parameters and private markers
                self.params += ch
            elif " " <= ch <= "/":  # intermediates
                self.params += ch
            else:
                self.state = "text"
                self.csi(ch, self.params)
        elif state in ("osc", "dcs"):
            if ch == "\x07":
                self.state = "text"
            elif ch == "\x1b":
                self.state = state + "-esc"
        elif state in ("osc-esc", "dcs-esc"):
            self.state = "text" if ch == "\\" else state[:-4]
        elif state == "charset":
            self.state = "text"

    def control(self, ch: str) -> None:
        if ch == "\r":
            self.x, self.wrap_next = 0, False
        elif ch in "\n\x0b\x0c":
            self.index()
        elif ch == "\b":
            self.x, self.wrap_next = max(0, self.x - 1), False
        elif ch == "\t":
            self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)

    def escape(self, ch: str) -> None:
        self.state = "text"
        if ch == "[":
            self.state, self.params = "csi", ""
        elif ch == "]":
            self.state = "osc"
        elif ch in "PX^_":
            self.state = "dcs"
        elif ch in "()*+":
            self.state = "charset"
        elif ch == "7":
            self.saved = (self.x, self.y)
        elif ch == "8":
            self.x, self.y = self.saved
        elif ch == "D":
            self.index()
        elif ch == "E":
            self.x = 0
            self.index()
        elif ch == "M":
            self.reverse_index()
        elif ch == "c":
            self.__init__(self.rows, self.cols)

    def put(self, ch: str) -> None:
        if self.wrap_next:
            self.x, self.wrap_next = 0, False
            self.index()
        self.lines[self.y][self.x] = ch
        if self.x == self.cols - 1:
            self.wrap_next = True
        else:
            self.x += 1

    def index(self) -> None:
        if self.y == self.bottom:
            self.scroll_up(1)
        elif self.y < self.rows - 1:
            self.y += 1

    def reverse_index(self) -> None:
        if self.y == self.top:
            self.scroll_down(1)
        elif self.y > 0:
            self.y -= 1

    def scroll_up(self, n: int) -> None:
        for _ in range(n):
            del self.lines[self.top]
            self.lines.insert(self.bottom, [" "] * self.cols)

    def scroll_down(self, n: int) -> None:
        for _ in range(n):
            del self.lines[self.bottom]
            self.lines.insert(self.top, [" "] * self.cols)

    def csi(self, final: str, raw: str) -> None:
        private = raw.startswith("?") or raw.startswith(">") or raw.startswith("=")
        body = raw.lstrip("?>=").rstrip(" !\"#$%&'()*+,-./")
        try:
            args = [int(p) if p else 0 for p in body.split(";")] if body else []
        except ValueError:
            args = []
        n = max(1, args[0]) if args else 1
        self.wrap_next = False
        if private:
            if final in "hl" and args:
                on = final == "h"
                for mode in args:
                    if mode in (1049, 47, 1047):
                        self.switch_screen(on, save=mode == 1049)
                    elif mode == 25:
                        self.cursor_visible = on
            return
        if final == "A":
            self.y = max(self.top if self.y >= self.top else 0, self.y - n)
        elif final == "B":
            self.y = min(self.bottom if self.y <= self.bottom else self.rows - 1, self.y + n)
        elif final == "C":
            self.x = min(self.cols - 1, self.x + n)
        elif final == "D":
            self.x = max(0, self.x - n)
        elif final == "E":
            self.x, self.y = 0, min(self.rows - 1, self.y + n)
        elif final == "F":
            self.x, self.y = 0, max(0, self.y - n)
        elif final in "G`":
            self.x = min(self.cols - 1, n - 1)
        elif final == "d":
            self.y = min(self.rows - 1, n - 1)
        elif final in "Hf":
            row = args[0] if args else 1
            col = args[1] if len(args) > 1 else 1
            self.y, self.x = min(self.rows - 1, max(1, row) - 1), min(self.cols - 1, max(1, col) - 1)
        elif final == "J":
            mode = args[0] if args else 0
            if mode == 0:
                self.lines[self.y][self.x:] = [" "] * (self.cols - self.x)
                for row in range(self.y + 1, self.rows):
                    self.lines[row] = [" "] * self.cols
            elif mode == 1:
                self.lines[self.y][:self.x + 1] = [" "] * (self.x + 1)
                for row in range(self.y):
                    self.lines[row] = [" "] * self.cols
            else:
                for row in range(self.rows):
                    self.lines[row] = [" "] * self.cols
        elif final == "K":
            mode = args[0] if args else 0
            line = self.lines[self.y]
            if mode == 0:
                line[self.x:] = [" "] * (self.cols - self.x)
            elif mode == 1:
                line[:self.x + 1] = [" "] * (self.x + 1)
            else:
                self.lines[self.y] = [" "] * self.cols
        elif final == "X":
            line = self.lines[self.y]
            for i in range(self.x, min(self.cols, self.x + n)):
                line[i] = " "
        elif final == "@":
            line = self.lines[self.y]
            line[self.x:] = ([" "] * n + line[self.x:])[: self.cols - self.x]
        elif final == "P":
            line = self.lines[self.y]
            line[self.x:] = (line[self.x + n:] + [" "] * n)[: self.cols - self.x]
        elif final == "L" and self.top <= self.y <= self.bottom:
            for _ in range(n):
                del self.lines[self.bottom]
                self.lines.insert(self.y, [" "] * self.cols)
        elif final == "M" and self.top <= self.y <= self.bottom:
            for _ in range(n):
                del self.lines[self.y]
                self.lines.insert(self.bottom, [" "] * self.cols)
        elif final == "S":
            self.scroll_up(n)
        elif final == "T":
            self.scroll_down(n)
        elif final == "r":
            top = (args[0] if args and args[0] else 1) - 1
            bottom = (args[1] if len(args) > 1 and args[1] else self.rows) - 1
            if 0 <= top < bottom < self.rows:
                self.top, self.bottom = top, bottom
                self.x, self.y = 0, 0
        elif final == "s":
            self.saved = (self.x, self.y)
        elif final == "u":
            self.x, self.y = self.saved
        # m (colors), n (reports), t, q and the rest: nothing to draw

    def switch_screen(self, alternate: bool, save: bool) -> None:
        if alternate and self.alternate is None:
            if save:
                self.saved = (self.x, self.y)
            self.alternate = self.blank_lines(self.rows)
            self.lines = self.alternate
        elif not alternate and self.alternate is not None:
            self.alternate = None
            self.lines = self.main
            if save:
                self.x, self.y = self.saved
        self.top, self.bottom = 0, self.rows - 1


# -- Keys -----------------------------------------------------------------------------------------------------

SPECIAL = {
    "enter": "\r", "backspace": "\x7f", "tab": "\t", "escape": "\x1b", "delete": "\x1b[3~",
    "arrow up": "\x1b[A", "arrow down": "\x1b[B", "arrow right": "\x1b[C", "arrow left": "\x1b[D",
    "home": "\x1b[H", "end": "\x1b[F", "page up": "\x1b[5~", "page down": "\x1b[6~", "insert": "\x1b[2~",
    "f1": "\x1bOP", "f2": "\x1bOQ", "f3": "\x1bOR", "f4": "\x1bOS", "f5": "\x1b[15~", "f6": "\x1b[17~",
    "f7": "\x1b[18~", "f8": "\x1b[19~", "f9": "\x1b[20~", "f10": "\x1b[21~", "f11": "\x1b[23~", "f12": "\x1b[24~",
    " ": " ", "space": " ",
}
SHIFTED = dict(zip("`1234567890-=[]\\;',./", "~!@#$%^&*()_+{}|:\"<>?"))


def key_bytes(key: str, shift: bool = False, ctrl: bool = False, alt: bool = False) -> str:
    """What a terminal sends for a key the app reports (Flet's names: 'A', 'Enter', 'Arrow Up', ...)."""
    name = (key or "").lower()
    if name in SPECIAL:
        out = SPECIAL[name]
        if shift and name == "tab":
            out = "\x1b[Z"
    elif len(name) == 1:
        if ctrl and "a" <= name <= "z":
            out = chr(ord(name) - 96)
        elif ctrl and name in "[\\]":
            out = chr({"[": 27, "\\": 28, "]": 29}[name])
        elif shift:
            out = name.upper() if name.isalpha() else SHIFTED.get(name, name)
        else:
            out = name
    else:
        return ""
    return ("\x1b" + out) if alt and out else out
