# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""One copy of the app per person on a desktop or laptop (Windows, macOS, Linux).

Two copies share this device's saved identity, so each would keep taking the agent's connection from the other.
The first copy holds a port on this computer's loopback address; a second copy finds it taken, asks the first
to come to the front, and quits. (On a NanoBorealis computer itself, host.claim_single_instance does the same.)
"""

from __future__ import annotations

import socket
import sys
import threading
from collections.abc import Callable

PORT = 47621
_server: socket.socket | None = None
on_show: Callable[[], None] | None = None  # set by the running app: bring its window forward


def claim(port: int = PORT) -> bool:
    """True when this is the only copy. Otherwise the running copy is asked to show itself, and this one should quit."""
    global _server
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # nobody else may share the port
    try:
        server.bind(("127.0.0.1", port))
        server.listen(4)
    except OSError:
        server.close()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as other:
                other.sendall(b"show\n")
        except OSError:
            pass
        return False
    _server = server
    threading.Thread(target=_serve, args=(server,), daemon=True).start()
    return True


def _serve(server: socket.socket) -> None:
    while True:
        try:
            conn, _addr = server.accept()
        except OSError:
            return
        with conn:
            try:
                conn.settimeout(2)
                said = conn.recv(16)
            except OSError:
                continue
        if said.startswith(b"show") and on_show is not None:
            try:
                on_show()
            except Exception:
                pass
