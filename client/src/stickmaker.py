# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Makes a NanoBorealis install stick: the stock installer ISO, plus the owner's keys.

The ISO goes onto the stick byte for byte, streamed from the GitHub release (or read from a
local file) and hashed on the way. Then, at the next 1 MiB boundary past the image, comes a
small block with setup.json (the OpenRouter key and so on) that the installed system picks up
(see nanoborealis-setup-from-stick). The ISO itself stays untouched, so its checksum and the
installer's media check still hold. Everything is read back and verified.

Writing a whole disk needs administrator rights, so the app runs this module elevated:

    python stickmaker.py write --disk 2 --serial ABC123 --source latest|PATH.iso \
        --setup-file SETUP.json --progress PROGRESS.json

and watches PROGRESS.json. Nothing here imports Flet.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

REPO = "korxspyre/nanoborealis"
MAGIC = b"NANOBOREALIS-SETUP-V1\n"
ALIGN = 1 << 20
CHUNK = 4 << 20
MIN_GB, MAX_GB = 8, 512
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class StickError(Exception):
    pass


@dataclass
class Disk:
    id: str  # Windows disk number, or /dev/sdX on Linux
    name: str
    serial: str
    size: int
    sector: int = 512

    @property
    def label(self) -> str:
        return f"{self.name} ({self.size / 1e9:.0f} GB)"


# -- Finding sticks ---------------------------------------------------------------


def list_sticks() -> list[Disk]:
    """USB disks that are safe to offer: not the system or boot disk, 8-512 GB."""
    if sys.platform == "win32":
        script = ("Get-Disk | Select-Object Number,FriendlyName,SerialNumber,BusType,Size,IsBoot,IsSystem,"
                  "LogicalSectorSize | ConvertTo-Json -Compress")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             creationflags=_NO_WINDOW, timeout=60).stdout.strip()
        rows = json.loads(out) if out else []
        rows = rows if isinstance(rows, list) else [rows]
        disks = []
        for row in rows:
            size = int(row.get("Size") or 0)
            if row.get("BusType") != "USB" or row.get("IsBoot") or row.get("IsSystem"):
                continue
            if not MIN_GB * 1e9 <= size <= MAX_GB * 1e9:
                continue
            disks.append(Disk(str(row["Number"]), (row.get("FriendlyName") or "USB disk").strip(),
                              (row.get("SerialNumber") or "").strip(), size, int(row.get("LogicalSectorSize") or 512)))
        return disks
    out = subprocess.run(["lsblk", "-J", "-b", "-d", "-o", "PATH,SIZE,TRAN,TYPE,MODEL,SERIAL,LOG-SEC,MOUNTPOINTS"],
                         capture_output=True, text=True, timeout=30).stdout
    disks = []
    root_disk = _linux_root_disk()
    for dev in json.loads(out or '{"blockdevices": []}').get("blockdevices", []):
        size = int(dev.get("size") or 0)
        if dev.get("tran") != "usb" or dev.get("type") != "disk" or dev.get("path") == root_disk:
            continue
        if MIN_GB * 1e9 <= size <= MAX_GB * 1e9:
            disks.append(Disk(dev["path"], (dev.get("model") or "USB disk").strip(), (dev.get("serial") or "").strip(),
                              size, int(dev.get("log-sec") or 512)))
    return disks


def _linux_root_disk() -> str:
    try:
        source = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "/"], capture_output=True, text=True).stdout.strip()
        parent = subprocess.run(["lsblk", "-n", "-o", "PKNAME", source], capture_output=True, text=True).stdout.split()
        return f"/dev/{parent[0]}" if parent else ""
    except OSError:
        return ""


# -- The release ------------------------------------------------------------------


@dataclass
class Release:
    tag: str
    iso_name: str
    size: int
    sha256: str
    parts: list[str]  # download URLs, in order


