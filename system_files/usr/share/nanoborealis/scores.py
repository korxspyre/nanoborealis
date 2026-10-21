# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""How capable each model is, so the planner knows which to run and the council knows whose word
counts for more.

Three sources, best first:
  - overrides.json in the pool's state folder: scores you set yourself, {model id: 0-100}.
  - LiveBench (livebench.ai), saved by `refresh` into livebench.json: an overall score and one per
    category (coding, reasoning, math...). A model's quantized build scores a little under its
    full-precision one.
  - A guess from the model's size, for models LiveBench doesn't list. It is marked as estimated
    (≈) everywhere it's shown, and the bench lab's measurements are what to trust instead.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import time
import urllib.request
from pathlib import Path

from loadout import Model

SITE = "https://livebench.ai"
QUANT_FACTOR = {"3bit": 0.90, "4bit": 0.97, "5bit": 0.98, "6bit": 0.99}  # small, deliberately cautious
_NOISE = {"mlx", "community", "instruct", "it", "chat", "hf", "gguf", "dwq", "mxfp4", "bf16", "fp16",
          "8bit", "6bit", "5bit", "4bit", "3bit", "gs32", "gs64", "q4", "q8", "latest",
          "free", "effort", "auto", "low", "medium", "high", "xhigh"}  # OpenRouter's :free, LiveBench's effort


def tokens(name: str) -> set[str]:
    """What identifies a model in a name: family, version and sizes, without the packaging."""
    name = name.split("/")[-1].lower()
    return {t for t in re.split(r"[^a-z0-9.]+", name) if t and t not in _NOISE}


def _numbered(words: set[str]) -> set[str]:
    """The parts that pin a model down: versions and sizes (qwen3.6, k2.6, 27b, a3b, v4), not date
    stamps (0731, 20251101), which only tell releases of the same model apart."""
    return {w for w in words if re.search(r"\d", w) and not re.fullmatch(r"\d{4,}", w)}


def match(model_id: str, names: list[str]) -> str | None:
    """The LiveBench entry for a model: exactly the same versions and sizes, then the most
    identifying words in common. GLM 4.7 is never scored as GLM 5.3, nor Kimi K2 as K2.6."""
    want = tokens(model_id)
    best, best_score = None, 0.0
    for name in names:
        have = tokens(name)
        if _numbered(want) != _numbered(have):
            continue
        overlap = len(want & have) / len(want | have)
        if overlap > best_score:
            best, best_score = name, overlap
    return best if best_score >= 0.5 else None


def guess(model: Model) -> float:
    """A rough score from size alone. Mixture-of-experts models count as the dense model of
    sqrt(total x active) billion parameters, a common rule of thumb."""
    effective = math.sqrt(model.total_b * model.active_b) if model.moe else model.total_b
    score = 10 + 10 * math.log2(max(effective, 0.5))
    return round(max(5.0, min(60.0, score)) * QUANT_FACTOR.get(model.quant.lower(), 1.0), 1)


def load(state_dir: Path) -> tuple[dict, dict]:
    """(LiveBench rows {name: {"overall": x, "categories": {...}}}, overrides {model id: x})."""
    def read(name: str) -> dict:
        try:
            data = json.loads((state_dir / name).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}
    return read("livebench.json").get("models", {}), read("overrides.json")


def capabilities(models: list[Model], state_dir: Path) -> dict[str, tuple[float, bool]]:
    """model id -> (score 0-100, estimated?) for the planner."""
    livebench, overrides = load(state_dir)
    names = list(livebench)
    found = {}
    for model in models:
        if isinstance(overrides.get(model.id), (int, float)):
            found[model.id] = (float(overrides[model.id]), False)
            continue
        name = match(model.id, names)
        if name is not None:
            score = float(livebench[name]["overall"]) * QUANT_FACTOR.get(model.quant.lower(), 1.0)
            found[model.id] = (round(score, 1), model.quant.lower() in QUANT_FACTOR)
        else:
            found[model.id] = (guess(model), True)
    return found


