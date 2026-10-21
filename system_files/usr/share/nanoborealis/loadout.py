# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Plans the pool's loadout: which models run, spread over which computers, so each one is fast
enough for an agent and the pool as a whole runs as capable a council as it can.

Pure logic, no network: pool.py feeds it exo's /state and /models, the scores (scores.py) and
what the bench lab measured (benchdb.py), then turns the plan into exo instances.

How exo runs a model, which is what the speed model follows:
  - A model is split into consecutive layers over the computers that hold it (pipeline), each
    taking a share in proportion to its free memory when the model is placed.
  - Writing a token reads every *active* weight once, stage after stage, then the token travels
    round the ring. A mixture-of-experts model reads only its active experts, so a big MoE model
    spread over small computers can still write quickly.
  - Reading a prompt is arithmetic-bound rather than memory-bound.
The rates below are priors; every bench run corrects them (see BenchDB.calibration).
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

GB = 1024**3

# Memory bandwidth (bytes/s) and usable arithmetic (FLOP/s) per backend, before calibration.
# Deliberately modest: consumer GPUs, base Apple chips, dual-channel desktop memory.
PRIORS = {
    "cuda": {"bandwidth": 300e9, "flops": 20e12},
    "metal": {"bandwidth": 120e9, "flops": 8e12},
    "cpu": {"bandwidth": 20e9, "flops": 0.4e12},
}
# Per-token cost of each hop between computers in the ring.
HOP_SECONDS = {"thunderbolt": 0.0005, "ethernet": 0.002, "wifi": 0.010, "unknown": 0.010}
LINK_RANK = {"thunderbolt": 3, "ethernet": 2, "wifi": 1, "unknown": 0}
BACKEND_RANK = {"cuda": 3, "metal": 2, "cpu": 1}


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    backend: str  # cuda | metal | cpu: the best one exo reports for this computer
    link: str  # thunderbolt | ethernet | wifi | unknown: its best network interface
    mem_total: int
    mem_capacity: int  # free for models if the loadout were rebuilt: free now + what served models hold
    # On an NVIDIA node both are its VRAM budget: `pool join` has exo report that (OVERRIDE_MEMORY_MB)
    # instead of system memory, and exo reports it as is, without taking off what's loaded.


@dataclass(frozen=True)
class Model:
    id: str
    size: int  # bytes of weights
    total_b: float  # billions of parameters
    active_b: float  # billions used per token (fewer than total for mixture-of-experts)
    family: str = ""
    quant: str = ""
    context: int = 0

    @property
    def moe(self) -> bool:
        return self.active_b < self.total_b * 0.9

    @property
    def bytes_per_token(self) -> float:
        return self.size * self.active_b / self.total_b if self.total_b else float(self.size)


@dataclass
class Policy:
    min_gen_tps: float = 10.0  # writing slower than this feels broken in an agent (as compute.py)
    min_prompt_tps: float = 150.0  # agent prompts run to thousands of tokens
    headroom: int = 2 * GB  # left free on every computer for its owner
    kv_reserve: int = 2 * GB  # per model, for its context cache, spread like its layers
    max_nodes: int = 4  # computers per model; each extra hop costs every token
    max_models: int = 8
    contention: float = 0.5  # how much two models on one computer slow each other (0: not at all)
    anchor_min: float = 40.0  # the council wants at least one model this capable
    diversity: float = 0.15  # bonus for a model family the council doesn't have yet
    untested_factor: float = 0.85  # models whose tool calls haven't been checked count for less
    pinned: list[str] = field(default_factory=list)  # models to run whenever they fit
    banned: list[str] = field(default_factory=list)  # models never to run
    excluded_nodes: list[str] = field(default_factory=list)  # computers to leave out (drained or paused)

    @classmethod
    def from_dict(cls, data: dict) -> "Policy":
        known = {f for f in cls.__dataclass_fields__}
        policy = cls(**{k: v for k, v in data.items() if k in known and k not in ("headroom", "kv_reserve")})
        for key in ("headroom", "kv_reserve"):  # stored in GB, like people think of them
            if key + "_gb" in data:
                setattr(policy, key, int(float(data[key + "_gb"]) * GB))
        return policy

    def to_dict(self) -> dict:
        data = {k: getattr(self, k) for k in self.__dataclass_fields__ if k not in ("headroom", "kv_reserve")}
        data["headroom_gb"] = round(self.headroom / GB, 2)
        data["kv_reserve_gb"] = round(self.kv_reserve / GB, 2)
        return data


