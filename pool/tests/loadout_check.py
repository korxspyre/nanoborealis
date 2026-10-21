# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks the loadout planner, and `pool.py`'s loadout commands against a stand-in exo pool
(stub_exo.py): one GPU desktop, a Mac and two CPU laptops, a catalog with a model too big to be
fast, and one whose tool calls fail.

    python3 pool/tests/loadout_check.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "system_files/usr/share/nanoborealis"
sys.path.insert(0, str(TOOLS))

import loadout  # noqa: E402
from benchdb import BenchDB  # noqa: E402
from loadout import GB, Facts, Model, Node, Policy  # noqa: E402


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


POOL = {
    "nodes": [
        {"id": "n-gpu", "name": "gpu-desk", "backend": "MlxCuda", "link": "ethernet", "ram_gb": 23},  # VRAM
        {"id": "n-mac", "name": "mac", "backend": "MlxMetal", "link": "wifi", "ram_gb": 24},
        {"id": "n-la", "name": "laptop-a", "backend": "MlxCpu", "link": "wifi", "ram_gb": 16},
        {"id": "n-lb", "name": "laptop-b", "backend": "MlxCpu", "link": "wifi", "ram_gb": 16},
    ],
    "models": [
        {"id": "mlx-community/Qwen3-30B-A3B-4bit", "size_mb": 16800, "family": "qwen", "quantization": "4bit",
         "base_model": "Qwen3 30B", "n_layers": 48},
        {"id": "mlx-community/gemma-3-27b-it-4bit", "size_mb": 15600, "family": "gemma", "quantization": "4bit",
         "base_model": "Gemma 3 27B", "n_layers": 62},
        {"id": "mlx-community/Qwen3-14B-4bit", "size_mb": 8300, "family": "qwen", "quantization": "4bit",
         "base_model": "Qwen3 14B", "n_layers": 40},
        {"id": "mlx-community/Qwen3-4B-4bit", "size_mb": 2300, "family": "qwen", "quantization": "4bit",
         "base_model": "Qwen3 4B", "n_layers": 36},
        {"id": "mlx-community/Llama-3.1-70B-4bit", "size_mb": 40000, "family": "llama", "quantization": "4bit",
         "base_model": "Llama 3.1 70B", "n_layers": 80},
        {"id": "mlx-community/Broken-8B-4bit", "size_mb": 4600, "family": "broken", "quantization": "4bit",
         "base_model": "Broken 8B", "n_layers": 32},
    ],
    "truth": {"cpu": 0.5, "metal": 0.8, "cuda": 1.0},
    "bad_tools": ["mlx-community/Broken-8B-4bit"],
}


# -- The planner on its own -----------------------------------------------------------------------

check(loadout.parameters("mlx-community/Qwen3-30B-A3B-4bit", "Qwen3 30B", 17 * GB, "4bit") == (30.0, 3.0),
      "a mixture-of-experts model's total and active parameters come from its name")
check(loadout.parameters("mlx-community/Qwen3-14B-4bit", "Qwen3 14B", 8 * GB, "4bit") == (14.0, 14.0),
      "a dense model's parameters come from its name")

nodes = [Node("g", "gpu", "cuda", "ethernet", 32 * GB, 30 * GB), Node("m", "mac", "metal", "wifi", 24 * GB, 20 * GB),
         Node("a", "lap-a", "cpu", "wifi", 16 * GB, 12 * GB), Node("b", "lap-b", "cpu", "wifi", 16 * GB, 12 * GB)]
models = [Model("moe", int(16.4 * GB), 30, 3, "qwen", "4bit"), Model("dense27", int(15.2 * GB), 27, 27, "gemma", "4bit"),
          Model("dense14", int(8.1 * GB), 14, 14, "qwen", "4bit"), Model("small", int(2.2 * GB), 4, 4, "qwen", "4bit"),
          Model("huge", 39 * GB, 70, 70, "llama", "4bit")]
facts = Facts({"moe": (52, True), "dense27": (47, True), "dense14": (41, True), "small": (26, True), "huge": (58, True)},
              {}, {}, {})
policy = Policy()
plan = loadout.plan(nodes, models, facts, policy)
chosen = {p.model.id for p in plan.placements}
check(len(plan.placements) >= 3, f"the pool runs a council, not one model ({len(plan.placements)} models)")
check(all(p.speed.gen_tps >= policy.min_gen_tps and p.speed.prompt_tps >= policy.min_prompt_tps
          for p in plan.placements), "every model is above the speed floors")