def latest_release(channel: str = "stable") -> Release:
    """The newest installer for a build. Stable is the Latest release; dev
    installers are pre-releases tagged nanoborealis-<build>-... When a build has none yet, the
    stable installer is returned, and the stick's setup data moves the computer at first login."""
    data = None
    if channel == "dev":
        request = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases?per_page=50",
                                         headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            releases = json.loads(response.read())
        own = [r for r in releases if not r.get("draft")
               and r.get("tag_name", "").startswith(f"nanoborealis-{channel}-")
               and any(a["name"].endswith(".iso.sha256") for a in r.get("assets", []))]
        if own:
            data = max(own, key=lambda r: r.get("created_at", ""))
    if data is None:
        request = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                         headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read())
    assets = {a["name"]: a for a in data.get("assets", [])}
    sums = [name for name in assets if name.endswith(".iso.sha256")]
    if not sums:
        raise StickError(f"release {data.get('tag_name')} has no installer checksum")
    iso_name = sums[0][: -len(".sha256")]
    parts = sorted((n for n in assets if re.fullmatch(re.escape(iso_name) + r"\.part\d+", n)),
                   key=lambda n: int(n.rsplit("part", 1)[1]))
    if not parts:
        raise StickError(f"release {data.get('tag_name')} has no installer parts")
    with urllib.request.urlopen(assets[sums[0]]["browser_download_url"], timeout=30) as response:
        sha = response.read().decode().split()[0].lower()
    return Release(data.get("tag_name", "?"), iso_name, sum(assets[p]["size"] for p in parts), sha,
                   [assets[p]["browser_download_url"] for p in parts])


def _download(urls: list[str]) -> Iterator[bytes]:
    for url in urls:
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    while chunk := response.read(1 << 20):
                        yield chunk
                break
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(3)  # a part that failed before sending anything is safe to retry


def _read_file(path: str) -> Iterator[bytes]:
    with open(path, "rb") as source:
        while chunk := source.read(1 << 20):
            yield chunk


def setup_block(setup: dict | None) -> bytes:
    """The block written past the image; all zeros clears an old one when there's no setup."""
    if not setup:
        return bytes(ALIGN)
    payload = json.dumps(setup, separators=(",", ":")).encode()
    block = MAGIC + f"{len(payload)} {hashlib.sha256(payload).hexdigest()}\n".encode() + payload
    return block + bytes(-len(block) % 4096)


# -- Raw disk access --------------------------------------------------------------


class RawDisk:
    """Sector-aligned reads and writes on a whole disk."""

    def __init__(self, disk: Disk):
        self.sector = disk.sector or 512
        if sys.platform == "win32":
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateFileW.restype = wintypes.HANDLE
            k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            k32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
            k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            k32.ReadFile.argtypes = k32.WriteFile.argtypes
            k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                            ctypes.c_void_p]
            self._k32, self._wt = k32, wintypes
            path = rf"\\.\PhysicalDrive{disk.id}"
            self._h = k32.CreateFileW(path, 0xC0000000, 3, None, 3, 0, None)
            if self._h in (None, wintypes.HANDLE(-1).value):
                raise StickError(f"couldn't open {path} (Windows error {ctypes.get_last_error()})")
        else:
            self._fd = os.open(disk.id, os.O_RDWR | getattr(os, "O_SYNC", 0))

    def _seek(self, offset: int) -> None:
        if sys.platform == "win32":
            if not self._k32.SetFilePointerEx(self._h, offset, None, 0):
                raise StickError(f"seek failed (Windows error {ctypes.get_last_error()})")
        else:
            os.lseek(self._fd, offset, os.SEEK_SET)

    def write_at(self, offset: int, data: bytes) -> None:
        data = bytes(data) + bytes(-len(data) % self.sector)
        self._seek(offset)
        if sys.platform == "win32":
            done = self._wt.DWORD(0)
            buf = ctypes.create_string_buffer(data, len(data))
            if not self._k32.WriteFile(self._h, buf, len(data), ctypes.byref(done), None) or done.value != len(data):
                raise StickError(f"write at {offset} failed (Windows error {ctypes.get_last_error()})")
        else:
            view = memoryview(data)
            while view:
                view = view[os.write(self._fd, view):]

    def read_at(self, offset: int, length: int) -> bytes:
        padded = length + (-length % self.sector)
        self._seek(offset)
        if sys.platform == "win32":
            done = self._wt.DWORD(0)
            buf = ctypes.create_string_buffer(padded)
            if not self._k32.ReadFile(self._h, buf, padded, ctypes.byref(done), None):
                raise StickError(f"read at {offset} failed (Windows error {ctypes.get_last_error()})")
            return buf.raw[:min(length, done.value)]
        return os.read(self._fd, padded)[:length]

    def close(self) -> None:
        if sys.platform == "win32":
            self._k32.FlushFileBuffers(self._h)
            done = self._wt.DWORD(0)
            self._k32.DeviceIoControl(self._h, 0x00070140, None, 0, None, 0, ctypes.byref(done), None)  # rescan
            self._k32.CloseHandle(self._h)
        else:
            os.fsync(self._fd)
            os.close(self._fd)