@dataclass(frozen=True)
class Speed:
    gen_tps: float
    prompt_tps: float
    measured: bool = False


@dataclass
class Placement:
    model: Model
    nodes: tuple[str, ...]  # node ids, in exo's ring order is up to exo; this is the set
    shares: dict[str, float]  # node id -> fraction of the model's layers
    speed: Speed
    capability: float
    estimated: bool  # capability is an estimate, not a benchmark score
    tools: bool | None  # tool calls checked: True, failed: False, never checked: None
    value: float = 0.0


@dataclass
class Loadout:
    placements: list[Placement]
    notes: list[str]  # why computers or models were left out, and warnings

    @property
    def value(self) -> float:
        return sum(p.value for p in self.placements)


# -- Reading exo ------------------------------------------------------------------------------------


def unwrap(value):
    """exo tags variants as {"KindName": {...}}; returns (kind, fields)."""
    if isinstance(value, dict) and len(value) == 1:
        kind, inner = next(iter(value.items()))
        if kind[:1].isupper() and isinstance(inner, dict):
            return kind, inner
    return "", value if isinstance(value, dict) else {}


def _bytes(memory) -> int:
    if isinstance(memory, dict):
        return int(memory.get("inBytes") or 0)
    return int(memory or 0)


def _backend(names) -> str:
    found = {"MlxCuda": "cuda", "MlxMetal": "metal", "MlxCpu": "cpu"}
    ranked = [found[n] for n in names or [] if n in found]
    return max(ranked, key=BACKEND_RANK.__getitem__) if ranked else "cpu"


def _link(network) -> str:
    kinds = {"thunderbolt": "thunderbolt", "ethernet": "ethernet", "maybe_ethernet": "ethernet", "wifi": "wifi"}
    found = [kinds.get(str(i.get("interfaceType")), "unknown") for i in (network or {}).get("interfaces") or []
             if isinstance(i, dict) and not str(i.get("ipAddress", "")).startswith(("127.", "::1"))]
    return max(found, key=LINK_RANK.__getitem__) if found else "unknown"


def served(state: dict) -> list[dict]:
    """The instances exo runs: id, model, and each node's share of the model's layers."""
    found = []
    for instance_id, wrapped in (state.get("instances") or {}).items():
        _, inner = unwrap(wrapped)
        assignments = inner.get("shardAssignments") or {}
        node_to_runner = assignments.get("nodeToRunner") or {}
        runner_to_shard = assignments.get("runnerToShard") or {}
        shares = {}
        for node, runner in node_to_runner.items():
            _, shard = unwrap(runner_to_shard.get(runner))
            layers = int(shard.get("endLayer", 0)) - int(shard.get("startLayer", 0))
            total = int((shard.get("modelCard") or {}).get("nLayers") or 0)
            shares[node] = layers / total if total > 0 and layers > 0 else 1 / len(node_to_runner)
        found.append({"id": instance_id, "model": assignments.get("modelId"), "shares": shares,
                      "runners": list(node_to_runner.values())})
    return found