check("huge" not in chosen, "a model that would be too slow sharded over this pool is left out")
check(len({p.model.family for p in plan.placements}) >= 2, "the council mixes model families")
used = {n.id: 0.0 for n in nodes}
for p in plan.placements:
    for i, share in p.shares.items():
        used[i] += (p.model.size + policy.kv_reserve) * share
check(all(used[n.id] <= n.mem_capacity - policy.headroom + 1 for n in nodes),
      "every computer keeps its headroom free")
cpu_only = [p for p in plan.placements if all(i in ("a", "b") for i in p.nodes)]
check(all(p.model.moe or p.model.size < 4 * GB for p in cpu_only),
      "CPU-only laptops get small or mixture-of-experts models, never a big dense one")
check(max(p.capability for p in plan.placements) >= policy.anchor_min, "the council has an anchor model")

banned = loadout.plan(nodes, models, facts, Policy(banned=["moe"]))
check("moe" not in {p.model.id for p in banned.placements}, "a banned model isn't planned")
pinned = loadout.plan(nodes, models, facts, Policy(pinned=["small"], max_models=1))
check([p.model.id for p in pinned.placements] == ["small"], "a pinned model goes in first")
drained = loadout.plan(nodes, models, facts, Policy(excluded_nodes=["a"]))
check(all("a" not in p.nodes for p in drained.placements), "a drained computer holds nothing")
weak = loadout.plan(nodes, [models[3]], facts, policy)
check(any("anchor" in n for n in weak.notes), "a council without a capable model says so")
failed = loadout.plan(nodes, models, Facts(facts.capability, {"moe": False}, {}, {}), policy)
check("moe" not in {p.model.id for p in failed.placements}, "a model whose tool calls failed isn't planned")

with tempfile.TemporaryDirectory() as tmp:
    db = BenchDB(Path(tmp) / "bench.sqlite")
    predicted = loadout.predict(models[3], [nodes[2]], {"a": 1.0})
    db.record(engine="exo", model="small", nodes={"a": "lap-a"}, backends={"cpu"}, prompt_tokens=4000,
              prompt_tps=predicted.prompt_tps / 2, gen_tps=predicted.gen_tps / 2, tools=True, predicted=predicted)
    calibration = db.calibration()
    check(abs(calibration[("gen", "cpu")] - 0.5) < 1e-6, "bench runs calibrate the speed priors per backend")
    check(db.tools() == {"small": True}, "the tool-call check is remembered")
    check(db.measured()[("small", frozenset({"a"}))].measured, "a measured speed is used as measured")


# -- NVIDIA nodes ------------------------------------------------------------------------------------

gpu_state = {
    "nodeIdentities": {"g": {"friendlyName": "rtx4070"}, "c": {"friendlyName": "laptop"}},
    "nodeMemory": {"g": {"ramTotal": {"inBytes": 64 * GB}, "ramAvailable": {"inBytes": 11 * GB}},
                   "c": {"ramTotal": {"inBytes": 16 * GB}, "ramAvailable": {"inBytes": 8 * GB}}},
    "nodeBackends": {"g": ["MlxCpu", "MlxCuda", "Vllm"], "c": ["MlxCpu"]},
    "instances": {"i1": {"MlxRingInstance": {"shardAssignments": {
        "modelId": "m", "nodeToRunner": {"g": "r1", "c": "r2"},
        "runnerToShard": {"r1": {"PipelineShardMetadata": {"startLayer": 0, "endLayer": 20, "modelCard": {"nLayers": 40}}},
                          "r2": {"PipelineShardMetadata": {"startLayer": 20, "endLayer": 40, "modelCard": {"nLayers": 40}}}}}}}},
}
gpu_nodes = {n.name: n for n in loadout.nodes_from_state(gpu_state, {"m": 8 * GB})}
check(gpu_nodes["rtx4070"].backend == "cuda" and gpu_nodes["rtx4070"].mem_capacity == 11 * GB,
      "an NVIDIA node's memory is its VRAM budget, with nothing added back for loaded models")
check(gpu_nodes["laptop"].mem_capacity == 12 * GB, "a computer's memory counts what its served models hold")
shares = loadout.shares_for(models[1], list(gpu_nodes.values()), {"g": 2 * GB, "c": 12 * GB})
check(abs(shares["g"] - 11 / 23) < 1e-9, "exo splits a model by an NVIDIA node's fixed budget, not what's left")

