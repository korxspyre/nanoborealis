# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The app as part of the NanoBorealis computer it runs on.

On a NanoBorealis computer the app is the agent's own window: it comes with the OS, pairs with the
computer by itself, and should behave like the rest of the desktop. One copy runs at a time (the
launcher brings it forward instead), it says when the agent is done while you're elsewhere, and it
uses the system's fonts. None of this applies on phones and PCs, where every function here does
nothing and says so.

Nothing here imports Flet.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pairing

APP_ID = "nanoborealis"  # the app menu entry, and the window's id (see build_files/nanoborealis-window.c)
_lock = None


def is_host() -> bool:
    """True on a NanoBorealis computer itself: its pairing service offers the app a local socket."""
    return os.name == "posix" and os.path.exists(pairing.LOCAL_SOCKET)


def comes_with_os() -> bool:
    """The copy the OS ships in /usr: it's updated with the OS, never by itself."""
    return os.path.abspath(__file__).startswith("/usr/")


def forget_window_shim() -> None:
    """/usr/bin/nanoborealis-app preloads a fix into Flet's window program. Once the window is up,
    programs the app starts (a browser, a file manager) shouldn't get it too."""
    kept = [p for p in os.environ.get("LD_PRELOAD", "").split(":") if p and "nanoborealis-window" not in p]
    if kept:
        os.environ["LD_PRELOAD"] = ":".join(kept)
    else:
        os.environ.pop("LD_PRELOAD", None)


def _runtime_dir() -> Path | None:
    path = os.environ.get("XDG_RUNTIME_DIR")
    return Path(path) if path and os.path.isdir(path) else None


def claim_single_instance() -> bool:
    """True when this is the only copy running for this person. Otherwise the running copy's window
    is brought forward, and this one should quit."""
    global _lock
    runtime = _runtime_dir()
    if runtime is None:
        return True
    import fcntl

    _lock = open(runtime / "nanoborealis-app.lock", "w")
    try:
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        _lock.close()
        _lock = None
        show_running_window()
        return False


def _kwin_script(name: str, source: str) -> bool:
    """Run a one-off KWin script (the window manager's own way to find and raise a window)."""
    runtime, gdbus = _runtime_dir(), shutil.which("gdbus")
    if runtime is None or gdbus is None:
        return False
    path = runtime / f"{name}.js"
    path.write_text(source)
    try:
        loaded = subprocess.run([gdbus, "call", "--session", "--dest", "org.kde.KWin", "--object-path", "/Scripting",
                                 "--method", "org.kde.kwin.Scripting.loadScript", _text(str(path)), _text(name)],
                                capture_output=True, text=True, timeout=5)
        number = re.search(r"int32 (-?\d+)", loaded.stdout or "")  # gdbus answers "(int32 5,)"
        if loaded.returncode != 0 or number is None or int(number.group(1)) < 0:
            return False
        subprocess.run([gdbus, "call", "--session", "--dest", "org.kde.KWin", "--object-path",
                        f"/Scripting/Script{number.group(1)}", "--method", "org.kde.kwin.Script.run"],
                       capture_output=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        subprocess.run([gdbus, "call", "--session", "--dest", "org.kde.KWin", "--object-path", "/Scripting",
                        "--method", "org.kde.kwin.Scripting.unloadScript", _text(name)], capture_output=True, timeout=5)
        path.unlink(missing_ok=True)


def show_running_window() -> bool:
    return _kwin_script("nanoborealis-raise", f"""
for (const w of workspace.windowList()) {{
    if (w.resourceClass === "{APP_ID}" && w.normalWindow) {{
        w.minimized = false;
        workspace.activeWindow = w;
        break;
    }}
}}
""")


def notify(title: str, body: str) -> None:
    """A desktop notification from the app, grouped under it in KDE's notification history."""
    gdbus = shutil.which("gdbus")
    if gdbus is None or not is_host():
        return
    body = body if len(body) <= 240 else body[:237] + "..."
    try:
        subprocess.Popen([gdbus, "call", "--session", "--dest", "org.freedesktop.Notifications",
                          "--object-path", "/org/freedesktop/Notifications",
                          "--method", "org.freedesktop.Notifications.Notify",
                          _text("NanoBorealis"), "uint32 0", _text(APP_ID), _text(title), _text(body), "@as []",
                          f"{{'desktop-entry': <{_text(APP_ID)}>}}", "int32 8000"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _text(value: str) -> str:
    """A string as gdbus reads its arguments (GVariant text), whatever it contains."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def system_font() -> str | None:
    """The desktop's UI font family (KDE's General/font), e.g. "Noto Sans"."""
    for path in (Path.home() / ".config" / "kdeglobals", Path("/etc/xdg/kdeglobals")):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        match = re.search(r"^\[General\][^\[]*?^font=([^,\n]+)", text, re.M | re.S)
        if match:
            return match.group(1).strip()
    return "Noto Sans" if is_host() else None
