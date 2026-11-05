# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Routes the agent's own OpenRouter calls through the pool relay's tokenizing route, or puts them back.

`nanoborealis privacy` pipes this into the agent's container, where it runs as the agent, so the config
keeps its owner. nanobot notices the change without a restart.

    python3 - on <relay-base> <relay-token>   send the openrouter provider to <relay-base>/src/openrouter/v1
    python3 - off                              put it back as it was
    python3 - status                           say where it goes

With it on, what the agent sends a free cloud model passes through the relay on this computer first:
personal details become placeholders (tokenizer.py) and are restored in the answer. The provider's
original address and key are kept in ~/.nanobot/privacy-backup.json so `off` can restore them.
"""

import json
import sys
from pathlib import Path

PROVIDER = "openrouter"
SOURCE = "openrouter"  # the name of the source in /etc/nanoborealis/sources.json


def rewrite(config: dict, base: str, token: str, backup: dict) -> dict:
    """`config` with its openrouter provider pointed at the relay. The first time, what it had goes into `backup`."""
    provider = config.setdefault("providers", {}).setdefault(PROVIDER, {})
    if "apiBase" not in backup and "apiKey" not in backup:
        backup["apiBase"] = provider.get("apiBase")  # None: it used OpenRouter's own address
        backup["apiKey"] = provider.get("apiKey")
    provider["apiBase"] = f"{base.rstrip('/')}/src/{SOURCE}/v1"
    provider["apiKey"] = token
    return config


def restore(config: dict, backup: dict) -> dict:
    provider = config.setdefault("providers", {}).setdefault(PROVIDER, {})
    for field in ("apiBase", "apiKey"):
        if backup.get(field) is None:
            provider.pop(field, None)
        else:
            provider[field] = backup[field]
    return config


def routed(config: dict) -> str:
    return str((config.get("providers") or {}).get(PROVIDER, {}).get("apiBase") or "")


def main(argv: list[str]) -> None:
    folder = Path.home() / ".nanobot"
    path, backup_path = folder / "config.json", folder / "privacy-backup.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    try:
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        backup = {}
    action = argv[0] if argv else "status"
    if action == "status":
        where = routed(config)
        print(f"on: the agent's OpenRouter calls go through the relay ({where})" if f"/src/{SOURCE}/" in where
              else "off: the agent's OpenRouter calls go straight to OpenRouter")
        return
    if action == "on" and len(argv) == 3:
        config = rewrite(config, argv[1], argv[2], backup)
        backup_path.write_text(json.dumps(backup, indent=2) + "\n", encoding="utf-8")
        backup_path.chmod(0o600)
        print(f"the agent's OpenRouter calls now go through {routed(config)}")
    elif action == "off":
        config = restore(config, backup)
        backup_path.unlink(missing_ok=True)
        print("the agent's OpenRouter calls go straight to OpenRouter again")
    else:
        sys.exit(__doc__)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


if __name__ == "__main__":
    main(sys.argv[1:])
