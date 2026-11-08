# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Hands: lend this device to the agents, inside a sandbox, so they can work here without being able
to break it.

The app keeps asking the NanoBorealis computer it's paired with for work (POST /nanoborealis/hands/poll),
runs each job in this device's box and answers with the result. A box is a sandbox that sees only the
folders shared with the agents (by default ~/NanoBorealis Work) and the network:

    Windows  a WSL2 system of its own ("NanoBorealisHands"): no Windows drives except the shared
             folders, and no starting Windows programs (wsl.conf turns automount and interop off)
    Linux    bubblewrap: the system read-only, a private home, the shared folders read-write

Nothing else on the device is reachable from the box, so a mistake (or a bad idea) stays inside it.

    python hands.py serve [--machine NAME] [--folder PATH ...]   lend this device until stopped
    python hands.py box                                          set up the box and say what it is
    python hands.py try CMD...                                   run one command in the box here
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pairing  # noqa: E402

OUTPUT_LIMIT = 400_000  # the end of a job's output that's sent back
DISTRO = "NanoBorealisHands"
UBUNTU = "https://cloud-images.ubuntu.com/wsl/releases/24.04/current/"
UBUNTU_FILE = "ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz"


def default_folders() -> list[Path]:
    return [Path.home() / "NanoBorealis Work"]


def home() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "NanoBorealis" / "hands"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _text(raw: bytes) -> str:
    """wsl.exe writes UTF-16 to pipes; everything else, UTF-8."""
    if raw[:2] in (b"\xff\xfe",) or (len(raw) > 1 and raw[1:2] == b"\x00"):
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode(errors="replace")