def nodes_from_state(state: dict, sizes: dict[str, int] | None = None) -> list[Node]:
    """Every computer in the pool. `sizes` (model id -> bytes) lets the memory that served models
    hold count as available, since a new loadout may put something else there. An NVIDIA node
    reports its whole VRAM budget however much is loaded, so nothing is added back there."""
    identities = state.get("nodeIdentities") or {}
    memory = state.get("nodeMemory") or {}
    held: dict[str, float] = {}
    for instance in served(state):
        size = (sizes or {}).get(instance["model"] or "", 0)
        for node, share in instance["shares"].items():
            held[node] = held.get(node, 0) + size * share
    nodes = []
    for node in sorted(set(identities) | set(memory)):
        ram = memory.get(node) or {}
        total, free = _bytes(ram.get("ramTotal")), _bytes(ram.get("ramAvailable"))
        name = (identities.get(node) or {}).get("friendlyName") or node[:12]
        backend = _backend((state.get("nodeBackends") or {}).get(node))
        link = _link((state.get("nodeNetwork") or {}).get(node))
        if backend == "cuda":
            nodes.append(Node(node, name, backend, link, free, free))
        else:
            nodes.append(Node(node, name, backend, link, total, min(total, free + int(held.get(node, 0)))))
    return nodes


_MOE = re.compile(r"(\d+(?:\.\d+)?)B-A(\d+(?:\.\d+)?)B", re.I)
_SIZE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)B(?![a-z])", re.I)


def parameters(model_id: str, base_model: str, size: int, quant: str) -> tuple[float, float]:
    """(total, active) billions of parameters, from names like Qwen3-30B-A3B; else from the size."""
    for text in (model_id, base_model):
        moe = _MOE.search(text or "")
        if moe:
            return float(moe.group(1)), float(moe.group(2))
    for text in (base_model, model_id.split("/")[-1]):
        dense = _SIZE.search(text or "")
        if dense:
            total = float(dense.group(1))
            return total, total
    bits = {"3bit": 3, "4bit": 4, "5bit": 5, "6bit": 6, "8bit": 8}.get(quant.lower(), 16)
    total = size * 8 / bits / 1e9
    return total, total


def models_from_list(data: dict) -> list[Model]:
    """Chat models from exo's /models, skipping image and embedding models."""
    models = []
    for row in data.get("data") or []:
        if not isinstance(row, dict) or "TextGeneration" not in (row.get("tasks") or ["TextGeneration"]):
            continue
        size = int(row.get("storage_size_megabytes") or 0) * 1024 * 1024
        if size <= 0:
            continue
        quant = str(row.get("quantization") or "")
        total, active = parameters(str(row["id"]), str(row.get("base_model") or ""), size, quant)
        models.append(Model(str(row["id"]), size, total, active, str(row.get("family") or ""), quant,
                            int(row.get("context_length") or 0)))
    return models


# -- Speed ------------------------------------------------------------------------------------------


def predict(model: Model, nodes: list[Node], shares: dict[str, float], calibration: dict | None = None,
            load: dict[str, int] | None = None, contention: float = 0.0) -> Speed:
    """Tokens per second writing and reading a prompt, for `model` split over `nodes` by `shares`.
    `load`: how many models each node holds counting this one (they share its memory bandwidth)."""
    calibration = calibration or {}
    gen_s = prompt_s = 0.0
    for node in nodes:
        prior = PRIORS[node.backend]
        crowd = 1 + contention * max(0, (load or {}).get(node.id, 1) - 1)
        # This computer's own measurements first (a 2060 isn't a 4070), then its kind of hardware.
        gen_eff = calibration.get(("gen", "node:" + node.id), calibration.get(("gen", node.backend), 1.0))
        prompt_eff = calibration.get(("prompt", "node:" + node.id), calibration.get(("prompt", node.backend), 1.0))
        share = shares[node.id]
        gen_s += model.bytes_per_token * share / (prior["bandwidth"] * gen_eff / crowd)
        prompt_s += 2 * model.active_b * 1e9 * share / (prior["flops"] * prompt_eff / crowd)
    if len(nodes) > 1:
        worst = min((n.link for n in nodes), key=LINK_RANK.__getitem__)
        gen_s += len(nodes) * HOP_SECONDS[worst]  # round the ring once per token
    return Speed(1 / gen_s if gen_s else 0.0, 1 / prompt_s if prompt_s else 0.0)


