# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The agent's own settings, through nanobot's WebUI settings API (GET /api/settings and the
settings.* management requests): its models, the order it tries them in, and whether OpenRouter
may bill it at all.

nanobot switches models by preset: a named model with its settings. A chat picks one with
"/model <name>"; the agent's default is the first in its call order, the rest are its fallbacks.
Any OpenRouter model gets a preset of its own the first time it's chosen.

Every function here takes a connected AgentLink. Nothing here imports Flet.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_link import AgentLink
from models_catalog import Model

CONTEXT_SIZES = (65536, 200000, 262144, 500000, 1048576)  # the context windows nanobot accepts
# OpenRouter refuses a request rather than charge more than this (its max_price routing limit).
FREE_ONLY = {"provider": {"max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0}}}


async def load(link: AgentLink) -> dict[str, Any]:
    data = await link.api_get("/api/settings")
    return data if isinstance(data, dict) else {}


def presets(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """[{name, model, provider, ...}], the hidden "default" preset included."""
    raw = settings.get("model_presets")
    if isinstance(raw, dict):
        return [{"name": name, **(value if isinstance(value, dict) else {})} for name, value in raw.items()]
    return [p for p in raw or [] if isinstance(p, dict) and p.get("name")]


def call_order(settings: dict[str, Any]) -> list[str]:
    """The presets the agent tries, in order: its default, then its fallbacks."""
    order = settings.get("model_call_order")
    names = [(o.get("name") if isinstance(o, dict) else o) for o in order or []]
    names = [n for n in names if isinstance(n, str) and n]
    if names:
        return names
    agent = settings.get("agent") if isinstance(settings.get("agent"), dict) else {}
    return [agent.get("model_preset") or "default"]


def preset_model(settings: dict[str, Any], name: str | None) -> str | None:
    for preset in presets(settings):
        if preset.get("name") == name:
            model = preset.get("model")
            return model if isinstance(model, str) else None
    return None


def preset_name(model_id: str) -> str:
    """A preset name for an OpenRouter model id: "qwen/qwen3.8-27b:free" -> "qwen3.8-27b-free"."""
    name = re.sub(r"[^a-z0-9.\-]+", "-", model_id.split("/", 1)[-1].lower()).strip("-.")
    return (name or "model")[:48].rstrip("-.") if name != "default" else "default-model"


def context_size(context: int) -> int:
    """The largest context window nanobot accepts that the model has (or the smallest one)."""
    fitting = [size for size in CONTEXT_SIZES if size <= context] if context else []
    return fitting[-1] if fitting else CONTEXT_SIZES[0]


async def ensure_preset(link: AgentLink, model: Model, settings: dict[str, Any] | None = None) -> str:
    """The name of a preset for this OpenRouter model, made if there isn't one."""
    settings = settings if settings is not None else await load(link)
    for preset in presets(settings):
        if preset.get("model") == model.id and preset.get("provider") in ("openrouter", None, "auto") \
                and preset.get("name") != "default":
            return str(preset["name"])
    taken = {p.get("name") for p in presets(settings)}
    name, n = preset_name(model.id), 2
    while name in taken:
        name, n = f"{preset_name(model.id)[:44]}-{n}", n + 1
    await link.request("settings.model_configuration.create", {
        "name": name, "model": model.id, "provider": "openrouter", "max_tokens": 8192,
        "context_window_tokens": context_size(model.context), "temperature": 0.2,
    })
    return name


async def set_call_order(link: AgentLink, names: list[str]) -> None:
    await link.request("settings.model_call_order.update", {"order": names})


async def delete_preset(link: AgentLink, name: str) -> None:
    await link.request("settings.model_configuration.delete", {"name": name})


def openrouter_provider(settings: dict[str, Any]) -> dict[str, Any]:
    for provider in settings.get("providers") or []:
        if isinstance(provider, dict) and (provider.get("name") or provider.get("provider")) == "openrouter":
            return provider
    return {}


def free_only(settings: dict[str, Any]) -> bool:
    body = openrouter_provider(settings).get("extra_body") or openrouter_provider(settings).get("extraBody")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = None
    limit = ((body or {}).get("provider") or {}).get("max_price") if isinstance(body, dict) else None
    return isinstance(limit, dict) and limit.get("prompt") == 0 and limit.get("completion") == 0


async def set_free_only(link: AgentLink, on: bool, settings: dict[str, Any]) -> None:
    """Have OpenRouter refuse anything that costs money (or stop refusing), for every request."""
    body = openrouter_provider(settings).get("extra_body") or {}
    if isinstance(body, str):
        try:
            body = json.loads(body) or {}
        except ValueError:
            body = {}
    body = dict(body) if isinstance(body, dict) else {}
    provider = dict(body.get("provider") or {})
    if on:
        provider.update(FREE_ONLY["provider"])
    else:
        provider.pop("max_price", None)
    if provider:
        body["provider"] = provider
    else:
        body.pop("provider", None)
    await link.request("settings.provider.update", {"provider": "openrouter", "extra_body": json.dumps(body)})
