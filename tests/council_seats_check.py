# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks council.py: which seats the pool's loadout calls for, and the files each seat runs from.

    python3 tests/council_seats_check.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "system_files/usr/share/nanoborealis"))

import council  # noqa: E402

FAILED = False


def check(ok: bool, what: str) -> None:
    global FAILED
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    FAILED |= not ok


def placement(model: str, capability: float = 50, family: str = "") -> dict:
    return {"model": model, "capability": capability, "family": family}


check(council.slug("mlx-community/Qwen3-14B-4bit") == "qwen3-14b-4bit", "a seat's id comes from its model")
check(council.nametape("mlx-community/Qwen3-14B-4bit") == "Qwen3 14B", "its nametape is the model's short name")
check(council.nametape("mlx-community/gemma-3-12b-it-4bit") == "gemma 3 12b", "...without quantization or tuning tags")
check(council.slug("a/" + "x" * 80) == "x" * 40, "ids stay short enough for the Hall")

plan = {"placements": [placement("mlx-community/Qwen3-14B-4bit", 52.4, "qwen"),
                       placement("mlx-community/gemma-3-12b-it-4bit", 44, "gemma")]}
seats = council.desired(plan, [])
check([s["id"] for s in seats] == ["qwen3-14b-4bit", "gemma-3-12b-it-4bit"], "one seat per model in the loadout")
check([s["port"] for s in seats] == [8770, 8771], "each seat gets its own port")
check(seats[0]["capability"] == 52.4 and seats[0]["family"] == "qwen", "a seat carries its model's capability")

later = {"placements": [placement("mlx-community/Llama-3.1-8B-4bit"), placement("mlx-community/gemma-3-12b-it-4bit")]}
again = {s["id"]: s["port"] for s in council.desired(later, seats)}
check(again == {"llama-3-1-8b-4bit": 8770, "gemma-3-12b-it-4bit": 8771},
      "a seat that stays keeps its port; a new one takes a free one")
check(len(council.desired({"placements": [placement(f"m/model-{i}") for i in range(40)]}, [])) == council.MAX_SEATS,
      "there's a limit to how many seats there are")
check(len(council.desired({"placements": [placement("a/x"), placement("b/x")]}, [])) == 1,
      "two models with the same name share a seat id, and only one is seated")

config = council.config(seats[0])
check(config["providers"]["pool"]["apiBase"] == council.RELAY and "openrouter" not in config["providers"],
      "a seat's model comes through the pool relay, and it has no cloud key")
check(config["agents"]["defaults"]["modelPreset"] == "seat" and config["agents"]["defaults"]["fallbackModels"] == [],
      "a seat is its one model")
check("${NANOBOREALIS_RELAY_TOKEN}" in json.dumps(config) and "${NANOBOREALIS_SEAT_API_KEY}" in json.dumps(config),
      "its secrets come from the environment, never written into files")

unit = council.quadlet(seats[0])
for line in ("ContainerName=seat-qwen3-14b-4bit", "PublishPort=127.0.0.1:8770:8900", "Exec=serve",
             "Secret=seat-qwen3-14b-4bit-hall,type=env,target=NANOBOREALIS_HALL_TOKEN",
             "Volume=seat-qwen3-14b-4bit-home:/home/nanobot", "Environment=NANOBOREALIS_SEAT=qwen3-14b-4bit"):
    check(line in unit.splitlines(), f"the seat's unit: {line.split('=')[0]}")
check("Qwen3 14B" in council.agents_md(seats[0]) and "hall_read" in council.agents_md(seats[0]),
      "its instructions say who it is and how the council works")

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    (Path(tmp) / "plan.json").write_text(json.dumps(plan))
    out = subprocess.run([sys.executable, str(ROOT / "system_files/usr/share/nanoborealis/council.py"), "seats",
                          str(Path(tmp) / "plan.json"), str(Path(tmp) / "missing.json")], capture_output=True, text=True)
    listed = json.loads(out.stdout)
    check([s["id"] for s in listed] == [s["id"] for s in seats], "council.py seats, from the command line")
    (Path(tmp) / "seats.json").write_text(out.stdout)
    subprocess.run([sys.executable, str(ROOT / "system_files/usr/share/nanoborealis/council.py"), "files",
                    str(Path(tmp) / "seats.json"), "qwen3-14b-4bit", str(Path(tmp) / "q")], check=True)
    written = sorted(p.name for p in (Path(tmp) / "q").iterdir())
    check(written == ["AGENTS.md", "config.json", "seat-qwen3-14b-4bit.container"], "council.py files writes all three")

if FAILED:
    sys.exit(1)
print("all council seat checks passed")