def shares_for(model: Model, nodes: list[Node], free: dict[str, float]) -> dict[str, float]:
    """exo splits layers in proportion to the memory each node reports when the model is placed:
    what's free on a computer, and on an NVIDIA node its fixed VRAM budget."""
    weight = {n.id: float(n.mem_capacity) if n.backend == "cuda" else max(0.0, free[n.id]) for n in nodes}
    total = sum(weight.values())
    if total <= 0:
        return {n.id: 1 / len(nodes) for n in nodes}
    return {n.id: weight[n.id] / total for n in nodes}


def fits(model: Model, nodes: list[Node], shares: dict[str, float], free: dict[str, float], policy: Policy) -> bool:
    need = model.size + policy.kv_reserve
    return all(need * shares[n.id] <= free[n.id] - policy.headroom for n in nodes)


# -- Planning ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Facts:
    """What the planner knows about each model besides its size."""
    capability: dict[str, tuple[float, bool]]  # model id -> (score 0-100, estimated?)
    tools: dict[str, bool | None]  # model id -> tool calls passed / failed / unchecked
    measured: dict[tuple[str, frozenset], Speed]  # (model id, node ids) -> bench result
    calibration: dict  # ("gen"|"prompt", backend) -> efficiency against the priors


def _value(model: Model, capability: float, tools: bool | None, families: set[str], policy: Policy) -> float:
    value = capability
    if tools is None:
        value *= policy.untested_factor
    if model.family and model.family not in families:
        value *= 1 + policy.diversity
    return value


class _Pool:
    """Memory left on each node, and how many models each holds, while a plan is built."""

    def __init__(self, nodes: list[Node]):
        self.free = {n.id: float(n.mem_capacity) for n in nodes}
        self.load = {n.id: 0 for n in nodes}

    def take(self, placement: Placement, policy: Policy) -> None:
        need = placement.model.size + policy.kv_reserve
        for node_id, share in placement.shares.items():
            self.free[node_id] -= need * share
            self.load[node_id] += 1


def _evaluate(model: Model, group: list[Node], pool: _Pool, facts: Facts, policy: Policy,
              families: set[str]) -> Placement | None:
    shares = shares_for(model, group, pool.free)
    if not fits(model, group, shares, pool.free, policy):
        return None
    ids = frozenset(n.id for n in group)
    load = {n.id: pool.load[n.id] + 1 for n in group}
    measured = facts.measured.get((model.id, ids))
    if measured and max(load.values()) == 1:
        speed = measured
    else:
        speed = predict(model, group, shares, facts.calibration, load, policy.contention)
    if speed.gen_tps < policy.min_gen_tps or speed.prompt_tps < policy.min_prompt_tps:
        return None
    capability, estimated = facts.capability.get(model.id, (0.0, True))
    tools = facts.tools.get(model.id)
    placement = Placement(model, tuple(sorted(ids)), shares, speed, capability, estimated, tools)
    placement.value = _value(model, capability, tools, families, policy)
    return placement


def _still_fast(placements: list[Placement], pool: _Pool, nodes: dict[str, Node], facts: Facts,
                policy: Policy, extra: Placement) -> bool:
    """Adding `extra` mustn't push a model already placed on the same computers below the floors."""
    touched = set(extra.nodes)
    for placement in placements:
        if touched.isdisjoint(placement.nodes):
            continue
        load = {i: pool.load[i] + (1 if i in touched else 0) for i in placement.nodes}
        speed = predict(placement.model, [nodes[i] for i in placement.nodes], placement.shares,
                        facts.calibration, load, policy.contention)
        if speed.gen_tps < policy.min_gen_tps or speed.prompt_tps < policy.min_prompt_tps:
            return False
    return True


def _groups(nodes: list[Node], policy: Policy) -> list[list[Node]]:
    """Candidate sets of computers for one model: up to max_nodes of them, fastest backends first."""
    ordered = sorted(nodes, key=lambda n: (-BACKEND_RANK[n.backend], -n.mem_capacity, n.name))
    groups = []
    for size in range(1, min(policy.max_nodes, len(ordered)) + 1):
        groups.extend(list(g) for g in itertools.combinations(ordered, size))
    return groups