def details(model: Model, state_dir: Path) -> dict:
    """What a nametape shows: the score, where it came from, and the per-category scores."""
    livebench, overrides = load(state_dir)
    if isinstance(overrides.get(model.id), (int, float)):
        return {"overall": float(overrides[model.id]), "source": "set by you", "categories": {}}
    name = match(model.id, list(livebench))
    if name is None:
        return {"overall": guess(model), "source": "guessed from size", "categories": {}}
    factor = QUANT_FACTOR.get(model.quant.lower(), 1.0)
    row = livebench[name]
    return {"overall": round(row["overall"] * factor, 1), "source": f"LiveBench: {name}",
            "categories": {k: round(v * factor, 1) for k, v in row.get("categories", {}).items()}}


# -- Refreshing from LiveBench ---------------------------------------------------------------------


def parse(table_csv: str, categories: dict[str, list[str]]) -> dict[str, dict]:
    """LiveBench's table: one row per model, one column per task. A category's score is the mean of
    its tasks, and the overall score the mean of the categories, as the leaderboard computes it."""
    rows = {}
    for row in csv.DictReader(io.StringIO(table_csv)):
        name = (row.get("model") or "").strip()
        if not name:
            continue
        per_category = {}
        for category, tasks in categories.items():
            values = []
            for task in tasks:
                try:
                    values.append(float(row[task]))
                except (KeyError, TypeError, ValueError):
                    pass
            if values:
                per_category[category] = round(sum(values) / len(values), 2)
        if per_category:
            rows[name] = {"overall": round(sum(per_category.values()) / len(per_category), 2),
                          "categories": per_category}
    return rows


def _get(url: str, timeout: float = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "nanoborealis-pool"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def releases(page_script: str) -> list[str]:
    """The leaderboard's release dates, newest first, as its page script names them."""
    dates = set(re.findall(r'"(20\d\d-\d\d-\d\d)"', page_script))
    return sorted(dates, reverse=True)


def refresh(state_dir: Path) -> tuple[str, int]:
    """Download the newest LiveBench leaderboard into livebench.json. Returns (release, models)."""
    page = _get(SITE + "/").decode("utf-8", "replace")
    scripts = re.findall(r'src="\.?(/static/js/main\.[0-9a-f]+\.js)"', page)
    if not scripts:
        raise RuntimeError("LiveBench's page has changed; its data could not be found")
    script = _get(SITE + scripts[0]).decode("utf-8", "replace")
    for release in releases(script):
        stem = release.replace("-", "_")
        try:
            table = _get(f"{SITE}/table_{stem}.csv").decode("utf-8")
            categories = json.loads(_get(f"{SITE}/categories_{stem}.json"))
        except (OSError, ValueError):
            continue  # not every date in the script is a published release
        rows = parse(table, categories)
        if rows:
            state_dir.mkdir(parents=True, exist_ok=True)
            out = {"release": release, "fetched": time.time(), "models": rows}
            tmp = state_dir / "livebench.json.tmp"
            tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
            tmp.replace(state_dir / "livebench.json")
            return release, len(rows)
    raise RuntimeError("no LiveBench release could be downloaded")


MAX_AGE = 14 * 86400  # LiveBench publishes a release every month or so
RETRY_AFTER = 86400  # after a failed download (offline, site changed), try again a day later


def ensure(state_dir: Path) -> None:
    """Refresh livebench.json when it's missing or old, quietly: a pool that can't reach LiveBench
    plans with what it has (or the size guesses), and tries again a day later."""
    saved, tried = state_dir / "livebench.json", state_dir / "livebench.tried"
    now = time.time()
    try:
        if saved.exists() and now - saved.stat().st_mtime < MAX_AGE:
            return
        if tried.exists() and now - tried.stat().st_mtime < RETRY_AFTER:
            return
        state_dir.mkdir(parents=True, exist_ok=True)
        tried.touch()
        refresh(state_dir)
    except (OSError, RuntimeError, ValueError):
        pass

