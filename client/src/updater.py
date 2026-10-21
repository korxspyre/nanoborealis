# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Updates for the packaged app, from its GitHub releases, on one of two channels:

    stable   vX.Y.Z          releases everyone gets: the OS, its installer and the app together
    dev      vX.Y.Z-dev.N    built from every change to the app (pre-releases)

Releases before 0.7.1 were the app's own, tagged client-vX.Y.Z; they still count.

A channel offers its own builds and every more stable one, so dev gets everything. Versions order
as semver does: 0.3.0-dev.9 < 0.3.0-rc.2 < 0.3.0. (The old -rc builds of the retired testing
channel still order and count as dev.)

Only the packaged app updates itself; running from source updates with git. An update is found,
downloaded, checked against the release's SHA256SUMS, and only then installed:

- Windows: the installer runs silently, replaces the app, and reopens it.
- Linux: the archive's install.sh runs, then the app reopens.
- macOS: the disk image opens, to drag the new app over the old one.
- Android: Android's own installer opens on the downloaded APK (the first time, Android asks to
  allow NanoBorealis to install apps).

Nothing here imports Flet.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from typing import Callable

from version import VERSION

REPO = "korxspyre/nanoborealis"
ANDROID = hasattr(sys, "getandroidapilevel")  # Python built for Android, as the app's APK carries
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(rc|dev)\.(\d+))?$")
CHANNELS = {"stable": (None,), "dev": (None, "rc", "dev")}
RANK = {"dev": 0, "rc": 1, None: 2}  # a release outranks its own candidates and dev builds


class UpdateError(Exception):
    pass


@dataclass
class Update:
    version: str
    notes_url: str
    asset_name: str
    asset_url: str
    size: int
    sums_url: str


def parse(version: str) -> tuple[int, int, int, int, int] | None:
    """A sort key: major, minor, patch, then stable above rc above dev, then the build number."""
    match = VERSION_RE.match(version)
    if not match:
        return None
    major, minor, patch, kind, number = match.groups()
    return int(major), int(minor), int(patch), RANK[kind], int(number or 0)


def channel_of(version: str) -> str:
    """The channel a build came from: the one it keeps following unless the owner changes it."""
    match = VERSION_RE.match(version)
    kind = match.group(4) if match else None
    return {"rc": "dev", "dev": "dev"}.get(kind, "stable")


def can_update() -> bool:
    """Only a packaged build knows its version and can replace itself (on Android, the APK)."""
    return (bool(getattr(sys, "frozen", False)) or ANDROID) and parse(VERSION) is not None