HOP_COST = 0.25  # what one more computer in a model's ring costs, in cost() units


def cost(placement: Placement, nodes: dict[str, Node]) -> float:
    """How much of the pool's scarce hardware a placement uses: its share of each computer, weighted
    by how fast that computer is (a GPU is worth more than a laptop's processor), plus its hops."""
    weight = sum(placement.shares[i] * BACKEND_RANK[nodes[i].backend] for i in placement.nodes)
    return weight + HOP_COST * (len(placement.nodes) - 1)


def _better(a: Placement, b: Placement, nodes: dict[str, Node]) -> bool:
    """The more valuable model wins; for the same value, the one that leaves the fast hardware free
    for others. Any placement here already clears the speed floors, so more speed isn't worth a GPU."""
    if abs(a.value - b.value) > 1e-9:
        return a.value > b.value
    return (cost(a, nodes), -a.speed.gen_tps) < (cost(b, nodes), -b.speed.gen_tps)


def _greedy(models: list[Model], nodes: list[Node], facts: Facts, policy: Policy,
            start: list[Placement] | None = None, skip: set[str] | None = None) -> list[Placement]:
    by_id = {n.id: n for n in nodes}
    pool = _Pool(nodes)
    placements: list[Placement] = []
    for placement in start or []:
        pool.take(placement, policy)
        placements.append(placement)
    groups = _groups(nodes, policy)

    def ceiling(model: Model) -> float:  # the most this model could be worth
        return facts.capability.get(model.id, (0.0, True))[0] * (1 + policy.diversity)

    ordered = sorted(models, key=ceiling, reverse=True)
    pinned = [m for m in ordered if m.id in policy.pinned]
    rest = [m for m in ordered if m.id not in policy.pinned]
    while len(placements) < policy.max_models:
        taken = {p.model.id for p in placements} | (skip or set())
        families = {p.model.family for p in placements if p.model.family}
        best: Placement | None = None
        for pass_models in (pinned, rest):
            for model in pass_models:
                if model.id in taken:
                    continue
                if best is not None and pass_models is rest and ceiling(model) < best.value:
                    break  # sorted by ceiling: nothing further down can beat what's found
                for group in groups:
                    candidate = _evaluate(model, group, pool, facts, policy, families)
                    if candidate is None or not _still_fast(placements, pool, by_id, facts, policy, candidate):
                        continue
                    if best is None or _better(candidate, best, by_id):
                        best = candidate
            if best is not None:
                break  # a pinned model that fits goes in before anything else
        if best is None:
            break
        pool.take(best, policy)
        placements.append(best)
    return placements


def excluded(node: Node, policy: Policy) -> bool:
    return node.id in policy.excluded_nodes or node.name in policy.excluded_nodes


def running(instances: list[dict], nodes: list[Node], models: list[Model], facts: Facts,
            policy: Policy) -> list[Placement]:
    """Served instances (see served()) that can stay: every computer still usable, the model neither
    banned nor failing the tool-call check, and its speed (measured, else predicted) above the floors."""
    by_node = {n.id: n for n in nodes}
    by_model = {m.id: m for m in models}
    kept: list[Placement] = []
    families: set[str] = set()
    for instance in instances:
        model = by_model.get(instance["model"] or "")
        ids = list(instance["shares"])
        if (model is None or model.id in policy.banned or facts.tools.get(model.id) is False or not ids
                or any(i not in by_node or excluded(by_node[i], policy) for i in ids)):
            continue
        group = [by_node[i] for i in ids]
        speed = facts.measured.get((model.id, frozenset(ids))) or predict(
            model, group, instance["shares"], facts.calibration)
        if speed.gen_tps < policy.min_gen_tps or speed.prompt_tps < policy.min_prompt_tps:
            continue
        capability, estimated = facts.capability.get(model.id, (0.0, True))
        tools = facts.tools.get(model.id)
        placement = Placement(model, tuple(sorted(ids)), dict(instance["shares"]), speed, capability, estimated, tools)
        placement.value = _value(model, capability, tools, families, policy)
        families.add(model.family)
        kept.append(placement)
    return kept


