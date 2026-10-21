# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Lends this device to its NanoBorealis computer's pool, with the owner's consent: runs a pool node
here, so the pool's models can be spread over this device's memory and GPU too.

The node is exo, of exactly the version the pool runs (exo nodes only pool with their own version)
and in the pool's own namespace, both from the paired computer (admin.pool_info). How it runs:
  - where Podman is (Linux): the pool's own node image, its NVIDIA build when this device's GPU
    can run it, told to report the GPU's memory rather than the computer's;
  - on macOS (Apple silicon): a node bundle published for that exo version, checked against its
    published SHA-256 before it runs;
  - on Windows 11: inside WSL2, as a WSL system of its own made from that same node image (see
    "Windows" below).
It runs while the app is open. Nothing here imports Flet.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# A device can lend its GPU and its processor separately (two consents), each as a node of its own:
# the GPU node holds models in the GPU's memory, the processor node in the computer's. Slot 0 is
# the first node (exo's own ports), slot 1 the second: its own zenoh and API ports, the same
# discovery port (exo shares it between the nodes on a computer). Each slot has its own container,
# WSL system and home, so the two never touch each other's models.
SLOT_PORTS = {0: (52414, 52415), 1: (52424, 52425)}  # zenoh (TCP), API (TCP)
EXO_API = "http://127.0.0.1:52415"
CONTAINER = "nanoborealis-pool"
VOLUME = "nanoborealis-pool"
PORTS = "52413-52415 and 52424-52425, and 49152-65535 for model layers"


def slot_name(base: str, slot: int) -> str:
    return base if slot == 0 else f"{base}-{slot + 1}"


def slot_args(slot: int) -> list[str]:
    """exo's arguments for a node in this slot (nothing extra for the first)."""
    if slot == 0:
        return []
    zenoh, api = SLOT_PORTS[slot]
    return ["--zenoh-port", str(zenoh), "--api-port", str(api)]
MIN_COMPUTE = (7, 5)  # CUDA 13 runs on Turing (RTX 20 series) and newer
MIN_DRIVER = 580  # the first NVIDIA driver series with CUDA 13
DRIVER_RESERVE_MB = 1024  # VRAM the driver keeps for itself
CDI_DIRS = os.environ.get("NANOBOREALIS_CDI_DIRS") or "/etc/cdi:/var/run/cdi"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class NodeError(Exception):
    pass


class NeedsSetup(NodeError):
    """Windows needs a one-time step with administrator rights first (run_admin_setup)."""

    def __init__(self, install_wsl: bool):
        super().__init__("Windows needs a one-time setup for the pool node.")
        self.install_wsl = install_wsl


@dataclass(frozen=True)
class PoolInfo:
    namespace: str  # "" for exo's default
    engine: str  # exo's full commit
    image: str  # the node image repository
    bundles: str  # where this exo version's node bundles are published

    @classmethod
    def from_answer(cls, data: dict) -> "PoolInfo | None":
        if not data.get("in_pool") or not data.get("engine"):
            return None
        return cls(str(data.get("namespace") or ""), str(data["engine"]), str(data.get("image") or ""),
                   str(data.get("bundles") or ""))

    @property
    def ref(self) -> str:
        return self.engine[:7]


@dataclass(frozen=True)
class Gpu:
    variant: str  # cuda13 or cpu
    reason: str
    name: str = ""
    override_mb: int = 0


def _cdi_has_gpu() -> bool:
    for folder in filter(None, CDI_DIRS.split(":")):
        try:
            for spec in Path(folder).iterdir():
                if spec.suffix in (".yaml", ".yml", ".json") and "nvidia.com/gpu" in spec.read_text(errors="replace"):
                    return True
        except OSError:
            continue
    return False


