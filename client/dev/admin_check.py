# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks the control center's endpoints on the remote-access service, end to end.

The service runs in test mode with NANOBOREALIS_REMOTE_TEST_AGENT_HOME, a folder standing in for
the agent's home (see .github/workflows/test-client.yml):

    python dev/admin_check.py 127.0.0.1:18868 /tmp/pin3 /tmp/agenthome

Exits non-zero on the first failed check.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import admin  # noqa: E402
from pairing import Machine, NotPaired, split_address  # noqa: E402
from checks import check, pair  # noqa: E402


def refused(fn, *args) -> str:
    try:
        fn(*args)
    except admin.AdminError as e:
        return str(e)
    return ""


POOL = {  # for the stand-in exo (pool/tests/stub_exo.py): a GPU desktop and a laptop
    "nodes": [{"id": "n-gpu", "name": "gpu-desk", "backend": "MlxCuda", "link": "ethernet", "ram_gb": 23},
              {"id": "n-lap", "name": "laptop", "backend": "MlxCpu", "link": "wifi", "ram_gb": 16}],
    "models": [{"id": "mlx-community/Qwen3-14B-4bit", "size_mb": 8300, "family": "qwen", "quantization": "4bit",
                "base_model": "Qwen3 14B", "n_layers": 40},
               {"id": "mlx-community/Qwen3-4B-4bit", "size_mb": 2300, "family": "qwen", "quantization": "4bit",
                "base_model": "Qwen3 4B", "n_layers": 36},
               {"id": "mlx-community/gemma-3-12b-it-4bit", "size_mb": 7000, "family": "gemma",
                "quantization": "4bit", "base_model": "Gemma 3 12B", "n_layers": 48}],
    "truth": {"cpu": 0.5, "cuda": 1.0},
    "bad_tools": [],
}


def finished(machine: Machine) -> dict:
    for _ in range(240):
        job = admin.pool_job(machine)
        if not job["running"]:
            return job
        time.sleep(0.5)
    raise SystemExit("FAIL the pool change never finished")


def pool_page(machine: Machine) -> None:
    """The Pool page's endpoints, against a stand-in exo at NANOBOREALIS_POOL_API (the service was
    started with the same variables, so its pool.py talks to it)."""
    import subprocess
    import tempfile
    port = int(os.environ["NANOBOREALIS_POOL_API"].rsplit(":", 1)[1])
    stub_dir = tempfile.mkdtemp()
    spec = os.path.join(stub_dir, "pool.json")
    with open(spec, "w") as f:
        json.dump(POOL, f)
    stub = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "..", "..", "pool", "tests",
                                                          "stub_exo.py"), str(port), spec])
    try:
        time.sleep(1.5)
        overview = admin.pool_overview(machine)
        check(not overview.get("error") and len(overview["nodes"]) == 2 and overview["plan"]["start"]
              and overview["bench"] == [] and "min_gen_tps" in overview["policy"],
              f"pool page: computers, a proposed loadout, no measurements yet, the policy ({overview.get('error')})")

        job = admin.pool_apply(machine)
        check(job["running"] and job["what"], "pool page: applying runs in the background")
        job = finished(machine)
        check(job["ok"] and any("start" in line for line in job["output"]), "...and reports what it did")
        overview = admin.pool_overview(machine)
        check(not overview["plan"]["start"] and not overview["plan"]["stop"],
              "pool page: after applying, the pool runs the proposed loadout")

        admin.pool_bench(machine)
        check("still busy" in refused(admin.pool_apply, machine), "pool page: one change at a time")
        check(finished(machine)["ok"], "pool page: measuring what runs finishes")
        bench = admin.pool_overview(machine)["bench"]
        check(bool(bench) and all("fast" in row for row in bench), "pool page: the bench table has the measurements")

        policy = admin.pool_policy(machine, {"banned": ["mlx-community/Qwen3-4B-4bit"], "min_gen_tps": 5.0})
        check(policy["banned"] == ["mlx-community/Qwen3-4B-4bit"] and policy["min_gen_tps"] == 5.0,
              "pool page: policy changes are saved")
        check(refused(admin.pool_policy, machine, {"nonsense": 1}) != "", "pool page: an unknown setting is refused")
        check(refused(admin.pool_policy, machine, {"pinned": ["--rebuild"]}) != "",
              "pool page: a name that would be an option is refused")
        admin.pool_policy(machine, {"banned": []})

        admin.pool_drain(machine, "n-gpu")
        check(finished(machine)["ok"], "pool page: draining a computer finishes")
        nodes = {n["id"]: n for n in admin.pool_overview(machine)["nodes"]}
        check(nodes["n-gpu"]["drained"] and not nodes["n-gpu"]["holds"],
              "pool page: a drained computer holds no models")
        admin.pool_undrain(machine, "n-gpu")
        nodes = {n["id"]: n for n in admin.pool_overview(machine)["nodes"]}
        check(not nodes["n-gpu"]["drained"], "pool page: a drained computer can come back")
    finally:
        stub.terminate()


