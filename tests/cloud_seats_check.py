# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Cloud seats: a rig's seats that are free OpenRouter models the colony asks itself (no pool, no GPU).

Checks the spec rules, the head line, and how the colony reads a cloud seat's plain answers (ACCEPT/REJECT, CHOICE).
"""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "system_files/usr/share/nanoborealis"))
import rigs  # noqa: E402

loader = importlib.machinery.SourceFileLoader("colony", str(ROOT / "system_files/usr/libexec/nanoborealis-colony"))
spec = importlib.util.spec_from_loader("colony", loader)
colony = importlib.util.module_from_spec(spec)
loader.exec_module(colony)

failed = 0


def check(ok: bool, what: str) -> None:
    global failed
    print(("ok   " if ok else "FAIL ") + what)
    failed += not ok


civ = rigs.parse((ROOT / "system_files/usr/share/nanoborealis/rigs/civ.toml").read_text(encoding="utf-8"))
check(all(s["model"].endswith(":free") for s in civ["seats"]), "the crew that ships runs on free models only")

try:
    rigs.parse('[rig]\nname = "x"\n[[seat]]\nid = "a"\nkind = "cloud"\nmodel = "openai/gpt-5"\n')
    check(False, "a cloud seat on a paid model is refused")
except rigs.SpecError as e:
    check("free" in str(e), "a cloud seat on a paid model is refused")

head = rigs.pick_head([{"id": "a", "head_rank": 1, "kind": "cloud", "weight": 0.5}], {}, 0.0)
check(head["head"] == "a" and head["line"][0]["state"] == "ready", "a cloud seat is always there to lead: the colony can ask it")
check(rigs.askable({"kind": "cloud"}) and rigs.askable({"port": 9}) and not rigs.askable({"kind": "outside"}),
      "the colony can ask pool and cloud seats, not outside ones")

check(colony.cloud_verdict("ACCEPT\nLooks right.") == ("accept", "Looks right."), "ACCEPT on the first line accepts")
check(colony.cloud_verdict("**REJECT**: misses hidden files") == ("reject", "misses hidden files"), "REJECT with a reason rejects")
check(colony.cloud_verdict("REJECT")[0] == "reject" and colony.cloud_verdict("REJECT")[1], "a bare REJECT still carries a reason")
check(colony.cloud_verdict("I think it is fine") is None, "an answer without a verdict is not read as one")
check(colony.cloud_choice("CHOICE: Plan B\nREASON: cheaper", ["Plan A", "Plan B"]) == ("Plan B", "cheaper"), "CHOICE picks the option")
check(colony.cloud_choice("CHOICE: plan b.", ["Plan A", "Plan B"])[0] == "Plan B", "...matching case and stray punctuation")
check(colony.cloud_choice("CHOICE: Plan C", ["Plan A", "Plan B"]) is None, "an option that isn't on the ballot is not guessed at")

print("cloud seat checks passed" if not failed else f"{failed} cloud seat check(s) failed")
sys.exit(1 if failed else 0)