with tempfile.TemporaryDirectory() as tmp:
    db = BenchDB(Path(tmp) / "bench.sqlite")
    fast = Node("f", "rtx4070", "cuda", "ethernet", 11 * GB, 11 * GB)
    slow = Node("s", "rtx3050", "cuda", "wifi", 3 * GB, 3 * GB)
    for node, factor in ((fast, 1.0), (slow, 0.4)):
        guess = loadout.predict(models[3], [node], {node.id: 1.0})
        db.record(engine="exo", model="small", nodes={node.id: node.name}, backends={"cuda"}, prompt_tokens=4000,
                  prompt_tps=guess.prompt_tps * factor, gen_tps=guess.gen_tps * factor, tools=True, predicted=guess)
    calibration = db.calibration()
    check(abs(calibration[("gen", "node:s")] - 0.4) < 1e-6 and abs(calibration[("gen", "node:f")] - 1.0) < 1e-6,
          "each computer's own bench runs calibrate its speed")
    check(loadout.predict(models[2], [slow], {"s": 1.0}, calibration).gen_tps
          < loadout.predict(models[2], [fast], {"f": 1.0}, calibration).gen_tps * 0.5,
          "the planner expects the slower card to be slower, though both are NVIDIA")


def gpu_check(smi_line: str | None, cdi: bool) -> dict:
    """pool.py gpu with a stand-in nvidia-smi answering `smi_line` (None: no nvidia-smi at all)."""
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir, cdi_dir = Path(tmp, "bin"), Path(tmp, "cdi")
        bin_dir.mkdir()
        cdi_dir.mkdir()
        if smi_line is not None:
            smi = bin_dir / "nvidia-smi"
            smi.write_text(f"#!/bin/sh\necho '{smi_line}'\n")
            smi.chmod(0o755)
        if cdi:
            (cdi_dir / "nvidia.yaml").write_text("cdiVersion: 0.5.0\nkind: nvidia.com/gpu\ndevices:\n- name: all\n")
        env = {**os.environ, "PATH": str(bin_dir), "NANOBOREALIS_CDI_DIRS": str(cdi_dir)}
        done = subprocess.run([sys.executable, str(TOOLS / "pool.py"), "gpu"], env=env, capture_output=True, text=True)
        return json.loads(done.stdout)


rtx4070 = gpu_check("NVIDIA GeForce RTX 4070, 8.9, 580.82.07, 12282", cdi=True)
check(rtx4070["variant"] == "cuda13" and rtx4070["override_mb"] == 12282 - 1024,
      "an RTX 4070 with driver 580 runs the CUDA 13 node, reporting its VRAM less 1 GB")
laptop = gpu_check("NVIDIA GeForce RTX 3050 Laptop GPU, 8.6, 580.95.05, 4096", cdi=True)
check(laptop["variant"] == "cuda13" and laptop["override_mb"] == 3072, "a laptop RTX 3050 lends its 4 GB, less 1 GB")
old_driver = gpu_check("NVIDIA GeForce RTX 2060, 7.5, 550.54.14, 6144", cdi=True)
check(old_driver["variant"] == "cpu" and "older than 580" in old_driver["reason"],
      "an RTX 2060 on a driver older than 580 runs on the processor, and says to update the driver")
check(gpu_check("NVIDIA GeForce RTX 2060, 7.5, 580.82.07, 6144", cdi=True)["variant"] == "cuda13",
      "an RTX 2060 on driver 580 runs the CUDA 13 node")
no_cdi = gpu_check("NVIDIA GeForce RTX 4070, 8.9, 580.82.07, 12282", cdi=False)
check(no_cdi["variant"] == "cpu" and "Container Toolkit" in no_cdi["reason"],
      "without the container toolkit's spec, the node runs on the processor and says what to install")
pascal = gpu_check("NVIDIA GeForce GTX 1080, 6.1, 580.82.07, 8192", cdi=True)
check(pascal["variant"] == "cpu" and "too old" in pascal["reason"], "a GTX 10 series card is too old for CUDA 13")
check(gpu_check(None, cdi=True) == {"variant": "cpu", "reason": "no NVIDIA GPU found"},
      "a computer without nvidia-smi runs on the processor")


# -- Scores ---------------------------------------------------------------------------------------------

import scores  # noqa: E402

table = "model,web_of_lies,zebra,code_gen\nqwen3-30b-a3b-thinking,60,70,50\nqwen3-32b,1,2,3\n"
rows = scores.parse(table, {"reasoning": ["web_of_lies", "zebra"], "coding": ["code_gen"]})
check(rows["qwen3-30b-a3b-thinking"] == {"overall": 57.5, "categories": {"reasoning": 65.0, "coding": 50.0}},
      "LiveBench's table reads as category means, and overall as their mean")
check(scores.match("mlx-community/Qwen3-30B-A3B-4bit", list(rows)) == "qwen3-30b-a3b-thinking",
      "an exo model finds its LiveBench entry")
