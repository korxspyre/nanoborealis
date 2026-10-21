# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""What the bench lab has measured on this pool, and what the loadout planner learns from it.

Every run records the model, the computers holding it, the speeds exo reported, whether the
model made a correct tool call, and what the planner's priors predicted. The planner then uses a
measured speed wherever the same model ran on the same computers, and elsewhere scales its
priors per backend by how the measurements compared with them.
"""

from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path

from loadout import Facts, Speed

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    at REAL NOT NULL,
    engine TEXT NOT NULL,          -- exo's commit, so an engine update can be told apart
    model TEXT NOT NULL,
    nodes TEXT NOT NULL,           -- node ids, sorted, comma-separated
    names TEXT NOT NULL,           -- their names then, for people reading the table
    backends TEXT NOT NULL,        -- the set of backends involved, sorted, comma-separated
    prompt_tokens INTEGER NOT NULL,
    prompt_tps REAL NOT NULL,
    gen_tps REAL NOT NULL,
    predicted_prompt_tps REAL,     -- the priors' guess, uncalibrated
    predicted_gen_tps REAL,
    tools INTEGER                  -- 1: made the tool call, 0: didn't, NULL: not checked
);
CREATE INDEX IF NOT EXISTS runs_model_nodes ON runs (model, nodes);
"""


class BenchDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def record(self, *, engine: str, model: str, nodes: dict[str, str], backends: set[str],
               prompt_tokens: int, prompt_tps: float, gen_tps: float, tools: bool | None,
               predicted: Speed | None = None) -> None:
        ids = sorted(nodes)
        self.db.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), engine, model, ",".join(ids), ",".join(nodes[i] for i in ids),
             ",".join(sorted(backends)), prompt_tokens, prompt_tps, gen_tps,
             predicted.prompt_tps if predicted else None, predicted.gen_tps if predicted else None,
             None if tools is None else int(tools)))
        self.db.commit()

    def latest(self) -> list[sqlite3.Row]:
        """The newest run for every (model, computers) pair, fastest first."""
        self.db.row_factory = sqlite3.Row
        try:
            return self.db.execute(
                "SELECT * FROM runs r WHERE at = (SELECT MAX(at) FROM runs WHERE model = r.model AND nodes = r.nodes) "
                "ORDER BY gen_tps DESC").fetchall()
        finally:
            self.db.row_factory = None

    def tools(self) -> dict[str, bool | None]:
        """Per model, the newest tool-call check: passed, failed, or never checked."""
        found: dict[str, bool | None] = {}
        for model, tools in self.db.execute(
                "SELECT model, tools FROM runs WHERE tools IS NOT NULL ORDER BY at"):
            found[model] = bool(tools)
        return found

    def measured(self) -> dict[tuple[str, frozenset], Speed]:
        return {(row["model"], frozenset(row["nodes"].split(","))): Speed(row["gen_tps"], row["prompt_tps"], True)
                for row in self.latest()}

    def calibration(self) -> dict:
        """The geometric mean of measured/predicted per backend, over runs on that backend alone, and
        per computer, over runs on that computer alone (runs that mix them can't say which one was
        slow, so they only count as measurements)."""
        ratios: dict[tuple[str, str], list[float]] = {}
        for nodes, backends, gen, prompt, pgen, pprompt in self.db.execute(
                "SELECT nodes, backends, gen_tps, prompt_tps, predicted_gen_tps, predicted_prompt_tps FROM runs"):
            keys = [] if "," in backends else [backends]
            if "," not in nodes:
                keys.append("node:" + nodes)
            for kind, got, guess in (("gen", gen, pgen), ("prompt", prompt, pprompt)):
                if got and guess and got > 0 and guess > 0:
                    for key in keys:
                        ratios.setdefault((kind, key), []).append(math.log(got / guess))
        return {key: math.exp(sum(v) / len(v)) for key, v in ratios.items()}

    def facts(self, capability: dict[str, tuple[float, bool]]) -> Facts:
        return Facts(capability, self.tools(), self.measured(), self.calibration())