def _find_disk(disk_id: str, serial: str) -> Disk:
    for disk in list_sticks():
        if serial and disk.serial == serial:
            return disk
        if not serial and disk.id == disk_id:
            return disk
    raise StickError("the stick isn't connected any more, or no longer looks like a safe USB stick")


def _clear(disk: Disk) -> Disk:
    """Remove the stick's partition table, so the OS lets go of it, then find it again."""
    if sys.platform == "win32":
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Clear-Disk -Number {int(disk.id)} -RemoveData -RemoveOEM -Confirm:$false"],
                       check=True, capture_output=True, creationflags=_NO_WINDOW, timeout=120)
    else:
        for part in subprocess.run(["lsblk", "-n", "-l", "-o", "PATH", disk.id],
                                   capture_output=True, text=True).stdout.split()[1:]:
            subprocess.run(["umount", part], capture_output=True)
        subprocess.run(["wipefs", "-a", disk.id], check=True, capture_output=True)
    for _ in range(60):  # Windows may re-register the stick under a new number
        time.sleep(1)
        try:
            return _find_disk(disk.id, disk.serial)
        except StickError:
            continue
    raise StickError("the stick didn't come back after clearing it")


# -- Writing ----------------------------------------------------------------------


Progress = Callable[[str, int, int, str], None]  # phase, done bytes, total bytes, message


