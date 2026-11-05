# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Checks `nanoborealis privacy`: the agent's OpenRouter calls are pointed at the pool relay's tokenizing
route and put back exactly as they were, and the relay's copy of the key is stored privately.

    python3 tests/privacy_check.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "system_files/usr/share/nanoborealis"
sys.path.insert(0, str(TOOLS))

import sources  # noqa: E402


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


SEED = ROOT / "system_files/usr/share/nanoborealis/image/seed/config.json"
BASE, TOKEN = "http://host.containers.internal:11436", "t" * 32

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as home:
    folder = Path(home, ".nanobot")
    folder.mkdir()
    original = json.loads(SEED.read_text(encoding="utf-8"))
    (folder / "config.json").write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    env = {**os.environ, "HOME": home, "USERPROFILE": home}

    def run(*args: str) -> str:
        done = subprocess.run([sys.executable, str(TOOLS / "privacy.py"), *args], capture_output=True, text=True, env=env)
        if done.returncode != 0:
            raise SystemExit(f"privacy.py {args} failed: {done.stderr}")
        return done.stdout

    config = lambda: json.loads((folder / "config.json").read_text(encoding="utf-8"))  # noqa: E731
    check(run("status").startswith("off"), "before, the agent's calls go straight to OpenRouter")
    run("on", BASE, TOKEN)
    provider = config()["providers"]["openrouter"]
    check(provider["apiBase"] == f"{BASE}/src/openrouter/v1" and provider["apiKey"] == TOKEN,
          "on: the openrouter provider goes through the relay's tokenizing route, with the relay's token")
    check(config()["modelPresets"] == original["modelPresets"] and config()["agents"] == original["agents"]
          and provider["extraBody"] == original["providers"]["openrouter"]["extraBody"],
          "...and nothing else in the config changes")
    check(run("status").startswith("on"), "status says it's on")
    run("on", BASE, TOKEN)
    check(json.loads((folder / "privacy-backup.json").read_text())["apiKey"] == "${OPENROUTER_API_KEY}",
          "turning it on twice doesn't overwrite what off will restore")
    run("off")
    check(config() == original and not (folder / "privacy-backup.json").exists(),
          "off: the config is exactly as it was, and the backup is gone")
    check(run("status").startswith("off"), "status says it's off again")

    # A provider that had its own address keeps it through a round trip.
    custom = json.loads(json.dumps(original))
    custom["providers"]["openrouter"]["apiBase"] = "https://example.invalid/v1"
    (folder / "config.json").write_text(json.dumps(custom), encoding="utf-8")
    run("on", BASE, TOKEN)
    run("off")
    check(config()["providers"]["openrouter"]["apiBase"] == "https://example.invalid/v1", "a custom address comes back too")

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    env_file = Path(tmp, "etc", "sources.env")
    sources.store_key("OPENROUTER_API_KEY", "sk-or-v1-first", env_file)
    sources.store_key("OTHER_KEY", "other", env_file)
    sources.store_key("OPENROUTER_API_KEY", "sk-or-v1-second", env_file)
    lines = env_file.read_text().splitlines()
    check(lines.count("OPENROUTER_API_KEY=sk-or-v1-second") == 1 and "OTHER_KEY=other" in lines and not any("first" in l for l in lines),
          "a key is replaced in place, and the others are kept")
    check(os.name == "nt" or oct(env_file.stat().st_mode & 0o777) == "0o600", "the key file is private to root")
    for bad in (("lower", "x"), ("OPENROUTER_API_KEY", ""), ("OPENROUTER_API_KEY", "a\nb=c")):
        try:
            sources.store_key(*bad, env_file)
            refused = False
        except ValueError:
            refused = True
        check(refused, f"a bad key or name is refused ({bad[0]!r})")
    done = subprocess.run([sys.executable, str(TOOLS / "sources.py"), "key", "STDIN_KEY"], input="sk-or-v1-piped", text=True,
                          capture_output=True, env={**os.environ, "NANOBOREALIS_SOURCES_ENV": str(env_file)})
    check(done.returncode == 0 and "STDIN_KEY=sk-or-v1-piped" in env_file.read_text() and "sk-or-v1-piped" not in done.stdout,
          "`sources.py key` reads the key from stdin and doesn't echo it")

print("privacy checks passed")
