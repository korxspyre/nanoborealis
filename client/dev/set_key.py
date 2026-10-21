# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Give a paired NanoBorealis computer's agent a new OpenRouter key, from this device.

    python dev/set_key.py laptop.json

A small window asks for the key (hidden as you type or paste it) and sends it over the paired,
encrypted connection to the computer's pairing service, which stores it as the agent's secret and
restarts the agent. The key is never printed or saved here. laptop.json is what dev/remote.py pair
saves; the device must have been approved by an administrator of the computer.
"""

from __future__ import annotations

import json
import os
import sys
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import admin  # noqa: E402
from pairing import Machine, PairingError  # noqa: E402


def main(path: str) -> int:
    with open(path) as f:
        machine = Machine.from_json(json.load(f))
    if machine is None:
        raise SystemExit(f"{path} doesn't hold a paired computer")

    root = tk.Tk()
    root.title("NanoBorealis: OpenRouter key")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=16)
    frame.grid()
    ttk.Label(frame, text=f"New OpenRouter key for the agent on {machine.name}:").grid(row=0, column=0, sticky="w")
    entry = ttk.Entry(frame, width=56, show="•")
    entry.grid(row=1, column=0, pady=8)
    status = ttk.Label(frame, text="It goes straight to that computer, encrypted, and isn't kept here.")
    status.grid(row=2, column=0, sticky="w")
    result = {"code": 1}

    def save(_event=None) -> None:
        key = entry.get().strip()
        if not key:
            return
        status.config(text="Saving, and restarting the agent...")
        root.update()
        try:
            admin.set_key(machine, key)
        except PairingError as e:
            status.config(text=f"Not saved: {e}")
            return
        finally:
            key = ""
        entry.delete(0, "end")
        result["code"] = 0
        status.config(text="Saved. The agent restarted with the new key.")
        root.after(1800, root.destroy)

    buttons = ttk.Frame(frame)
    buttons.grid(row=3, column=0, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).grid(row=0, column=0, padx=6)
    ttk.Button(buttons, text="Save", command=save).grid(row=0, column=1)
    entry.bind("<Return>", save)
    entry.focus_set()
    root.attributes("-topmost", True)
    root.mainloop()
    print("saved" if result["code"] == 0 else "not saved")
    return result["code"]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1]))
