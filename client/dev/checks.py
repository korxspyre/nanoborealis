# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""What the end-to-end checks share: reporting a check, and pairing through a test-mode service
(NANOBOREALIS_REMOTE_TEST_PIN_FILE, where it writes each PIN instead of showing it)."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pairing import Machine, Pairing  # noqa: E402


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


def read_pin(path: str, after: float) -> str:
    for _ in range(100):
        if os.path.exists(path) and os.path.getmtime(path) >= after:
            return open(path).read().strip()
        time.sleep(0.1)
    raise SystemExit("FAIL the PIN never appeared")


def pair(host: str, port: int, pin_file: str, name: str) -> Machine:
    session = Pairing(host, port, name)
    session.start()
    asked = time.time() - 1
    session.reveal()
    return session.finish(read_pin(pin_file, asked))