def nvidia_gpu(containers: bool = True) -> Gpu:
    """The same verdict as `nanoborealis pool gpu`: whether this device's NVIDIA GPU can run the
    CUDA 13 node (Turing or newer, driver 580 or later, and, for a container, the NVIDIA Container
    Toolkit's CDI spec), and how much of its memory the node should report."""
    try:
        done = subprocess.run(["nvidia-smi", "--query-gpu=name,compute_cap,driver_version,memory.total",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15,
                              creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return Gpu("cpu", "no NVIDIA GPU found")
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    fields = [f.strip() for f in lines[0].split(",")] if lines else []
    if done.returncode != 0 or len(fields) < 4:
        return Gpu("cpu", "nvidia-smi sees no GPU; is the NVIDIA driver loaded?")
    name, compute, driver, memory = fields[:4]
    try:
        vram = int(float(memory))
        capability = tuple(int(p) for p in compute.split(".")[:2])
        driver_major = int(driver.split(".")[0])
    except ValueError:
        return Gpu("cpu", f"nvidia-smi answered something unexpected: {lines[0]}", name)
    if capability < MIN_COMPUTE:
        return Gpu("cpu", f"the {name} is too old for CUDA 13, which needs an RTX 20 series or newer", name)
    if driver_major < MIN_DRIVER:
        return Gpu("cpu", f"its driver {driver} is older than {MIN_DRIVER}, which CUDA 13 needs; update the NVIDIA driver",
                   name)
    if containers and not _cdi_has_gpu():
        return Gpu("cpu", "containers can't reach it yet: install the NVIDIA Container Toolkit, then "
                          "sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml", name)
    override = max(512, vram - DRIVER_RESERVE_MB)
    return Gpu("cuda13", f"the {name} runs the pool's models, with {override / 1024:.1f} GB of its memory", name, override)


@dataclass(frozen=True)
class Plan:
    how: str  # container | bundle | wsl | none
    reason: str  # for people: what runs, and on what
    gpu: Gpu | None = None
    bundle: str = ""  # the bundle's platform name, for `how == "bundle"`


def podman() -> str | None:
    return shutil.which("podman")


def plan(system: str | None = None, machine: str | None = None) -> Plan:
    """How this device can run a node."""
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    if system == "Linux":
        if podman():
            gpu = nvidia_gpu(containers=True)
            where = gpu.reason if gpu.variant == "cuda13" else f"the processor runs it ({gpu.reason})"
            return Plan("container", f"Podman runs the pool node; {where}.", gpu)
        return Plan("none", "On Linux the pool node runs in Podman: install it (for example sudo apt install "
                            "podman, or sudo dnf install podman), then try again.")
    if system == "Darwin":
        if machine == "arm64":
            return Plan("bundle", "The pool node runs on this Mac's Apple GPU.", None, "macos-arm64")
        return Plan("none", "Pool nodes need a Mac with Apple silicon.")
    if system == "Windows":
        build = windows_build()
        if build and build < MIN_WINDOWS_BUILD:
            return Plan("none", "Lending this computer to the pool needs Windows 11, version 22H2 or newer: its WSL "
                                "can share the network with the other computers. Serving one model works meanwhile.")
        gpu = nvidia_gpu(containers=False)  # WSL passes the GPU through by itself
        where = gpu.reason if gpu.variant == "cuda13" else f"the processor runs it ({gpu.reason})"
        return Plan("wsl", f"WSL2 runs the pool node; {where}.", gpu)
    return Plan("none", f"Pool nodes don't run on {system}.")


def container_command(info: PoolInfo, gpu: Gpu, slot: int = 0) -> list[str]:
    """`podman run` for the pool's node image of this exact exo version (tagged <variant>-<ref>)."""
    command = ["podman", "run", "-d", "--replace", "--name", slot_name(CONTAINER, slot), "--network", "host",
               "-v", f"{slot_name(VOLUME, slot)}:/home/exo", "--userns", "keep-id:uid=1000,gid=1000",
               "--security-opt", "no-new-privileges", "--cap-drop", "all"]
    if gpu.variant == "cuda13":
        command += ["--device", "nvidia.com/gpu=all", "-e", f"OVERRIDE_MEMORY_MB={gpu.override_mb}"]
    command.append(f"{info.image}:{gpu.variant}-{info.ref}")
    if info.namespace:
        command += ["--namespace", info.namespace]
    return command + slot_args(slot)


# -- Windows ----------------------------------------------------------------------------------------
# exo doesn't run on Windows itself, so the node runs in WSL2, as a WSL system of its own made from
# the pool's node image (published as parts, wsl-<variant>.json lists them with their SHA-256).
# That system can't see this computer's drives or start its programs (its wsl.conf), and reaches
# the other computers through WSL's mirrored networking (Windows 11 22H2 and later), with the
# pool's ports opened in the Hyper-V firewall that guards WSL. The GPU comes through WSL's own
# CUDA driver.

WSL = os.environ.get("NANOBOREALIS_WSL") or "wsl.exe"
DISTRO = "NanoBorealisPool"
MIN_WINDOWS_BUILD = 22621  # Windows 11 22H2: WSL's mirrored networking
WSL_VM_CREATOR = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"  # the Hyper-V firewall's name for WSL
FIREWALL_RULE = "NanoBorealisPool"


def _text(data: bytes) -> str:
    """wsl.exe writes UTF-16 to pipes; everything else, UTF-8."""
    text = data.decode("utf-16-le", "replace") if b"\x00" in data else data.decode("utf-8", "replace")
    return text.replace("\ufeff", "")


def _wsl(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([WSL, *args], capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess([WSL, *args], 1, b"", str(e).encode())


def windows_build() -> int:
    try:
        return int(platform.version().split(".")[2])
    except (IndexError, ValueError):
        return 0


def wsl_installed() -> bool:
    return _wsl("--status").returncode == 0


def distros() -> list[str]:
    return _text(_wsl("--list", "--quiet").stdout).split()


def _powershell(command: str, timeout: float = 600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                              capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess([], 1, "", str(e))


def firewall_ready() -> bool:
    return _powershell(f"Get-NetFirewallHyperVRule -Name '{FIREWALL_RULE}-TCP' -ErrorAction Stop | Out-Null",
                       timeout=60).returncode == 0


def admin_setup_script(install_wsl: bool) -> str:
    """What the one step with administrator rights does: install WSL (without a Linux system of its
    own) when it's missing, and let the pool's traffic into WSL through the Hyper-V firewall."""
    lines = ["$ErrorActionPreference = 'Stop'"]
    if install_wsl:
        lines.append("wsl.exe --install --no-distribution")
    for proto, ports in (("TCP", "52414,52415,52424,52425,49152-65535"), ("UDP", "52413")):
        name = f"{FIREWALL_RULE}-{proto}"
        lines.append(
            f"if (-not (Get-NetFirewallHyperVRule -Name '{name}' -ErrorAction SilentlyContinue)) {{ "
            f"New-NetFirewallHyperVRule -Name '{name}' -DisplayName 'NanoBorealis pool node ({proto})' "
            f"-Direction Inbound -VMCreatorId '{WSL_VM_CREATOR}' -Protocol {proto} -LocalPorts {ports} "
            f"-Action Allow | Out-Null }}")
    return "\n".join(lines)


def run_admin_setup(install_wsl: bool) -> bool:
    """Run admin_setup_script elevated: Windows asks the owner first. False when they declined."""
    import base64
    encoded = base64.b64encode(admin_setup_script(install_wsl).encode("utf-16-le")).decode()
    done = _powershell("$p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
                       f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{encoded}'; "
                       "exit $p.ExitCode", timeout=3600)
    return done.returncode == 0


def wslconfig_path() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / ".wslconfig"


def _wsl2_setting(path: Path, key: str) -> str | None:
    section = ""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
        elif section == "wsl2" and "=" in stripped and not stripped.startswith(("#", ";")):
            name, _, value = stripped.partition("=")
            if name.strip().lower() == key.lower():
                return value.strip()
    return None


def mirrored(path: Path) -> bool:
    return (_wsl2_setting(path, "networkingMode") or "").lower() == "mirrored"


def set_mirrored(path: Path) -> None:
    """Turn on WSL's mirrored networking in .wslconfig, keeping everything else in it as it was."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = []
    out, section, done = [], "", False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if section == "wsl2" and not done:
                out.append("networkingMode=mirrored")
                done = True
            section = stripped[1:-1].strip().lower()
        elif section == "wsl2" and "=" in stripped and stripped.partition("=")[0].strip().lower() == "networkingmode":
            if not done:
                out.append("networkingMode=mirrored")
                done = True
            continue
        out.append(line)
    if not done:
        if section != "wsl2":
            out += ([""] if out else []) + ["[wsl2]"]
        out.append("networkingMode=mirrored")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _download(url: str, dest, progress: Callable[[str], None], label: str) -> str:
    """Stream `url` onto the open file `dest`; returns its SHA-256."""
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "nanoborealis-app"})
    with urllib.request.urlopen(request, timeout=120) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            dest.write(chunk)
            done += len(chunk)
            if total and done % (64 << 20) < (1 << 20):
                progress(f"{label}: {done * 100 // total}%")
    return digest.hexdigest()


def fetch_wsl_system(info: PoolInfo, variant: str, home: Path, progress: Callable[[str], None]) -> Path:
    """Download this exo version's node as a WSL system (in parts), check each part and the whole
    against the published SHA-256, and unpack it to a .tar for `wsl --import`."""
    import gzip
    import json
    if not info.bundles:
        raise NodeError("The NanoBorealis computer doesn't say where this pool's node is published.")
    try:
        manifest = json.loads(_get(f"{info.bundles}/wsl-{variant}.json"))
        parts = manifest["parts"]
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        raise NodeError(f"Couldn't find the pool node for Windows at exo {info.ref}: {e}") from e
    home.mkdir(parents=True, exist_ok=True)
    packed, unpacked = home / f"wsl-{variant}.tar.gz", home / f"wsl-{variant}.tar"
    whole = hashlib.sha256()
    try:
        with open(packed, "wb") as out:
            for number, part in enumerate(parts, 1):
                label = f"Downloading the pool node, part {number} of {len(parts)}"
                progress(label)
                start = out.tell()
                got = _download(f"{info.bundles}/{part['name']}", out, progress, label)
                if got != part["sha256"]:
                    raise NodeError("A downloaded part of the pool node doesn't match its checksum; nothing was installed.")
                out.flush()
                with open(packed, "rb") as back:
                    back.seek(start)
                    while chunk := back.read(1 << 20):
                        whole.update(chunk)
        if whole.hexdigest() != manifest.get("sha256"):
            raise NodeError("The downloaded pool node doesn't match its checksum; nothing was installed.")
        progress("Unpacking the pool node...")
        with gzip.open(packed, "rb") as source, open(unpacked, "wb") as target:
            shutil.copyfileobj(source, target, 1 << 20)
    except (urllib.error.URLError, OSError, KeyError) as e:
        unpacked.unlink(missing_ok=True)
        raise NodeError(f"Couldn't download the pool node for Windows: {e}") from e
    except NodeError:
        unpacked.unlink(missing_ok=True)
        raise
    finally:
        packed.unlink(missing_ok=True)
    return unpacked


def _as_exo(*command: str) -> list[str]:
    """`wsl --user exo -- cmd` runs cmd through exo's login shell, which is nologin in the node image, so
    it always failed with "This account is currently not available". Start as root and drop to exo."""
    return ["--user", "root", "--", "runuser", "-u", "exo", "--", *command]


def _in_distro(*command: str, slot: int = 0) -> str | None:
    done = _wsl("-d", slot_name(DISTRO, slot), *_as_exo(*command), timeout=120)
    return _text(done.stdout).strip() if done.returncode == 0 else None


def wsl_command(info: PoolInfo, gpu: Gpu, slot: int = 0) -> list[str]:
    command = [WSL, "-d", slot_name(DISTRO, slot), "--cd", "/home/exo",
               *_as_exo("env", "HOME=/home/exo",
                        "LD_LIBRARY_PATH=/usr/lib/wsl/lib")]  # where WSL puts the Windows driver's CUDA and NVML
    if gpu.variant == "cuda13":
        command.append(f"OVERRIDE_MEMORY_MB={gpu.override_mb}")
    command.append("/opt/exo/.venv/bin/exo")
    if info.namespace:
        command += ["--namespace", info.namespace]
    return command + slot_args(slot)


def _get(url: str, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "nanoborealis-app"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _unpack(archive: tarfile.TarFile, dest: Path) -> None:
    """Extract only what stays inside `dest`: no absolute paths, no .., no links leading out, no devices."""
    root = dest.resolve()

    def inside(path: Path) -> bool:
        path = path.resolve()
        return path == root or root in path.parents

    for member in archive.getmembers():
        target = dest / member.name
        if not inside(target) or member.isdev() or member.isfifo():
            raise NodeError(f"The pool node bundle holds something unexpected: {member.name}")
        if (member.issym() or member.islnk()) and not inside(
                (target.parent if member.issym() else dest) / member.linkname):
            raise NodeError(f"The pool node bundle links outside itself: {member.name}")
    try:
        archive.extractall(dest, filter="data")
    except TypeError:  # Python before tarfile's extraction filters; checked above
        archive.extractall(dest)


def fetch_bundle(info: PoolInfo, name: str, home: Path, progress: Callable[[str], None] = lambda _: None) -> Path:
    """Download and unpack this exo version's node bundle for `name` (e.g. macos-arm64) once, after
    checking it against its published SHA-256. Returns the node program."""
    folder = home / f"exo-{info.ref}-{name}"
    program = folder / "exo-node" / "exo"
    if program.exists():
        return program
    if not info.bundles:
        raise NodeError("The NanoBorealis computer doesn't say where this pool's node bundles are.")
    url = f"{info.bundles}/exo-node-{name}.tar.gz"
    try:
        progress("Checking the node's checksum...")
        expected = _get(url + ".sha256").decode().split()[0].lower()
        progress("Downloading the pool node...")
        data = _get(url, timeout=1800)
    except (urllib.error.URLError, OSError, IndexError, UnicodeDecodeError) as e:
        raise NodeError(f"Couldn't download the pool node for exo {info.ref}: {e}") from e
    if hashlib.sha256(data).hexdigest() != expected:
        raise NodeError("The downloaded pool node doesn't match its published checksum; nothing was installed.")
    partial = folder.with_name(folder.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        _unpack(archive, partial)
    if not (partial / "exo-node" / "exo").exists():
        shutil.rmtree(partial, ignore_errors=True)
        raise NodeError("The pool node bundle is missing its program.")
    shutil.rmtree(folder, ignore_errors=True)
    partial.rename(folder)
    return program


def answers(timeout: float = 3, slot: int = 0) -> bool:
    try:
        _get(f"http://127.0.0.1:{SLOT_PORTS[slot][1]}/node_id", timeout=timeout)
        return True
    except (urllib.error.URLError, OSError):
        return False


def default_home() -> Path:
    """Where this device keeps its node: the bundles, the node's own home and its log."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "NanoBorealis" / "pool-node"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "NanoBorealis" / "pool-node"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "nanoborealis" / "pool-node"


_active: "list[Node]" = []


def stop_active() -> None:
    """Stop the nodes this app started, if any: the app calls this as its window closes."""
    for node in list(_active):
        try:
            node.stop()
        except (OSError, subprocess.SubprocessError):
            pass


class Node:
    """This device's pool node: started and stopped with the app (start/stop), or left for good."""

    def __init__(self, info: PoolInfo, home: Path, slot: int = 0):
        self.info = info
        self.home = home
        self.slot = slot
        self.process: subprocess.Popen | None = None
        self.how: str | None = None

    @property
    def api_port(self) -> int:
        return SLOT_PORTS[self.slot][1]

    def start(self, chosen: Plan, progress: Callable[[str], None] = lambda _: None, gpu: "Gpu | None" = None) -> None:
        """Start this slot's node; `gpu` picks its build (the GPU's, or Gpu("cpu") for the processor)."""
        distro = slot_name(DISTRO, self.slot)
        if chosen.how == "container":
            gpu = gpu or chosen.gpu or Gpu("cpu", "")
            progress("Downloading the pool node (large the first time; later only what changed)...")
            done = subprocess.run(container_command(self.info, gpu, self.slot), capture_output=True, text=True,
                                  timeout=3600)
            if done.returncode != 0:
                raise NodeError(f"Podman couldn't start the pool node: {done.stderr.strip()[-400:]}")
            self.how = "container"
        elif chosen.how == "wsl":
            gpu = gpu or chosen.gpu or Gpu("cpu", "")
            if not wsl_installed() or not firewall_ready():
                raise NeedsSetup(install_wsl=not wsl_installed())
            config = wslconfig_path()
            if not mirrored(config):
                progress("Turning on WSL's mirrored networking...")
                set_mirrored(config)
                _wsl("--shutdown", timeout=120)  # WSL reads .wslconfig when it starts
            if (distro not in distros() or _in_distro("cat", "/opt/exo/ENGINE_COMMIT", slot=self.slot) != self.info.engine
                    or _in_distro("cat", "/etc/nanoborealis-variant", slot=self.slot) != gpu.variant):
                system = fetch_wsl_system(self.info, gpu.variant, self.home, progress)
                progress("Setting up the pool node in WSL...")
                if distro in distros():
                    _wsl("--unregister", distro, timeout=600)
                done = _wsl("--import", distro, str(self.home / slot_name("wsl", self.slot)), str(system),
                            "--version", "2", timeout=3600)
                system.unlink(missing_ok=True)
                if done.returncode != 0:
                    raise NodeError(f"WSL couldn't set up the pool node: {_text(done.stdout + done.stderr).strip()[-400:]}")
            log = open(self.home / f"{slot_name('node', self.slot)}.log", "ab")  # node.log, node-2.log
            self.process = subprocess.Popen(wsl_command(self.info, gpu, self.slot), stdout=log,
                                            stderr=subprocess.STDOUT, creationflags=_NO_WINDOW)
            self.how = "wsl"
        elif chosen.how == "bundle":
            program = fetch_bundle(self.info, chosen.bundle, self.home, progress)
            args = [str(program)] + (["--namespace", self.info.namespace] if self.info.namespace else [])
            log = open(self.home / "node.log", "ab")
            env = {**os.environ, "HOME": str(self.home / "node-home")}
            (self.home / "node-home").mkdir(parents=True, exist_ok=True)
            self.process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env,
                                            creationflags=_NO_WINDOW)
            self.how = "bundle"
        else:
            raise NodeError(chosen.reason)
        if self not in _active:
            _active.append(self)

    @property
    def running(self) -> bool:
        if self.how in ("bundle", "wsl"):
            return self.process is not None and self.process.poll() is None
        if self.how == "container":
            done = subprocess.run(["podman", "container", "inspect", "--format", "{{.State.Running}}",
                                   slot_name(CONTAINER, self.slot)],
                                  capture_output=True, text=True, timeout=30)
            return done.stdout.strip() == "true"
        return False

    def stop(self) -> None:
        if self.how == "container":
            subprocess.run(["podman", "stop", "--time", "20", slot_name(CONTAINER, self.slot)], capture_output=True,
                           timeout=60)
        elif self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(20)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.how == "wsl":
            _wsl("--terminate", slot_name(DISTRO, self.slot), timeout=60)
        self.process = None
        self.how = None
        if self in _active:
            _active.remove(self)

    def leave(self, delete_models: bool = False) -> None:
        """Stop for good; with delete_models, also the models this device downloaded for the pool."""
        self.stop()
        if podman():
            subprocess.run(["podman", "rm", "-f", slot_name(CONTAINER, self.slot)], capture_output=True, timeout=60)
            if delete_models:
                subprocess.run(["podman", "volume", "rm", "-f", slot_name(VOLUME, self.slot)], capture_output=True,
                               timeout=120)
        if delete_models:
            shutil.rmtree(self.home / "node-home", ignore_errors=True)
            if sys.platform == "win32" and slot_name(DISTRO, self.slot) in distros():
                _wsl("--unregister", slot_name(DISTRO, self.slot), timeout=600)  # its WSL system, and its models


class Lending:
    """What this device lends the pool, by the owner's two consents: its GPU (a node on the GPU's
    memory) and its processor (a node on the computer's memory), each taken whole. A Mac's GPU and
    processor share one memory, so there one node lends both."""

    def __init__(self, info: PoolInfo, home: Path):
        self.info = info
        self.home = home
        self.nodes: list[Node] = []

    @property
    def how(self) -> str | None:
        return next((n.how for n in self.nodes if n.how), None)

    @property
    def api_ports(self) -> list[int]:
        return [n.api_port for n in self.nodes if n.how]

    def roles(self, chosen: Plan, gpu: bool, cpu: bool) -> list[Gpu]:
        """The builds to run, in slot order: the GPU's first, where it can run the pool's models."""
        if chosen.how == "bundle":
            return [Gpu("metal", "")] if gpu or cpu else []
        builds = []
        if gpu and chosen.gpu is not None and chosen.gpu.variant == "cuda13":
            builds.append(chosen.gpu)
        if cpu:
            builds.append(Gpu("cpu", "the processor runs the pool's models, with this computer's memory"))
        return builds

    def start(self, chosen: Plan, gpu: bool, cpu: bool, progress: Callable[[str], None] = lambda _: None) -> None:
        builds = self.roles(chosen, gpu, cpu)
        if not builds:
            raise NodeError("Nothing to lend: allow the GPU, the processor, or both.")
        self.stop()
        for slot, build in enumerate(builds):
            node = Node(self.info, self.home, slot)
            try:
                node.start(chosen, progress, None if chosen.how == "bundle" else build)
            except NodeError:
                self.stop()
                raise
            self.nodes.append(node)

    @property
    def running(self) -> bool:
        return any(n.running for n in self.nodes)

    def answers(self, timeout: float = 3) -> bool:
        return all(answers(timeout, n.slot) for n in self.nodes) if self.nodes else False

    def stop(self) -> None:
        for node in self.nodes:
            node.stop()
        self.nodes = []

    def leave(self, delete_models: bool = False) -> None:
        # Both slots, whichever ran this time: an earlier choice may have left a second one behind.
        for slot in SLOT_PORTS:
            Node(self.info, self.home, slot).leave(delete_models)
        self.nodes = []