check(scores.match("mlx-community/Qwen3-14B-4bit", list(rows)) is None,
      "a model is never matched to one of another size")
# Names from LiveBench's 2026-06-25 release, and how exo and OpenRouter name the same models.
live = ["glm-5.3-flash", "kimi-k2.6-thinking", "qwen3.6-27b", "deepseek-v4-flash", "deepseek-v4-flash-0731",
        "nemotron-3-ultra-550b-a55b", "inkling-xhigh", "qwen3.7-max", "qwen3.8-max"]
for model_id, want in [("mlx-community/Qwen3.6-27B-4bit", "qwen3.6-27b"),
                       ("mlx-community/DeepSeek-V4-Flash", "deepseek-v4-flash"),
                       ("moonshotai/Kimi-K2.6", "kimi-k2.6-thinking"),
                       ("nvidia/nemotron-3-ultra-550b-a55b:free", "nemotron-3-ultra-550b-a55b"),
                       ("thinkingmachines/inkling:free", "inkling-xhigh"),
                       ("mlx-community/GLM-4.7-Flash-4bit", None),
                       ("mlx-community/Kimi-K2-Thinking", None),
                       ("qwen/qwen3.7-max", "qwen3.7-max")]:
    check(scores.match(model_id, live) == want, f"{model_id} is scored as {want or 'nothing on LiveBench'}")
with tempfile.TemporaryDirectory() as tmp:
    Path(tmp, "livebench.json").write_text(json.dumps({"release": "test", "models": rows}))
    moe = Model("mlx-community/Qwen3-30B-A3B-4bit", 16 * GB, 30, 3, "qwen", "4bit")
    found = scores.capabilities([moe], Path(tmp))[moe.id]
    check(found == (round(57.5 * 0.97, 1), True), "a quantized build scores a little under LiveBench's, as an estimate")
    Path(tmp, "overrides.json").write_text(json.dumps({moe.id: 61}))
    check(scores.capabilities([moe], Path(tmp))[moe.id] == (61.0, False), "a score you set wins")
with tempfile.TemporaryDirectory() as tmp:
    calls = []
    real_refresh, scores.refresh = scores.refresh, lambda d: calls.append(d) or ("x", 0)
    try:
        scores.ensure(Path(tmp))
        scores.ensure(Path(tmp))
        check(len(calls) == 1, "a missing LiveBench copy is fetched once, not again within a day after a failure")
        Path(tmp, "livebench.json").write_text("{}")
        Path(tmp, "livebench.tried").unlink()
        scores.ensure(Path(tmp))
        check(len(calls) == 1, "a fresh LiveBench copy isn't fetched again")
    finally:
        scores.refresh = real_refresh
check(scores.guess(Model("x", GB, 4, 4, quant="4bit")) < scores.guess(Model("y", 8 * GB, 14, 14, quant="4bit")),
      "the size guess grows with the model")


# -- pool.py against a stand-in exo -------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


