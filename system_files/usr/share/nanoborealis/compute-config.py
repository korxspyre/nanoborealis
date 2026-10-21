# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Adds, removes, or selects a compute device in the agent's nanobot config.

`nanoborealis compute` pipes this into the agent's container, where it runs as the agent,
so the config keeps its owner. nanobot notices the change without a restart.

    python3 - add <name> <url> <token> <model> | remove <name> | use <name>|cloud | list
"""

import json
import sys
from pathlib import Path

CLOUD_DEFAULT = "ultra"
CLOUD_FALLBACKS = ["super", "lightning"]

path = Path.home() / ".nanobot" / "config.json"
config = json.loads(path.read_text(encoding="utf-8"))
providers = config.setdefault("providers", {})
presets = config.setdefault("modelPresets", {})
defaults = config.setdefault("agents", {}).setdefault("defaults", {})
action = sys.argv[1] if len(sys.argv) > 1 else "list"


def key(name: str) -> str:
    return f"device-{name}"


if action == "add":
    name, url, token, model = sys.argv[2:6]
    providers[key(name)] = {"apiBase": url, "apiKey": token}
    presets[key(name)] = {"provider": key(name), "model": model, "maxTokens": 4096,
                          "contextWindowTokens": 32768 if model.endswith("-32k") else 16384, "temperature": 0.2}
    print(f"added {name}: {model} at {url}")
elif action == "remove":
    name = sys.argv[2]
    providers.pop(key(name), None)
    presets.pop(key(name), None)
    if defaults.get("modelPreset") == key(name):
        defaults["modelPreset"] = CLOUD_DEFAULT
        defaults["fallbackModels"] = CLOUD_FALLBACKS
    defaults["fallbackModels"] = [m for m in defaults.get("fallbackModels", []) if m != key(name)]
    print(f"removed {name}")
elif action == "use":
    name = sys.argv[2]
    if name == "cloud":
        defaults["modelPreset"] = CLOUD_DEFAULT
        defaults["fallbackModels"] = CLOUD_FALLBACKS
    elif key(name) in presets:
        # The cloud models stay as fallbacks for when the device is switched off.
        defaults["modelPreset"] = key(name)
        defaults["fallbackModels"] = [CLOUD_DEFAULT, *CLOUD_FALLBACKS]
    else:
        sys.exit(f"no compute device named {name}")
    print(f"the agent now uses {name} first")
elif action == "list":
    devices = [k for k in presets if k.startswith("device-")]
    for k in devices:
        print(f"{k[len('device-'):]}\t{presets[k].get('model')}\t{providers.get(k, {}).get('apiBase')}")
    if not devices:
        print("no compute devices")
    current = defaults.get("modelPreset", "")
    print(f"in use: {current[len('device-'):] if current.startswith('device-') else 'cloud (' + current + ')'}")
    sys.exit(0)
else:
    sys.exit(f"unknown action {action}")

tmp = path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
tmp.replace(path)
