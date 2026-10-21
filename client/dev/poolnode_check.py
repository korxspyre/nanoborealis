# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks lending this device to the pool (poolnode.py) without real hardware: stand-ins for
nvidia-smi, Podman and exo's node program, and a local server for the node bundles.

    python dev/poolnode_check.py

Exits non-zero on the first failed check.
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import poolnode  # noqa: E402
from checks import check  # noqa: E402

INFO = poolnode.PoolInfo.from_answer({
    "in_pool": True, "namespace": "nanoborealis-home", "engine": "21a54c5" + "0" * 33,
    "image": "ghcr.io/korxspyre/nanoborealis-pool", "bundles": ""})


def script(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def tarball(files: dict[str, str], links: dict[str, str] | None = None) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o755
            archive.addfile(member, io.BytesIO(data))
        for name, target in (links or {}).items():
            member = tarfile.TarInfo(name)
            member.type, member.linkname = tarfile.SYMTYPE, target
            archive.addfile(member)
    return out.getvalue()


def main() -> None:
    check(INFO is not None and INFO.ref == "21a54c5", "the pool's exo version comes from the computer")
    check(poolnode.PoolInfo.from_answer({"in_pool": False}) is None, "a computer in no pool offers nothing to join")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bin_dir, cdi = tmp / "bin", tmp / "cdi"
        bin_dir.mkdir()
        cdi.mkdir()
        os.environ["PATH"] = str(bin_dir)  # only the stand-ins: CI runners come with a real Podman
        poolnode.CDI_DIRS = str(cdi)

        # -- The GPU verdict, as `nanoborealis pool gpu` gives it -----------------------------------
        def smi(line: str) -> None:
            script(bin_dir, "nvidia-smi", f"echo '{line}'\n")

        check(poolnode.nvidia_gpu().reason == "no NVIDIA GPU found", "no nvidia-smi: no GPU")
        smi("NVIDIA GeForce RTX 4070, 8.9, 580.82.07, 12282")
        check(poolnode.nvidia_gpu().variant == "cpu", "an RTX 4070 without the container toolkit stays on the processor")
        check(poolnode.nvidia_gpu(containers=False).variant == "cuda13", "...but its driver and generation are right")
        (cdi / "nvidia.yaml").write_text("kind: nvidia.com/gpu\n")
        gpu = poolnode.nvidia_gpu()
        check(gpu.variant == "cuda13" and gpu.override_mb == 11258, "an RTX 4070 runs the CUDA 13 node, with its VRAM")
        smi("NVIDIA GeForce RTX 2060, 7.5, 550.54.14, 6144")
        check("older than 580" in poolnode.nvidia_gpu().reason, "an old driver is named as the reason")

        # -- How each system runs a node --------------------------------------------------------------
        smi("NVIDIA GeForce RTX 3050 Laptop GPU, 8.6, 580.95.05, 4096")
        without = poolnode.plan("Linux", "x86_64")
        check(without.how == "none" and "install" in without.reason, "Linux without Podman is told to install it")
        script(bin_dir, "podman", 'echo "$@" >> "$PODMAN_LOG"\n'
                                  '[ "$1 $2" = "container inspect" ] && echo true\nexit 0\n')
        os.environ["PODMAN_LOG"] = str(tmp / "podman.log")
        linux = poolnode.plan("Linux", "x86_64")
        check(linux.how == "container" and linux.gpu.variant == "cuda13" and linux.gpu.override_mb == 3072,
              "Linux with Podman runs the node image, on the laptop's RTX 3050 with 3 GB of it")
        check(poolnode.plan("Darwin", "arm64").bundle == "macos-arm64", "a Mac with Apple silicon runs the Mac bundle")
        check(poolnode.plan("Darwin", "x86_64").how == "none", "an Intel Mac is told pool nodes need Apple silicon")
        check(poolnode.plan("Windows", "AMD64").how == "wsl", "Windows is pointed at WSL2")

        command = poolnode.container_command(INFO, linux.gpu)
        check(command[-3:] == ["ghcr.io/korxspyre/nanoborealis-pool:cuda13-21a54c5",
                               "--namespace", "nanoborealis-home"],
              "the container is the pool's exact exo version, NVIDIA build, in the pool's namespace")
        check("nvidia.com/gpu=all" in command and "OVERRIDE_MEMORY_MB=3072" in command and "all" in command,
              "the GPU goes in through CDI, with its memory reported, and every capability dropped")
        cpu_command = poolnode.container_command(INFO, poolnode.Gpu("cpu", ""))
        check("--device" not in cpu_command and cpu_command[-3].endswith(":cpu-21a54c5"),
              "without a usable GPU, the processor build")

        second = poolnode.container_command(INFO, poolnode.Gpu("cpu", ""), slot=1)
        check("nanoborealis-pool-2" in second and "nanoborealis-pool-2:/home/exo" in second
              and second[-4:] == ["--zenoh-port", "52424", "--api-port", "52425"],
              "a second node (the processor beside the GPU) has its own container, models and ports")
        check(command[-4:] != ["--zenoh-port", "52424", "--api-port", "52425"] and "--api-port" not in command,
              "the first node keeps exo's own ports")

        lending = poolnode.Lending(INFO, tmp / "home")
        check([g.variant for g in lending.roles(linux, gpu=True, cpu=True)] == ["cuda13", "cpu"],
              "consent to both: a GPU node, then a processor node")
        check([g.variant for g in lending.roles(linux, gpu=True, cpu=False)] == ["cuda13"],
              "only the GPU: the processor stays the owner's")
        check([g.variant for g in lending.roles(linux, gpu=False, cpu=True)] == ["cpu"],
              "only the processor: the GPU stays the owner's")
        no_gpu = poolnode.Plan("container", "", poolnode.Gpu("cpu", "no NVIDIA GPU"))
        check([g.variant for g in lending.roles(no_gpu, gpu=True, cpu=True)] == ["cpu"],
              "no GPU the pool can use: only the processor node, whatever was allowed")
        check(len(lending.roles(poolnode.plan("Darwin", "arm64"), gpu=True, cpu=True)) == 1,
              "a Mac lends one node: its GPU and processor share a memory")
        lending.start(linux, gpu=True, cpu=True)
        started = (tmp / "podman.log").read_text()
        check("--name nanoborealis-pool " in started and "--name nanoborealis-pool-2 " in started
              and lending.api_ports == [52415, 52425], "both nodes start, each on its own API port")
        lending.stop()
        check("stop --time 20 nanoborealis-pool-2" in (tmp / "podman.log").read_text() and not lending.nodes,
              "and both stop with the app")

        node = poolnode.Node(INFO, tmp / "home")
        node.start(linux)
        check("--name nanoborealis-pool" in (tmp / "podman.log").read_text() and node.running,
              "the node starts in Podman, and is seen running")
        node.stop()
        check("stop --time 20 nanoborealis-pool" in (tmp / "podman.log").read_text(), "and stops with the app")

        # -- Bundles ------------------------------------------------------------------------------------
        site = tmp / "site"
        site.mkdir()
        good = tarball({"exo-node/exo": "#!/bin/sh\nexec /bin/sleep 30\n"})
        (site / "exo-node-macos-arm64.tar.gz").write_bytes(good)
        (site / "exo-node-macos-arm64.tar.gz.sha256").write_text(hashlib.sha256(good).hexdigest() + "  x\n")
        evil = tarball({"exo-node/exo": "x", "../../escape": "x"})
        (site / "exo-node-evil.tar.gz").write_bytes(evil)
        (site / "exo-node-evil.tar.gz.sha256").write_text(hashlib.sha256(evil).hexdigest())
        linky = tarball({"exo-node/exo": "x"}, {"exo-node/lib": "/etc"})
        (site / "exo-node-linky.tar.gz").write_bytes(linky)
        (site / "exo-node-linky.tar.gz.sha256").write_text(hashlib.sha256(linky).hexdigest())
        (site / "exo-node-tampered.tar.gz").write_bytes(good)
        (site / "exo-node-tampered.tar.gz.sha256").write_text("0" * 64)
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(site)))
        SimpleHTTPRequestHandler.log_message = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        info = poolnode.PoolInfo(INFO.namespace, INFO.engine, INFO.image, f"http://127.0.0.1:{server.server_port}")
        home = tmp / "home"

        program = poolnode.fetch_bundle(info, "macos-arm64", home)
        check(program.exists() and os.access(program, os.X_OK), "a bundle is downloaded, checked and unpacked")
        for name, why in (("tampered", "checksum"), ("evil", "unexpected"), ("linky", "links outside")):
            try:
                poolnode.fetch_bundle(info, name, home)
                check(False, f"a {name} bundle is refused")
            except poolnode.NodeError as e:
                check(why in str(e) and not (home / f"exo-21a54c5-{name}").exists(),
                      f"a {name} bundle is refused, and nothing of it is kept")
        check(not (tmp / "escape").exists(), "nothing lands outside the node's folder")

        node = poolnode.Node(info, home)
        node.start(poolnode.plan("Darwin", "arm64"))
        time.sleep(0.3)
        check(node.running, "the bundled node runs")
        node.stop()
        check(not node.running, "and stops with the app")

        # -- Windows: the node as a WSL system of its own ------------------------------------------
        state = tmp / "wsl-state"
        state.mkdir()
        os.environ["WSL_STATE"] = str(state)
        script(bin_dir, "wsl.exe", """
log() { echo "$*" >> "$WSL_STATE/calls"; }
log "$@"
case "$1" in
  --status) [ -e "$WSL_STATE/installed" ] ;;
  --list) [ -e "$WSL_STATE/distro" ] && echo NanoBorealisPool; exit 0 ;;
  --shutdown|--terminate) exit 0 ;;
  --import) /bin/mkdir -p "$WSL_STATE/rootfs" && /bin/tar -xf "$4" -C "$WSL_STATE/rootfs" && : > "$WSL_STATE/distro" ;;
  --unregister) /bin/rm -rf "$WSL_STATE/rootfs" "$WSL_STATE/distro" ;;
  # exo's shell is nologin, as in the real image: a command run with --user exo never starts
  -d) [ "$4" = exo ] && { echo "This account is currently not available."; exit 1; }
      # -d NAME [--cd DIR] --user root -- runuser -u exo -- cat FILE
      [ "${10}" = cat ] && exec /bin/cat "$WSL_STATE/rootfs${11}"; exec /bin/sleep 30 ;;
esac
""")
        script(bin_dir, "powershell.exe", """
case "$*" in
  *Get-NetFirewallHyperVRule*Out-Null*) [ -e "$WSL_STATE/firewall" ] ;;
  *Start-Process*) : > "$WSL_STATE/installed"; : > "$WSL_STATE/firewall" ;;
esac
""")
        poolnode.WSL = str(bin_dir / "wsl.exe")
        os.environ["USERPROFILE"] = str(tmp / "winhome")
        (tmp / "winhome").mkdir()
        real_build = poolnode.windows_build
        poolnode.windows_build = lambda: 19045  # Windows 10
        check(poolnode.plan("Windows", "AMD64").how == "none", "Windows 10 is told lending needs Windows 11")
        poolnode.windows_build = lambda: 22631
        smi("NVIDIA GeForce RTX 4070, 8.9, 581.29, 12282")
        windows = poolnode.plan("Windows", "AMD64")
        check(windows.how == "wsl" and windows.gpu.variant == "cuda13",
              "Windows 11 runs the node in WSL, on the RTX 4070 through WSL's CUDA (no container toolkit needed)")

        # The node, as CI publishes it: a WSL system in parts, with a manifest of checksums.
        rootfs = io.BytesIO()
        with tarfile.open(fileobj=rootfs, mode="w") as archive:
            for name, text in (("opt/exo/ENGINE_COMMIT", INFO.engine + "\n"), ("etc/nanoborealis-variant", "cuda13\n"),
                               ("etc/wsl.conf", "[automount]\nenabled = false\n")):
                data = text.encode()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        import gzip
        import json
        packed = gzip.compress(rootfs.getvalue())
        half = len(packed) // 2
        parts = [packed[:half], packed[half:]]
        for number, data in enumerate(parts):
            (site / f"wsl-cuda13.tar.gz.part{number:02d}").write_bytes(data)
        (site / "wsl-cuda13.json").write_text(json.dumps({
            "sha256": hashlib.sha256(packed).hexdigest(),
            "parts": [{"name": f"wsl-cuda13.tar.gz.part{n:02d}", "sha256": hashlib.sha256(d).hexdigest(), "size": len(d)}
                      for n, d in enumerate(parts)]}))

        node = poolnode.Node(info, home)
        try:
            node.start(windows)
            check(False, "Windows first asks for the one-time setup")
        except poolnode.NeedsSetup as e:
            check(e.install_wsl, "Windows without WSL first asks for the one-time setup, which installs WSL")
        script_text = poolnode.admin_setup_script(install_wsl=True)
        check("wsl.exe --install --no-distribution" in script_text and "New-NetFirewallHyperVRule" in script_text
              and "52414,52415,52424,52425,49152-65535" in script_text and poolnode.WSL_VM_CREATOR in script_text,
              "the admin step installs WSL and lets the pool's ports into WSL, and nothing else")
        check(poolnode.run_admin_setup(install_wsl=True), "the admin step runs, elevated")

        wslconfig = tmp / "winhome" / ".wslconfig"
        wslconfig.write_text("# mine\n[wsl2]\nmemory=8GB\nnetworkingMode=nat\n[experimental]\nautoMemoryReclaim=gradual\n")
        node.start(windows)
        config = wslconfig.read_text()
        check(poolnode.mirrored(wslconfig) and "memory=8GB" in config and "# mine" in config
              and "autoMemoryReclaim=gradual" in config and "networkingMode=nat" not in config,
              "mirrored networking is turned on, and the rest of .wslconfig is kept")
        calls = (state / "calls").read_text()
        check("--shutdown" in calls and "--import NanoBorealisPool" in calls and "--version 2" in calls,
              "WSL restarts for the new networking, and the node's system is imported from the checked download")
        time.sleep(0.5)
        calls = (state / "calls").read_text()  # the node's own launch lands once it has started
        check(node.running and "OVERRIDE_MEMORY_MB=11258" in calls and "LD_LIBRARY_PATH=/usr/lib/wsl/lib" in calls
              and "--namespace nanoborealis-home" in calls, "the node runs in WSL with the GPU's memory and WSL's CUDA")
        node.stop()
        check(not node.running and "--terminate NanoBorealisPool" in (state / "calls").read_text(),
              "and stops with the app, WSL system and all")

        (state / "calls").write_text("")
        node.start(windows)
        check("--import" not in (state / "calls").read_text(), "next time, the node's system is already there")
        node.stop()
        (state / "rootfs/opt/exo/ENGINE_COMMIT").write_text("0" * 40 + "\n")  # the pool moved to another exo
        (state / "calls").write_text("")
        node.start(windows)
        calls = (state / "calls").read_text()
        check("--unregister NanoBorealisPool" in calls and "--import" in calls,
              "when the pool's exo version changes, the node's system is replaced with the matching one")
        node.stop()
        node.leave(delete_models=True) if sys.platform == "win32" else None
        poolnode.windows_build = real_build

        (site / "wsl-cuda13.tar.gz.part01").write_bytes(b"tampered")
        (state / "distro").unlink()
        try:
            poolnode.Node(info, tmp / "home2").start(windows)
            check(False, "a tampered download is refused")
        except poolnode.NodeError as e:
            check("checksum" in str(e) and not list((tmp / "home2").glob("wsl-*.tar*")),
                  "a tampered download is refused, and nothing of it is kept")
        server.shutdown()
    print("all pool node checks passed")


if __name__ == "__main__":
    main()
