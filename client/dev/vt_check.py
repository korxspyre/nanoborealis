# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The app's terminal screen (vt.py): what full-screen programs send, drawn as they'd look."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import vt  # noqa: E402

failed = 0


def check(ok: bool, what: str) -> None:
    global failed
    print(("ok   " if ok else "FAIL ") + what)
    failed += not ok


s = vt.Screen(5, 20)
s.feed(b"hello\r\nworld")
check(s.display()[:2] == ["hello", "world"] and (s.x, s.y) == (5, 1), "text, CR LF, and the cursor after it")
s.feed(b"\x1b[2J\x1b[3;4HX")
check(s.display()[2] == "   X" and all(not line for i, line in enumerate(s.display()) if i != 2), "clear screen, then move and print")
s.feed(b"\x1b[1;1Habcdef\x1b[1;3H\x1b[K")
check(s.display()[0] == "ab", "erase to the end of the line")
s.feed(b"\x1b[?1049h\x1b[HALT")
check(s.display()[0] == "ALT" and s.alternate is not None, "the alternate screen (vim, less, top)")
s.feed(b"\x1b[?1049l")
check(s.display()[0] == "ab" and s.alternate is None, "...and back to what was there")
s = vt.Screen(3, 10)
s.feed(b"1\r\n2\r\n3\r\n4")
check(s.display() == ["2", "3", "4"], "scrolling at the bottom")
s = vt.Screen(3, 5)
s.feed(b"abcdefg")
check(s.display() == ["abcde", "fg", ""], "wrapping at the edge")
s = vt.Screen(4, 10)
s.feed(b"\x1b[31;1mred\x1b[0m \x1b]0;title\x07ok")
check(s.display()[0] == "red ok", "colors and window titles are skipped, not printed")
s.feed(b"\x1b[2;3r\x1b[2;1Ha\r\nb\r\nc")
check(s.display()[1:3] == ["b", "c"] and s.display()[0] == "red ok", "a scroll region scrolls only itself")
s = vt.Screen(2, 10)
s.feed("héllo ✓".encode())
check(s.display()[0] == "héllo ✓", "UTF-8, split or not")
check(vt.key_bytes("A") == "a" and vt.key_bytes("A", shift=True) == "A" and vt.key_bytes("C", ctrl=True) == "\x03",
      "keys: letters, shift, ctrl")
check(vt.key_bytes("Arrow Up") == "\x1b[A" and vt.key_bytes("Enter") == "\r" and vt.key_bytes("1", shift=True) == "!",
      "keys: arrows, enter, shifted digits")
print("terminal screen checks passed" if not failed else f"{failed} terminal screen check(s) failed")
sys.exit(1 if failed else 0)