with tempfile.TemporaryDirectory() as tmp:
    spec = Path(tmp) / "pool.json"
    spec.write_text(json.dumps(POOL))
    port = free_port()
    api = f"http://127.0.0.1:{port}"
    stub = subprocess.Popen([sys.executable, str(Path(__file__).with_name("stub_exo.py")), str(port), str(spec)])
    env = {**os.environ, "NANOBOREALIS_POOL_API": api, "NANOBOREALIS_POOL_STATE": str(Path(tmp) / "state"),
           "NANOBOREALIS_POOL_OFFLINE": "1"}

    def pool(*args: str, ok: bool = True, **extra_env: str) -> str:
        done = subprocess.run([sys.executable, str(TOOLS / "pool.py"), *args], env={**env, **extra_env}, capture_output=True,
                              text=True, timeout=120, stdin=subprocess.DEVNULL)
        if ok and done.returncode != 0:
            print(done.stdout, done.stderr)
            check(False, f"pool.py {' '.join(args)} exits cleanly")
        return done.stdout + done.stderr

    def get(path: str):
        with urllib.request.urlopen(api + path, timeout=10) as response:
            return json.loads(response.read())

    try:
        for _ in range(50):
            try:
                get("/node_id")
                break
            except OSError:
                time.sleep(0.1)

        out = pool("nodes")
        check("Computers in the pool: 4" in out and "gpu-desk" in out and "cuda" in out,
              "nodes lists every computer with its backend")

        proposal = json.loads(pool("plan", "--json"))
        check(len(proposal["placements"]) >= 3 and not proposal["stop"], "plan proposes a council for an empty pool")
        check("mlx-community/Llama-3.1-70B-4bit" not in proposal["start"], "plan leaves out the model too slow to use")

        check("Nothing changed." in pool("apply"), "apply asks first, and changes nothing without a yes")
        pool("apply", "--yes")
        created = [e for e in get("/_log") if e["action"] == "create"]
        check(sorted(e["model"] for e in created) == sorted(proposal["start"]),
              "apply starts exactly the proposed models")
        for entry in created:
            wanted = next(p for p in proposal["placements"] if p["model"] == entry["model"])
            check(entry["nodes"] == sorted(n["id"] for n in wanted["nodes"]),
                  f"{entry['model'].split('/')[1]} runs on exactly the planned computers")
        check("already runs this loadout" in pool("plan"), "after apply, the pool already runs the plan")

        broken = "mlx-community/Broken-8B-4bit"
        check(broken in proposal["start"], "a model whose tool calls were never checked can be planned")
        out = pool("bench", "--prompt", "2000")
        check(out.count("tok/s writing") == len(created), "bench measures every served model")
        check("tool call FAILED" in out, "the tool-call check catches a model that can't call tools")
        results = pool("bench", "--results")
        check("✓" in results, "the results table shows what runs acceptably")
        proposal = json.loads(pool("plan", "--json"))
        check(all(p["model"] != broken for p in proposal["placements"]) and
              any(s["model"] == broken for s in proposal["stop"]),
              "a model that failed the tool check leaves the plan, and plan would stop it")
        pool("apply", "--yes")

        trial = "mlx-community/Qwen3-4B-4bit"  # banned and stopped, then tried by hand
        pool("policy", f"banned={trial}")
        pool("apply", "--yes")
        served = {i["MlxRingInstance"]["shardAssignments"]["modelId"] for i in get("/state")["instances"].values()}
        check(trial not in served, "applying after a ban stops the banned model")
        memory = get("/state")["nodeMemory"]
        roomiest = max(POOL["nodes"], key=lambda n: memory[n["id"]]["ramAvailable"]["inBytes"])
        log_before = len(get("/_log"))
        pool("bench", "--try", trial, "--on", roomiest["name"])
        log = get("/_log")[log_before:]
        check([e["action"] for e in log] == ["create", "delete"] and log[0]["nodes"] == [roomiest["id"]],
              "bench --try places a model where asked, measures it, then stops it")
        some_served = next(iter(served - {broken}))
        check("served already" in pool("bench", "--try", some_served, ok=False),
              "bench --try refuses a model that is already served")

        request = urllib.request.Request(api + "/_stuck", data=json.dumps({"model": trial}).encode(),
                                         headers={"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=10).close()
        log_before = len(get("/_log"))
        out = pool("bench", "--try", trial, "--on", roomiest["name"], ok=False, NANOBOREALIS_POOL_PATIENCE="2")
        log = get("/_log")[log_before:]
        check("didn't get ready" in out and [e["action"] for e in log] == ["create", "delete"],
              "a model that never gets ready isn't waited for forever, and is stopped again")
        pool("policy", "banned=")

        instances = [i["MlxRingInstance"]["shardAssignments"] for i in get("/state")["instances"].values()]
        holders = sorted({n for a in instances for n in a["nodeToRunner"]})
        check(bool(holders), "before the drain, some computer holds part of a model")
        target = next(n for n in POOL["nodes"] if n["id"] == holders[0])
        touching = [a["modelId"] for a in instances if target["id"] in a["nodeToRunner"]]
        log_before = len(get("/_log"))
        pool("drain", target["name"], "--yes")
        state = get("/state")
        on_target = [i for i in state["instances"].values()
                     if target["id"] in i["MlxRingInstance"]["shardAssignments"]["nodeToRunner"]]
        check(not on_target, f"after drain, no model uses the drained computer ({target['name']})")
        deleted = [e["model"] for e in get("/_log")[log_before:] if e["action"] == "delete"]
        check(sorted(deleted) == sorted(touching), "drain stops only the models that used the drained computer")
        check("DRAINED" in pool("nodes"), "nodes shows the drained computer")
        pool("undrain", target["name"])
        check(json.loads(pool("policy"))["excluded_nodes"] == [], "undrain lets the planner use it again")

        pool("policy", "min_gen_tps=1000")
        out = pool("plan")
        check("nothing in the catalog runs above the speed floors" in out and "stop" in out,
              "with impossible speed floors, the plan says so and would stop everything")
        pool("policy", "min_gen_tps=10")

        out = pool("scores")
        check("guessed from size" in out and "≈" in out, "scores are shown, and guesses are marked as estimates")
    finally:
        stub.terminate()
        stub.wait()

print("all loadout checks passed")
