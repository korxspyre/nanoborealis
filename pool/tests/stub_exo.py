# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""A stand-in for an exo pool, for checking `nanoborealis pool`'s loadout commands without the
hardware: the same API shapes as exo (/state, /models, /instance/previews, POST /instance, DELETE
/instance/<id>, /bench/chat/completions, /v1/chat/completions), over a pool described in JSON.

    python3 stub_exo.py <port> <pool.json>

pool.json: {"nodes": [{"id", "name", "backend": "MlxCuda"|"MlxMetal"|"MlxCpu", "link", "ram_gb"}],
                       (an MlxCuda node's ram_gb is its VRAM budget, reported as is, like exo with
                       OVERRIDE_MEMORY_MB, however much is loaded)
            "models": [{"id", "size_mb", "family", "quantization", "base_model", "n_layers"}],
            "truth": {"cpu": 0.5, ...},  # how the real speed compares with the planner's priors
            "bad_tools": [model ids that never make a tool call]}
Placement is instant, and GET /_log lists the instance changes made so far.
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "system_files/usr/share/nanoborealis"))
import loadout  # noqa: E402

GB = 1024**3
LOCK = threading.Lock()


class Pool:
    def __init__(self, spec: dict):
        self.spec = spec
        self.nodes = {n["id"]: n for n in spec["nodes"]}
        self.free = {n["id"]: int(n["ram_gb"] * GB) for n in spec["nodes"]}
        self.models = {m["id"]: m for m in spec["models"]}
        self.instances: dict[str, dict] = {}
        self.log: list[dict] = []

    def reported(self, node_id: str) -> int:
        node = self.nodes[node_id]
        return int(node["ram_gb"] * GB) if node["backend"] == "MlxCuda" else self.free[node_id]

    def size(self, model_id: str) -> int:
        return int(self.models[model_id]["size_mb"]) * 1024 * 1024

    def state(self) -> dict:
        runners = {}
        wrapped = {}
        for iid, inst in self.instances.items():
            wrapped[iid] = {"MlxRingInstance": inst}
            stuck = inst["shardAssignments"]["modelId"] in self.spec.get("stuck", [])  # never gets ready
            for runner in inst["shardAssignments"]["nodeToRunner"].values():
                runners[runner] = {"RunnerWarmingUp": {}} if stuck else {"RunnerReady": {}}
        return {
            "instances": wrapped, "runners": runners, "downloads": {},
            "nodeIdentities": {i: {"friendlyName": n["name"]} for i, n in self.nodes.items()},
            "nodeMemory": {i: {"ramTotal": {"inBytes": int(n["ram_gb"] * GB)}, "ramAvailable": {"inBytes": self.reported(i)}}
                           for i, n in self.nodes.items()},
            "nodeBackends": {i: [n["backend"], "MlxCpu"] if n["backend"] != "MlxCpu" else ["MlxCpu"]
                             for i, n in self.nodes.items()},
            "nodeNetwork": {i: {"interfaces": [{"name": "eth0", "ipAddress": f"10.0.0.{k + 2}",
                                                "interfaceType": n.get("link", "ethernet")}]}
                            for k, (i, n) in enumerate(self.nodes.items())},
            "lastSeen": {i: "2026-09-28T12:00:00Z" for i in self.nodes},
        }

    def catalog(self) -> dict:
        return {"data": [{"id": m["id"], "storage_size_megabytes": m["size_mb"], "tasks": ["TextGeneration"],
                          "family": m.get("family", ""), "quantization": m.get("quantization", ""),
                          "base_model": m.get("base_model", ""), "context_length": 32768,
                          "capabilities": ["text"]} for m in self.models.values()]}

    def instance(self, model_id: str, node_ids: list[str]) -> dict:
        layers = int(self.models[model_id].get("n_layers", 48))
        total = sum(self.reported(i) for i in node_ids) or 1
        counts = [max(1, round(layers * self.reported(i) / total)) for i in node_ids]
        counts[-1] = layers - sum(counts[:-1])
        iid = uuid.uuid4().hex
        node_to_runner, runner_to_shard, start = {}, {}, 0
        for node, count in zip(node_ids, counts):
            runner = uuid.uuid4().hex
            node_to_runner[node] = runner
            runner_to_shard[runner] = {"PipelineShardMetadata": {
                "startLayer": start, "endLayer": start + count, "modelCard": {"modelId": model_id, "nLayers": layers}}}
            start += count
        return {"instanceId": iid, "shardAssignments": {"modelId": model_id, "nodeToRunner": node_to_runner,
                                                         "runnerToShard": runner_to_shard}}

    def previews(self, model_id: str, required: list[str]) -> list[dict]:
        found = []
        if model_id not in self.models:
            return [{"model_id": model_id, "sharding": "Pipeline", "instance_meta": "MlxRing", "error": "unknown model"}]
        others = sorted((i for i in self.nodes if i not in required), key=lambda i: -self.reported(i))
        for count in range(max(1, len(required)), len(self.nodes) + 1):
            group = list(required) + others[:count - len(required)]
            if sum(self.reported(i) for i in group) < self.size(model_id):
                found.append({"model_id": model_id, "sharding": "Pipeline", "instance_meta": "MlxRing",
                              "instance": None, "error": "not enough memory"})
                continue
            inst = self.instance(model_id, group)
            for sharding in ("Pipeline", "Tensor"):
                found.append({"model_id": model_id, "sharding": sharding, "instance_meta": "MlxRing",
                              "instance": {"MlxRingInstance": inst}, "error": None})
        return found

    def create(self, wrapped: dict) -> None:
        _, inst = loadout.unwrap(wrapped)
        model_id = inst["shardAssignments"]["modelId"]
        for node, share in self.shares(inst).items():
            self.free[node] -= int(self.size(model_id) * share)
        self.instances[inst["instanceId"]] = inst
        self.log.append({"action": "create", "model": model_id, "nodes": sorted(inst["shardAssignments"]["nodeToRunner"])})

    def delete(self, iid: str) -> bool:
        inst = self.instances.pop(iid, None)
        if inst is None:
            return False
        model_id = inst["shardAssignments"]["modelId"]
        for node, share in self.shares(inst).items():
            self.free[node] += int(self.size(model_id) * share)
        self.log.append({"action": "delete", "model": model_id, "nodes": sorted(inst["shardAssignments"]["nodeToRunner"])})
        return True

    @staticmethod
    def shares(inst: dict) -> dict[str, float]:
        assignments = inst["shardAssignments"]
        out = {}
        for node, runner in assignments["nodeToRunner"].items():
            _, shard = loadout.unwrap(assignments["runnerToShard"][runner])
            out[node] = (shard["endLayer"] - shard["startLayer"]) / shard["modelCard"]["nLayers"]
        return out

    def speed(self, model_id: str) -> tuple[float, float]:
        inst = next((i for i in self.instances.values() if i["shardAssignments"]["modelId"] == model_id), None)
        if inst is None:
            raise KeyError(model_id)
        state = self.state()
        sizes = {m: self.size(m) for m in self.models}
        nodes = {n.id: n for n in loadout.nodes_from_state(state, sizes)}
        model = next(m for m in loadout.models_from_list(self.catalog()) if m.id == model_id)
        shares = self.shares(inst)
        truth = {(kind, b): f for b, f in self.spec.get("truth", {}).items() for kind in ("gen", "prompt")}
        speed = loadout.predict(model, [nodes[i] for i in shares], shares, truth)
        return speed.gen_tps, speed.prompt_tps