def coordinator(machine: Machine) -> None:
    """A computer that coordinates the pool without a node: a lending device checks in, and the
    service points the pool's tools at that device's node while it answers (the service runs with
    NANOBOREALIS_REMOTE_POOL_EVERY=1 and the pointer file at NANOBOREALIS_REMOTE_POOL_API_FILE)."""
    import socket
    import subprocess
    import tempfile
    pointer = os.environ["NANOBOREALIS_REMOTE_POOL_API_FILE"]

    def points_at() -> str:
        try:
            return open(pointer).read().strip()
        except OSError:
            return ""

    def wait_for(wanted, seconds: float = 20) -> bool:
        for _ in range(int(seconds * 5)):
            if wanted(points_at()):
                return True
            time.sleep(0.2)
        return False

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    spec = os.path.join(tempfile.mkdtemp(), "pool.json")
    with open(spec, "w") as f:
        json.dump({**POOL, "nodes": POOL["nodes"][:1]}, f)
    stub = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "..", "..", "pool", "tests",
                                                          "stub_exo.py"), str(port), spec])
    try:
        admin.pool_lend(machine, True, [port])
        check(wait_for(lambda p: p == f"127.0.0.1:{port}"), f"coordinator: a lending device's node is used ({points_at()})")
    finally:
        stub.terminate()
        stub.wait(10)
    check(wait_for(lambda p: p != f"127.0.0.1:{port}"), "coordinator: a node that stops answering is dropped")
    admin.pool_lend(machine, False)
    check(True, "coordinator: a device can stop lending")


def main(address: str, pin_file: str, home: str) -> None:
    host, port = split_address(address)
    machine = pair(host, port, pin_file, "Control center check")
    check(admin.supported(machine), "the computer offers the control center")
    config_path = os.path.join(home, ".nanobot", "config.json")
    for _ in range(50):
        config = json.load(open(config_path))
        if config["agents"]["defaults"].get("model"):
            break
        time.sleep(0.2)
    limit = config["providers"]["openrouter"].get("extraBody", {}).get("provider", {}).get("max_price", {})
    check(config["agents"]["defaults"].get("model", "").endswith(":free") and limit.get("prompt") == 0,
          "an agent set up before free-only is kept to free models")
    check(config["providers"]["openrouter"]["apiKey"] == "${OPENROUTER_API_KEY}" and
          config["agents"]["defaults"]["modelPreset"] == "ultra", "...and nothing else in its config changes")
    info = admin.overview(machine)
    check(info["agent"]["running"] and info["machine"], f"overview: the agent's state ({info['agent']['state']})")

    admin.write_file(machine, f"{admin.WORKSPACE}/memory/MEMORY.md", "# Memory\n- likes tea\n")
    check(admin.read_file(machine, f"{admin.WORKSPACE}/memory/MEMORY.md") == "# Memory\n- likes tea\n",
          "a workspace file round-trips")
    check(any(f["path"].endswith("memory/MEMORY.md") for f in admin.list_files(machine)), "workspace files are listed")
    for bad in ("../../../etc/passwd", "/etc/passwd", f"{admin.WORKSPACE}/../../.ssh/id_rsa", ".nanobot/config.json",
                ".nanobot/sessions/x.jsonl"):
        check(refused(admin.read_file, machine, bad) != "" and refused(admin.write_file, machine, bad, "x") != "",
              f"stays out of {bad}")
    os.symlink("/etc/hostname", os.path.join(home, ".nanobot", "workspace", "escape.md"))
    check(refused(admin.read_file, machine, f"{admin.WORKSPACE}/escape.md") != "",
          "a link out of the agent's home is not followed")
    admin.delete_file(machine, f"{admin.WORKSPACE}/memory/MEMORY.md")
    check(refused(admin.read_file, machine, f"{admin.WORKSPACE}/memory/MEMORY.md") != "", "a file can be deleted")

    check("no OpenRouter key" in admin.usage(machine).get("error", ""), "usage says when there's no key")
    check("sk-or-" in refused(admin.set_key, machine, "hunter2"), "a key that isn't one is turned down")
    admin.set_key(machine, "sk-or-v1-" + "0" * 64)
    check(open(os.path.join(home, ".openrouter-key")).read() == "sk-or-v1-" + "0" * 64, "a new key is saved")

    # The pool, for lending a device to it: nothing until `pool join` has recorded it.
    pool_file = os.path.join(os.path.dirname(os.path.abspath(home)), "pool.json")
    if os.path.exists(pool_file):
        os.remove(pool_file)
    check(admin.pool_info(machine) == {"in_pool": False}, "pool: a computer in no pool says so")
    engine = "21a54c5" + "0" * 33
    with open(pool_file, "w") as f:
        json.dump({"namespace": "nanoborealis-home", "engine": engine,
                   "image": "ghcr.io/korxspyre/nanoborealis-pool", "variant": "cpu"}, f)
    info = admin.pool_info(machine)
    check(info["in_pool"] and info["namespace"] == "nanoborealis-home" and info["engine"] == engine
          and info["bundles"].endswith("/pool-node-21a54c5"),
          "pool: the pool's name, exo's exact version, and where its node bundles are")
    pool_page(machine)
    coordinator(machine)
    check("no council yet" in refused(admin.hall, machine, "GET", "/rooms"),
          "council: a computer without one says how to seat it")

    listed = admin.devices(machine)
    check(any(d["this"] and d["id"] == machine.device_id for d in listed), "devices: this one is marked")
    other = pair(host, port, pin_file, "Second device")
    admin.remove_device(machine, other.device_id)
    try:
        admin.overview(other)
        check(False, "a removed device is locked out")
    except NotPaired:
        check(True, "a removed device is locked out")
    stranger = Machine(host, port, machine.name, machine.certificate, "unknown-" * 6, "0000")
    try:
        admin.overview(stranger)
        check(False, "an unknown device gets nothing")
    except NotPaired:
        check(True, "an unknown device gets nothing")
    print("all control center checks passed")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
