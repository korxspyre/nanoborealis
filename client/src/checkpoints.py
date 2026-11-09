# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Undo for what agents change on a device that lends its hands.

Before a job that may change something, hands.py takes a checkpoint of the folders its request is about: a copy of
each (skipping what is rebuilt anyway, like node_modules and virtual environments), kept under the hands folder.
`restore` puts a folder back as it was, keeping what is there now beside it, so even an undo can be undone. The
newest checkpoints are kept within a size budget; older ones go.

    python checkpoints.py list
    python checkpoints.py restore <id> [folder]      every folder of the checkpoint, or one
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

SKIP = {"node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "target", "build", "dist",
        ".gradle", ".next", ".cache"}
FOLDER_LIMIT = 512 * 1024 * 1024  # a folder bigger than this (after SKIP) isn't copied: the job is told so
BUDGET = 4 * 1024 * 1024 * 1024  # all checkpoints together; the oldest go first
KEEP_AT_LEAST = 5


def base() -> Path:
    import hands  # its data_root knows where a phone lets the app keep files
    root = hands.home() / "checkpoints"
    root.mkdir(parents=True, exist_ok=True)
    return root


def folder_size(path: Path, limit: int) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
            if total > limit:
                return total
    return total


def take(folders: list[str], job: str, command: str) -> dict:
    """A checkpoint of `folders`. Returns {"id", "folders": [...copied], "skipped": {folder: why}}."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    cid = f"{stamp}-{job or 'job'}"[:64]
    where = base() / cid
    where.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], {}
    for i, folder in enumerate(folders):
        source = Path(folder)
        if not source.is_dir():
            skipped[folder] = "not a folder here"
            continue
        size = folder_size(source, FOLDER_LIMIT)
        if size > FOLDER_LIMIT:
            skipped[folder] = f"larger than {FOLDER_LIMIT // (1024 * 1024)} MB"
            continue
        shutil.copytree(source, where / str(i), symlinks=True, ignore=shutil.ignore_patterns(*SKIP),
                        ignore_dangling_symlinks=True, dirs_exist_ok=True)
        copied.append(folder)
    (where / "checkpoint.json").write_text(json.dumps({"id": cid, "at": time.time(), "command": command, "job": job,
                                                       "folders": folders, "copied": copied, "skipped": skipped}),
                                           encoding="utf-8")
    prune()
    return {"id": cid, "folders": copied, "skipped": skipped}


def entries() -> list[dict]:
    found = []
    for meta in base().glob("*/checkpoint.json"):
        try:
            found.append(json.loads(meta.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda e: -float(e.get("at") or 0))


def prune() -> None:
    total = 0
    for i, entry in enumerate(entries()):
        where = base() / entry["id"]
        total += folder_size(where, BUDGET * 2)
        if i >= KEEP_AT_LEAST and total > BUDGET:
            shutil.rmtree(where, ignore_errors=True)


def restore(cid: str, only: str | None = None) -> list[str]:
    """Put the checkpoint's folders back. What is there now moves aside to '<folder>.before-undo-<time>'."""
    where = base() / cid
    meta = json.loads((where / "checkpoint.json").read_text(encoding="utf-8"))
    done = []
    for i, folder in enumerate(meta["folders"]):
        if folder not in meta["copied"] or (only and os.path.normcase(os.path.abspath(only)) != os.path.normcase(os.path.abspath(folder))):
            continue
        target = Path(folder)
        if target.exists():
            target.rename(target.with_name(f"{target.name}.before-undo-{time.strftime('%H%M%S')}"))
        shutil.copytree(where / str(i), target, symlinks=True)
        done.append(folder)
    return done


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "list":
        for e in entries():
            print(f"{e['id']}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(e['at']))}  {e['command'][:70]}")
            for f in e["copied"]:
                print(f"    {f}")
        return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "restore":
        for folder in restore(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None):
            print(f"restored {folder}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