def close_orphaned_windows() -> int:
    """Close windows that an earlier version of this app left on screen, frozen, and return how
    many. Versions before the one that fixed it ended themselves for an update without closing
    their window, which is Flet's own program (flet.exe, under ~/.flet/client): one whose app
    process is gone can only ever show a frozen screen. Windows only, packaged app only."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return 0
    script = ("Get-CimInstance Win32_Process -Filter \"Name='flet.exe'\" | Where-Object { "
              "$_.ExecutablePath -like \"$env:USERPROFILE\\.flet\\client\\*\" -and "
              "-not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue) } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=60,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return len(result.stdout.split())


def clean_old_windows() -> int:
    """Delete the copies of Flet's window program that earlier versions of this app unpacked, and
    return the bytes freed. Each packaged build unpacks its own (about 100 MB, under
    ~/.flet/client, named by a fingerprint of the build's copy), and Flet only clears them out
    after 30 days unused, so every update would leave one behind for a month. Flet marks the copy
    it runs from each time (.last-used), so the newest stays; a copy still running fails to be
    renamed and stays too. Only in a packaged app: running from source uses Flet's own copy."""
    import secrets
    import shutil
    from pathlib import Path
    if not getattr(sys, "frozen", False):
        return 0
    base = Path.home() / ".flet" / "client"

    def last_used(folder: Path) -> float:
        try:
            marker = folder / ".last-used"
            return max(marker.stat().st_mtime if marker.exists() else 0, folder.stat().st_mtime)
        except OSError:
            return 0

    try:
        copies = [d for d in base.iterdir() if d.is_dir() and re.fullmatch(r"flet-desktop-\w+-[\d.]+-[0-9a-f]{12}", d.name)]
    except OSError:
        return 0
    freed = 0
    for old in sorted(copies, key=last_used)[:-1]:
        size = sum(f.stat().st_size for f in old.rglob("*") if f.is_file())
        trash = base / f".trash-{secrets.token_hex(4)}"
        try:
            old.rename(trash)  # fails while a window runs from it (Windows)
        except OSError:
            continue
        shutil.rmtree(trash, ignore_errors=True)
        freed += size
    return freed


def assets_for_platform(version: str) -> tuple[str, str]:
    """This platform's file in a release: dev builds name it with their version; a release keeps
    one name for good, so the README's releases/latest/download links always fetch the newest."""
    if ANDROID:
        return f"NanoBorealis-{version}-android.apk", "NanoBorealis-android.apk"
    if sys.platform == "win32":
        return f"NanoBorealis-Setup-{version}.exe", "NanoBorealis-Setup.exe"
    if sys.platform == "darwin":
        return f"NanoBorealis-{version}-macos.dmg", "NanoBorealis-macos.dmg"
    return f"NanoBorealis-{version}-linux-x86_64.tar.gz", "NanoBorealis-linux-x86_64.tar.gz"


def newest(releases: list[dict], channel: str) -> tuple[str, dict] | None:
    """The newest release a channel accepts, as (version, release)."""
    kinds = CHANNELS.get(channel, CHANNELS["stable"])
    best: tuple[tuple, str, dict] | None = None
    for release in releases:
        tag = release.get("tag_name", "")
        prefix = next((p for p in ("v", "client-v") if tag.startswith(p)), None)
        if release.get("draft") or prefix is None:
            continue
        version = tag[len(prefix):]
        match = VERSION_RE.match(version)
        if not match or match.group(4) not in kinds:
            continue
        key = parse(version)
        if best is None or key > best[0]:
            best = (key, version, release)
    return (best[1], best[2]) if best else None


def check(channel: str = "stable") -> Update | None:
    """The newest release on `channel`, if it's newer than this app and has a file for this platform."""
    current = parse(VERSION)
    if current is None:
        return None
    request = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases?per_page=100",
                                     headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        releases = json.loads(response.read())
    found = newest(releases, channel)
    if found is None or parse(found[0]) <= current:
        return None
    version, release = found
    assets = {a["name"]: a for a in release.get("assets", [])}
    name = next((n for n in assets_for_platform(version) if n in assets), None)
    if name is None or "SHA256SUMS" not in assets:
        return None
    return Update(version, release.get("html_url", ""), name, assets[name]["browser_download_url"],
                  int(assets[name].get("size") or 0), assets["SHA256SUMS"]["browser_download_url"])


def download(update: Update, progress: Callable[[int, int], None] | None = None, folder: str | None = None) -> str:
    """Download the update and check it against the release checksums. Returns the file path."""
    with urllib.request.urlopen(update.sums_url, timeout=30) as response:
        sums = response.read().decode()
    expected = next((line.split()[0].lower() for line in sums.splitlines()
                     if line.strip().endswith(update.asset_name)), None)
    if not expected:
        raise UpdateError(f"the release lists no checksum for {update.asset_name}")
    folder = tempfile.mkdtemp(prefix="nanoborealis-update-", dir=folder)
    path = os.path.join(folder, update.asset_name)
    digest = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(update.asset_url, timeout=60) as response, open(path, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, update.size)
    if digest.hexdigest() != expected:
        os.remove(path)
        raise UpdateError("the download doesn't match the release checksum; nothing was installed")
    return path


def android_activity():
    """The app's Android activity, through pyjnius (only in the APK)."""
    from jnius import autoclass
    return autoclass(os.environ["MAIN_ACTIVITY_HOST_CLASS_NAME"]).mActivity


def android_folder() -> str:
    """Where an APK is downloaded: the app's cache, which its FileProvider (Flet's build has one,
    as <package>.provider, covering the cache) can hand to Android's installer."""
    folder = os.path.join(android_activity().getCacheDir().getAbsolutePath(), "updates")
    os.makedirs(folder, exist_ok=True)
    return folder


def install_android(path: str) -> bool:
    """Open Android's installer on a downloaded APK. False when Android first needs the owner to
    allow NanoBorealis to install apps: its settings page for that opens instead."""
    from jnius import autoclass
    activity = android_activity()
    intent_class = autoclass("android.content.Intent")
    build = autoclass("android.os.Build$VERSION")
    if build.SDK_INT >= 26 and not activity.getPackageManager().canRequestPackageInstalls():
        uri = autoclass("android.net.Uri").parse(f"package:{activity.getPackageName()}")
        intent = intent_class(autoclass("android.provider.Settings").ACTION_MANAGE_UNKNOWN_APP_SOURCES, uri)
        activity.startActivity(intent)
        return False
    uri = autoclass("androidx.core.content.FileProvider").getUriForFile(
        activity, f"{activity.getPackageName()}.provider", autoclass("java.io.File")(path))
    intent = intent_class(intent_class.ACTION_VIEW)
    intent.setDataAndType(uri, "application/vnd.android.package-archive")
    intent.addFlags(intent_class.FLAG_GRANT_READ_URI_PERMISSION | intent_class.FLAG_ACTIVITY_NEW_TASK)
    activity.startActivity(intent)
    return True


def install_log() -> str:
    """Where the Windows installer writes what it did, for when an update doesn't take."""
    return os.path.join(tempfile.gettempdir(), "nanoborealis-update.log")


def start_outside_jobs(command_line: str) -> bool:
    """Windows: start a command through WMI (Win32_Process.Create), so it isn't this process's child
    and is in none of its job objects. True when WMI started it."""
    try:
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
             "-Arguments @{CommandLine=$env:NANOBOREALIS_HANDOFF}; exit [int]$r.ReturnValue"],
            env={**os.environ, "NANOBOREALIS_HANDOFF": command_line}, capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def install(path: str) -> None:
    """Start installing a verified download. The caller quits the app right after."""
    if sys.platform == "win32":
        # Silent install over this one, started only once this app has fully exited: run while it
        # was still going, the installer left its files in place and reopened the old version,
        # which then updated again, in a loop. A hidden PowerShell waits for this process and any
        # window of Flet's still running from an earlier copy, then runs the installer with a log;
        # the installer reopens the app.
        script = (f"Wait-Process -Id {os.getpid()} -Timeout 120 -ErrorAction SilentlyContinue; "
                  "Get-CimInstance Win32_Process -Filter \"Name='flet.exe'\" | Where-Object { "
                  "-not (Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue) } | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
                  "Start-Sleep -Seconds 1; "
                  f"Start-Process -FilePath '{path}' -ArgumentList '/SILENT','/SUPPRESSMSGBOXES','/NORESTART',"
                  f"'/CLOSEAPPLICATIONS','/LOG=\"{install_log()}\"'")
        # Encoded: quotes on a PowerShell command line don't survive Windows' argument quoting.
        import base64
        encoded = base64.b64encode(script.encode("utf-16-le")).decode()
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded]
        # The helper must outlive this app. Started as its child, it shares any job object the app was
        # launched in, and a job that closes with the app and forbids breaking away (some launchers
        # make those) ends the helper the moment the app quits: the app closed, and nothing
        # installed. Windows' WMI service starts it instead, as nobody's child, outside every job.
        if not start_outside_jobs(subprocess.list2cmdline(command)):
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            try:
                subprocess.Popen(command, close_fds=True,
                                 creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
            except OSError:
                subprocess.Popen(command, close_fds=True, creationflags=flags)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        folder = os.path.dirname(path)
        with tarfile.open(path) as archive:
            archive.extractall(folder, filter="data")
        top = next(os.path.join(folder, d) for d in os.listdir(folder)
                   if os.path.isdir(os.path.join(folder, d)))
        launcher = os.path.join(os.path.expanduser("~"), ".local/share/nanoborealis/NanoBorealis")
        subprocess.Popen(["sh", "-c", f'sleep 2 && "{top}/install.sh" && exec "{launcher}"'],
                         start_new_session=True)