POOL: Pool


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        url = urlsplit(self.path)
        with LOCK:
            if url.path == "/node_id":
                self.reply(200, "stub-node")
            elif url.path == "/state":
                self.reply(200, POOL.state())
            elif url.path in ("/models", "/v1/models"):
                self.reply(200, POOL.catalog())
            elif url.path == "/instance/previews":
                query = parse_qs(url.query)
                self.reply(200, {"previews": POOL.previews(query["model_id"][0], query.get("node_ids", []))})
            elif url.path == "/_log":
                self.reply(200, POOL.log)
            else:
                self.reply(404, {"detail": "not found"})

    def do_POST(self):
        url = urlsplit(self.path)
        body = self.body()
        with LOCK:
            if url.path == "/instance":
                POOL.create(body["instance"])
                self.reply(200, {"message": "Command received."})
            elif url.path == "/place_instance":
                previews = [p for p in POOL.previews(body["model_id"], []) if p.get("instance")]
                if not previews:
                    self.reply(400, {"detail": "no placement"})
                    return
                POOL.create(previews[0]["instance"])
                self.reply(200, {"message": "Command received."})
            elif url.path == "/_stuck":  # the checks' handle: this model's runners stay warming up
                POOL.spec.setdefault("stuck", []).append(body["model"])
                self.reply(200, {})
            elif url.path == "/bench/chat/completions":
                try:
                    gen, prompt = POOL.speed(body["model"])
                except KeyError:
                    self.reply(404, {"detail": "no instance"})
                    return
                tokens = sum(len(m.get("content", "")) for m in body.get("messages", [])) // 4
                self.reply(200, {"choices": [{"message": {"role": "assistant", "content": "It merges intervals."}}],
                                 "generation_stats": {"prompt_tps": round(prompt, 2), "generation_tps": round(gen, 2),
                                                      "prompt_tokens": tokens, "generation_tokens": 8,
                                                      "peak_memory_usage": {"inBytes": 0}}})
            elif url.path == "/v1/chat/completions":
                if not any(i["shardAssignments"]["modelId"] == body.get("model") for i in POOL.instances.values()):
                    self.reply(404, {"detail": "no instance"})
                    return
                message = {"role": "assistant", "content": "OK"}
                if body.get("tools") and body.get("model") not in POOL.spec.get("bad_tools", []):
                    message = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "call_1", "type": "function",
                        "function": {"name": "get_weather", "arguments": json.dumps({"city": "Paris"})}}]}
                self.reply(200, {"choices": [{"index": 0, "message": message}]})
            else:
                self.reply(404, {"detail": "not found"})

    def do_DELETE(self):
        url = urlsplit(self.path)
        with LOCK:
            if url.path.startswith("/instance/") and POOL.delete(url.path.rsplit("/", 1)[1]):
                self.reply(200, {"message": "Command received."})
            else:
                self.reply(404, {"detail": "no such instance"})


if __name__ == "__main__":
    POOL = Pool(json.loads(Path(sys.argv[2]).read_text()))
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