def plan(nodes: list[Node], models: list[Model], facts: Facts, policy: Policy,
         keep: list[Placement] | None = None) -> Loadout:
    """The most capable council the pool can run with every model above the speed floors.
    `keep`: models already running that stay where they are (see running()); the rest of the pool
    is filled around them. Without it, the whole loadout is planned from scratch."""
    notes: list[str] = []
    usable = [n for n in nodes if not excluded(n, policy)]
    candidates = [m for m in models if m.id not in policy.banned and facts.tools.get(m.id) is not False]
    for model in models:
        if facts.tools.get(model.id) is False and model.id not in policy.banned:
            notes.append(f"{model.id}: left out, its tool calls failed the bench check")
    fixed = list(keep or [])
    placements = _greedy(candidates, usable, facts, policy, start=fixed)
    # Swap-improvement: drop each new model in turn and refill; keep what's worth more.
    for _ in range(3):
        current = sum(p.value for p in placements)
        for i in range(len(fixed), len(placements)):
            if placements[i].model.id in policy.pinned:
                continue  # pinned models stay
            kept = placements[:i] + placements[i + 1:]
            refilled = _greedy(candidates, usable, facts, policy, start=kept, skip={placements[i].model.id})
            # Then the dropped model may come back, wherever the refill left room for it.
            refilled = _greedy(candidates, usable, facts, policy, start=refilled)
            if sum(p.value for p in refilled) > current + 1e-6:
                placements = refilled
                break
        else:
            break
    if not placements:
        notes.append("nothing in the catalog runs above the speed floors on these computers")
    elif max(p.capability for p in placements) < policy.anchor_min:
        notes.append(f"no model reaches the anchor capability ({policy.anchor_min:.0f}); "
                     "hard tasks will need the cloud models")
    used = {i for p in placements for i in p.nodes}
    for node in usable:
        if node.id not in used:
            notes.append(f"{node.name}: not used ({node.backend}, {node.mem_capacity / GB:.1f} GB free); "
                         "nothing it could hold, alone or with others, stays above the speed floors")
    for pinned in policy.pinned:
        if pinned not in {p.model.id for p in placements}:
            notes.append(f"{pinned}: pinned, but it doesn't fit above the speed floors")
    return Loadout(placements, notes)


def diff(loadout: Loadout, running: list[dict]) -> tuple[list[dict], list[Placement], list[Placement]]:
    """(instances to stop, placements to start, placements already running)."""
    wanted = {(p.model.id, frozenset(p.nodes)): p for p in loadout.placements}
    stop, keep = [], []
    for instance in running:
        key = (instance["model"], frozenset(instance["shares"]))
        if key in wanted:
            keep.append(wanted.pop(key))
        else:
            stop.append(instance)
    return stop, list(wanted.values()), keep


def describe(loadout: Loadout, nodes: list[Node]) -> list[str]:
    names = {n.id: n.name for n in nodes}
    lines = []
    for p in sorted(loadout.placements, key=lambda p: -p.capability):
        where = ", ".join(f"{names.get(i, i[:12])} {p.shares[i]:.0%}" for i in p.nodes)
        score = f"{'≈' if p.estimated else ''}{p.capability:.0f}"
        speed = f"{p.speed.gen_tps:.0f} tok/s writing, {p.speed.prompt_tps:.0f} reading"
        speed += "" if p.speed.measured else " (predicted)"
        tools = {True: "tools ok", False: "tools FAILED", None: "tools unchecked"}[p.tools]
        lines.append(f"  {p.model.id}  capability {score}  {speed}  {tools}")
        lines.append(f"      on {where}  ({p.model.size / GB:.1f} GB{', MoE' if p.model.moe else ''})")
    return lines