class Box:
    kind = "none"

    def __init__(self, folders: list[Path]):
        self.folders = [f.resolve() for f in folders]
        for folder in self.folders:
            folder.mkdir(parents=True, exist_ok=True)

    def mounts(self) -> list[tuple[Path, str]]:
        """Each shared folder and where the box sees it: /work/<name>."""
        return [(f, f"/work/{f.name.replace(' ', '-')}") for f in self.folders]

    def describe(self) -> dict:
        return {"kind": self.kind, "folders": [inside for _, inside in self.mounts()], "network": True,
                "os": sys.platform}

    def ensure(self, progress=print) -> None:
        raise NotImplementedError

    def run(self, command: str, timeout: int) -> dict:
        raise NotImplementedError

    def _run(self, argv: list[str], timeout: int, stdin: bytes | None = None) -> dict:
        try:
            done = subprocess.run(argv, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return {"exit": done.returncode, "timed_out": False, "output": _text(done.stdout)[-OUTPUT_LIMIT:]}
        except subprocess.TimeoutExpired as e:
            return {"exit": None, "timed_out": True, "output": _text(e.stdout or b"")[-OUTPUT_LIMIT:]}


class WslBox(Box):
    """A WSL2 system of its own that sees only the shared folders."""
    kind = "wsl2"

    def wsl(self, *args: str, timeout: int = 600, stdin: bytes | None = None) -> dict:
        return self._run(["wsl.exe", *args], timeout, stdin)

    def exists(self) -> bool:
        listed = self.wsl("--list", "--quiet", timeout=60)
        return DISTRO in listed["output"].replace("\x00", "").split()

    def wsl_conf(self) -> str:
        lines = ["[automount]", "enabled=false", "mountFsTab=true", "", "[interop]", "enabled=false",
                 "appendWindowsPath=false", "", "[user]", "default=agent", ""]
        return "\n".join(lines)

    def fstab(self) -> str:
        rows = []
        for folder, inside in self.mounts():
            rows.append(f"{str(folder).replace(' ', chr(92) + '040')} {inside} drvfs metadata,uid=1000,gid=1000 0 0")
        return "\n".join(rows) + "\n"

    def ensure(self, progress=print) -> None:
        if not self.exists():
            tar = home() / UBUNTU_FILE
            if not tar.exists():
                progress("downloading the box's Linux system (Ubuntu 24.04, about 350 MB)")
                sums = urllib.request.urlopen(UBUNTU + "SHA256SUMS", timeout=60).read().decode()
                want = next(line.split()[0] for line in sums.splitlines() if line.strip().endswith(UBUNTU_FILE))
                part = tar.with_suffix(".part")
                urllib.request.urlretrieve(UBUNTU + UBUNTU_FILE, part)
                digest = hashlib.sha256(part.read_bytes()).hexdigest()
                if digest != want:
                    part.unlink()
                    raise RuntimeError("the box's download didn't match its published checksum")
                part.replace(tar)
            progress("making the box (a WSL2 system of its own)")
            done = self.wsl("--import", DISTRO, str(home() / DISTRO), str(tar), "--version", "2", timeout=1800)
            if done["exit"] != 0:
                raise RuntimeError(f"wsl --import failed: {done['output'][-400:]}")
        # Its settings every time, so a change to the shared folders takes effect.
        script = ("set -e; if ! id agent >/dev/null 2>&1; then "
                  "old=$(getent passwd 1000 | cut -d: -f1); "
                  "if [ -n \"$old\" ]; then usermod -l agent -d /home/agent -m -s /bin/bash \"$old\"; "
                  "groupmod -n agent \"$old\" 2>/dev/null || true; else useradd -m -s /bin/bash -u 1000 agent; fi; fi; "
                  "echo 'agent ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/agent; chmod 440 /etc/sudoers.d/agent; "
                  f"echo {base64.b64encode(self.wsl_conf().encode()).decode()} | base64 -d > /etc/wsl.conf; "
                  f"echo {base64.b64encode(self.fstab().encode()).decode()} | base64 -d > /etc/fstab; "
                  + " ".join(f"mkdir -p '{inside}';" for _, inside in self.mounts()))
        # base64, like every command: wsl.exe re-splits its arguments and loses quotes on the way.
        done = self.wsl("-d", DISTRO, "-u", "root", "--", "bash", "-c",
                        f"echo {base64.b64encode(script.encode()).decode()} | base64 -d | bash", timeout=300)
        if done["exit"] != 0:
            raise RuntimeError(f"couldn't set the box up: {done['output'][-400:]}")
        self.wsl("--terminate", DISTRO, timeout=60)  # wsl.conf and fstab apply from the next start

    def check(self) -> dict:
        """What the box can see: proof the sandbox holds (no C: drive, no Windows programs)."""
        return self.run("ls /mnt; ls /work; command -v cmd.exe powershell.exe || echo no-windows-programs", 60)

    def run(self, command: str, timeout: int) -> dict:
        encoded = base64.b64encode(command.encode()).decode()
        return self.wsl("-d", DISTRO, "-u", "agent", "--cd", "~", "--", "bash", "-lc",
                        f"echo {encoded} | base64 -d | bash", timeout=timeout)


class BwrapBox(Box):
    """bubblewrap on Linux: the system read-only, a private home, the shared folders read-write."""
    kind = "bubblewrap"

    def ensure(self, progress=print) -> None:
        if not shutil.which("bwrap"):
            raise RuntimeError("bubblewrap isn't installed (it comes with Flatpak; or install the bubblewrap package)")
        (home() / "box-home").mkdir(exist_ok=True)

    def run(self, command: str, timeout: int) -> dict:
        argv = ["bwrap", "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc", "--symlink", "usr/bin", "/bin",
                "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/sbin", "/sbin",
                "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--bind", str(home() / "box-home"), "/home/agent",
                "--setenv", "HOME", "/home/agent", "--chdir", "/home/agent", "--unshare-all", "--share-net",
                "--die-with-parent", "--new-session"]
        for folder, inside in self.mounts():
            argv += ["--bind", str(folder), inside]
        return self._run([*argv, "bash", "-lc", command], timeout)


def make_box(folders: list[Path]) -> Box:
    if os.name == "nt":
        return WslBox(folders)
    if sys.platform.startswith("linux"):
        return BwrapBox(folders)
    raise RuntimeError("this device can't lend its hands yet (Windows and Linux can)")


def serve(machine: pairing.Machine, box: Box, stop=lambda: False, progress=print) -> None:
    """Ask the paired computer for work and do it in the box, until stop() says so."""
    box.ensure(progress)
    progress(f"lending this device's hands to {machine.name} ({box.kind}; sees {', '.join(box.describe()['folders'])})")
    headers = {"X-NanoBorealis-Device": machine.password}
    while not stop():
        try:
            status, answer, _ = pairing.call(machine.host, machine.port, "POST", "/nanoborealis/hands/poll",
                                             context=machine.context(), body={"box": box.describe()},
                                             headers=headers, timeout=60)
        except pairing.PairingError as e:
            progress(f"can't reach {machine.name}: {e}; trying again in 15 seconds")
            time.sleep(15)
            continue
        if status != 200:
            progress(f"{machine.name} said HTTP {status}: {answer.get('error', answer)}")
            time.sleep(30)
            continue
        job = answer.get("job")
        if not job:
            continue
        progress(f"job {job['id']}: {job['command'][:120]}")
        result = box.run(str(job["command"]), int(job.get("timeout") or 300))
        progress(f"job {job['id']}: exit {result['exit']}{' (timed out)' if result['timed_out'] else ''}")
        try:
            pairing.call(machine.host, machine.port, "POST", "/nanoborealis/hands/done", context=machine.context(),
                         body={"id": job["id"], **result}, headers=headers, timeout=60)
        except pairing.PairingError as e:
            progress(f"couldn't send job {job['id']}'s result: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", action="append", type=Path, help="a folder to share with the agents (repeatable)")
    parser.add_argument("--machine")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("serve")
    sub.add_parser("box")
    t = sub.add_parser("try")
    t.add_argument("words", nargs="+")
    args = parser.parse_args()
    box = make_box(args.folder or default_folders())
    if args.action == "box":
        box.ensure()
        print(json.dumps(box.describe(), indent=2))
        if isinstance(box, WslBox):
            print(box.check()["output"])
        return 0
    if args.action == "try":
        box.ensure()
        result = box.run(" ".join(args.words), 300)
        sys.stdout.write(result["output"])
        return result["exit"] or 0
    import devlink
    serve(devlink.pick(args.machine), box)
    return 0


if __name__ == "__main__":
    sys.exit(main())