def write_stick(disk: Disk, source: str, setup: dict | None, progress: Progress) -> str:
    """Write the ISO and setup block, verify both, and return the ISO's SHA-256."""
    if source == "latest":
        channel = (setup or {}).get("channel", "stable")
        progress("prepare", 0, 0, "Finding the latest NanoBorealis installer" if channel == "stable"
                 else f"Finding the latest NanoBorealis {channel} installer")
        release = latest_release(channel)
        total, expected, chunks = release.size, release.sha256, _download(release.parts)
        what = f"{release.iso_name} from {release.tag}"
    else:
        total, expected, chunks = os.path.getsize(source), None, _read_file(source)
        sums = Path(source + ".sha256")
        if sums.exists():
            expected = sums.read_text().split()[0].lower()
        what = os.path.basename(source)
    if total + 2 * ALIGN > disk.size:
        raise StickError("this stick is too small for NanoBorealis")

    progress("prepare", 0, total, f"Clearing {disk.label}")
    disk = _clear(disk)
    raw = RawDisk(disk)
    try:
        digest = hashlib.sha256()
        buffer = bytearray()
        first: bytes | None = None
        offset = 0
        written = 0
        for piece in chunks:
            digest.update(piece)
            buffer += piece
            while len(buffer) >= CHUNK:
                block = bytes(buffer[:CHUNK])
                del buffer[:CHUNK]
                if first is None:
                    first = block  # the partition table goes on last, so a half-written stick never looks valid
                else:
                    raw.write_at(offset, block)
                offset += CHUNK
                written += CHUNK
                progress("write", written, total, f"Writing {what}")
        if buffer:
            if first is None:
                first = bytes(buffer)
            else:
                raw.write_at(offset, bytes(buffer))
            written += len(buffer)
        if first is None:
            raise StickError("the installer image is empty")
        raw.write_at(0, first)
        got = digest.hexdigest()
        if written != total:
            raise StickError(f"got {written} bytes instead of {total}")
        if expected and got != expected:
            raise StickError("the download doesn't match the release checksum; nothing was left half-trusted, "
                             "but write the stick again")

        block_at = -(-total // ALIGN) * ALIGN
        block = setup_block(setup)
        raw.write_at(block_at, block)

        progress("verify", 0, total, "Reading the stick back to check it")
        check = hashlib.sha256()
        position = 0
        while position < total:
            length = min(CHUNK, total - position)
            check.update(raw.read_at(position, length))
            position += length
            progress("verify", position, total, "Reading the stick back to check it")
        if check.hexdigest() != got:
            raise StickError("the stick doesn't read back the same as what was written; try another stick")
        if raw.read_at(block_at, len(block)) != block:
            raise StickError("the setup block didn't read back correctly")
    finally:
        raw.close()
    progress("done", total, total, "The stick is ready")
    return got


# -- Running elevated -------------------------------------------------------------


def launch_elevated(disk: Disk, source: str, setup: dict | None, progress_file: str) -> str:
    """Start the write in an elevated process. Returns the path of the setup handoff file."""
    fd, setup_file = tempfile.mkstemp(prefix="nanoborealis-setup-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handoff:
        json.dump(setup or {}, handoff)
    args = ["write", "--disk", disk.id, "--serial", disk.serial, "--source", source,
            "--setup-file", setup_file, "--progress", progress_file]
    # A frozen (PyInstaller) app has no script to hand Python: it runs its own executable with
    # `write ...`, which the app's entry point sends here instead of opening a window.
    frozen = getattr(sys, "frozen", False)
    script = [] if frozen else [str(Path(__file__).resolve())]
    if sys.platform == "win32":
        python = sys.executable
        if not frozen and python.endswith("python.exe"):
            python = python.replace("python.exe", "pythonw.exe")
        params = subprocess.list2cmdline([*script, *args])
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", python, params, None, 0)
        if result <= 32:
            os.remove(setup_file)
            raise StickError("Windows didn't start the writer; the permission prompt may have been declined")
    else:
        subprocess.Popen(["pkexec", sys.executable, *script, *args])
    return setup_file


def _cli(argv: list[str]) -> int:
    opts = dict(zip(argv[1::2], argv[2::2]))
    progress_file = opts["--progress"]

    last = ["", 0.0]  # the last update written: phase, time

    def report(phase: str, done: int, total: int, message: str, error: str = "") -> None:
        # Windows refuses to replace a file while the app has it open to read, so a progress
        # update can collide with the app polling. That must never end the write: retry briefly,
        # and skip an in-between update rather than fail. The final result keeps trying.
        final = phase in ("done", "error")
        now = time.monotonic()
        if not final and phase == last[0] and now - last[1] < 0.25:
            return  # the app polls twice a second; more updates only add collisions
        last[0], last[1] = phase, now
        tmp = progress_file + ".tmp"
        for attempt in range(400 if final else 8):
            try:
                with open(tmp, "w", encoding="utf-8") as out:
                    json.dump({"phase": phase, "done": done, "total": total, "message": message,
                               "error": error, "time": time.time()}, out)
                os.replace(tmp, progress_file)
                return
            except PermissionError:
                time.sleep(0.025)

    # A record of each run that outlives the progress file, for when a write fails. It holds
    # steps and errors only, never the setup (which carries the key).
    log_path = os.path.join(os.path.dirname(progress_file), "nanoborealis-stick-writer.log")

    def log(line: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as out:
                out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
        except OSError:
            pass

    def logged(phase: str, done: int, total: int, message: str, error: str = "") -> None:
        if phase != "write" and phase != "verify" or done in (0, total):
            log(f"{phase}: {message}{' ' + error if error else ''}")
        report(phase, done, total, message, error)

    log(f"writer started: disk {opts.get('--disk')} serial {opts.get('--serial')} source {opts.get('--source')} "
        f"python {sys.version.split()[0]} admin {bool(ctypes.windll.shell32.IsUserAnAdmin()) if sys.platform == 'win32' else os.geteuid() == 0}")
    try:
        with open(opts["--setup-file"], encoding="utf-8") as handoff:
            setup = json.load(handoff)
        os.remove(opts["--setup-file"])  # the key doesn't stay on this computer
        disk = _find_disk(opts["--disk"], opts.get("--serial", ""))
        write_stick(disk, opts["--source"], setup or None, logged)
        return 0
    except Exception as e:  # report every failure to the app, which is waiting on this file
        import traceback
        log("failed:\n" + traceback.format_exc())
        report("error", 0, 0, "", f"{str(e) or type(e).__name__} (details: {log_path})")
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "write":
        sys.exit(_cli(sys.argv[1:]))
    for stick in list_sticks():
        print(f"{stick.id}\t{stick.label}\tserial {stick.serial}")
