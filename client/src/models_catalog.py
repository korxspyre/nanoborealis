# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The OpenRouter model catalog: every model, searchable, with what it costs.

OpenRouter's list is public (no key): https://openrouter.ai/api/v1/models. A model is free when
every price on it is zero. OpenRouter gives most free models a ":free" suffix, and the same model
without the suffix is billed, so an id is only ever shown with its suffix. The agent works through
tools, so a model that can't call tools can't drive it.

Nothing here imports Flet, and fetch() blocks: run it in a thread.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

CATALOG_URL = "https://openrouter.ai/api/v1/models"
_PRICE_KEYS = ("prompt", "completion", "request", "image", "web_search", "internal_reasoning")


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    description: str
    context: int
    prompt_price: float  # dollars per token; -1 when OpenRouter doesn't say (routers)
    completion_price: float
    free: bool
    tools: bool
    reasoning: bool
    vision: bool
    created: int

    @property
    def maker(self) -> str:
        return self.id.split("/", 1)[0]

    @property
    def short_name(self) -> str:
        """The name without its maker ("NVIDIA: Nemotron 3 Ultra (free)" -> "Nemotron 3 Ultra")."""
        name = self.name.split(": ", 1)[-1]
        return name[: -len(" (free)")] if name.endswith(" (free)") else name

    def price_label(self) -> str:
        if self.free:
            return "Free"
        if self.prompt_price < 0 or self.completion_price < 0:
            return "Price varies"
        return f"${per_million(self.prompt_price)} in · ${per_million(self.completion_price)} out per 1M tokens"

    def context_label(self) -> str:
        if self.context >= 1_000_000:
            return f"{self.context / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M context"
        return f"{self.context // 1000}K context" if self.context else ""

    def matches(self, words: list[str]) -> bool:
        haystack = f"{self.id} {self.name} {self.description}".lower()
        return all(word in haystack for word in words)


def per_million(price: float) -> str:
    value = price * 1_000_000
    return f"{value:.2f}" if value >= 0.1 else f"{value:.3f}".rstrip("0").rstrip(".")


def _price(pricing: dict[str, Any], key: str) -> float:
    try:
        return float(pricing.get(key) or 0)
    except (TypeError, ValueError):
        return -1.0


def parse(data: dict[str, Any]) -> list[Model]:
    models = []
    for raw in data.get("data") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        pricing = raw.get("pricing") or {}
        prices = [_price(pricing, key) for key in _PRICE_KEYS]
        params = raw.get("supported_parameters") or []
        inputs = (raw.get("architecture") or {}).get("input_modalities") or []
        models.append(Model(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            description=str(raw.get("description") or ""),
            context=int(raw.get("context_length") or 0),
            prompt_price=prices[0],
            completion_price=prices[1],
            free=all(price == 0 for price in prices),
            tools="tools" in params,
            reasoning="reasoning" in params or "include_reasoning" in params,
            vision="image" in inputs,
            created=int(raw.get("created") or 0),
        ))
    return models


def fetch(timeout: float = 20) -> list[Model]:
    request = urllib.request.Request(CATALOG_URL, headers={"Accept": "application/json",
                                                           "User-Agent": "NanoBorealis"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return parse(json.loads(response.read()))


class Catalog:
    """The list, fetched once per run (it's ~1 MB) and again when asked for after an hour."""

    def __init__(self) -> None:
        self.models: list[Model] = []
        self.by_id: dict[str, Model] = {}
        self.fetched = 0.0

    def load(self, force: bool = False) -> list[Model]:
        if force or not self.models or time.time() - self.fetched > 3600:
            self.models = fetch()
            self.by_id = {m.id: m for m in self.models}
            self.fetched = time.time()
        return self.models

    def get(self, model_id: str) -> Model | None:
        return self.by_id.get(model_id)

    def search(self, query: str = "", *, free_only: bool = True, tools_only: bool = True,
               sort: str = "newest") -> list[Model]:
        words = query.lower().split()
        found = [m for m in self.models
                 if (not free_only or m.free) and (not tools_only or m.tools) and m.matches(words)]
        if sort == "context":
            found.sort(key=lambda m: -m.context)
        elif sort == "price":
            found.sort(key=lambda m: (m.prompt_price + m.completion_price if m.prompt_price >= 0 else 1e9))
        elif sort == "name":
            found.sort(key=lambda m: m.name.lower())
        else:
            found.sort(key=lambda m: -m.created)
        return found


def label(model_id: str, catalog: Catalog | None = None) -> str:
    """A short name for a model id, with "free" said out loud: the same name without ":free" is
    a different, billed model."""
    model = catalog.get(model_id) if catalog else None
    name = model.short_name if model else model_id.rsplit("/", 1)[-1].split(":", 1)[0]
    free = model.free if model else model_id.endswith(":free")
    return f"{name} · free" if free else name
