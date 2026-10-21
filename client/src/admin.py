# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""The control center's calls to a NanoBorealis computer's pairing service (nanoborealis-remote),
for what nanobot can't do itself: the agent container's state, logs and restarts, its OpenRouter
key and what that key has spent, its memory files, the paired devices, and the pool. (Models, channels,
skills and schedules go through nanobot's own settings: AgentLink.request.)

The computer allows them to devices paired by one of its administrators. Every call blocks: run
them in a thread.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from pairing import DEVICE_HEADER, Machine, NotPaired, PairingError, call

WORKSPACE = ".nanobot/workspace"


class AdminError(PairingError):
    """The computer turned the request down, or couldn't carry it out."""


def _call(machine: Machine, method: str, path: str, body: dict | None = None, timeout: float = 45) -> dict[str, Any]:
    status, data, _ = call(machine.host, machine.port, method, path, context=machine.context(), body=body,
                           headers={DEVICE_HEADER: machine.password}, timeout=timeout)
    if status == 401:
        raise NotPaired("This computer doesn't know this device anymore. Pair it again.")
    if status == 404 and not data.get("error"):
        raise AdminError("This computer's NanoBorealis is too old for the control center. Update it.")
    if status != 200:
        raise AdminError(str(data.get("error") or f"The computer answered HTTP {status}."))
    return data


def supported(machine: Machine) -> bool:
    status, data, _ = call(machine.host, machine.port, "GET", "/nanoborealis/hello", context=machine.context())
    return status == 200 and bool(data.get("admin"))


def overview(machine: Machine) -> dict[str, Any]:
    return _call(machine, "GET", "/nanoborealis/agent")


def read_file(machine: Machine, path: str) -> str:
    return str(_call(machine, "GET", "/nanoborealis/agent/file?" + urllib.parse.urlencode({"path": path}))["content"])


def list_files(machine: Machine, path: str = WORKSPACE) -> list[dict[str, Any]]:
    return list(_call(machine, "GET", "/nanoborealis/agent/files?" + urllib.parse.urlencode({"path": path}))["files"])


def write_file(machine: Machine, path: str, content: str) -> None:
    _call(machine, "POST", "/nanoborealis/agent/file", {"path": path, "content": content})


def delete_file(machine: Machine, path: str) -> None:
    _call(machine, "POST", "/nanoborealis/agent/file", {"path": path, "content": "", "delete": True})


def logs(machine: Machine, lines: int = 300) -> list[str]:
    return list(_call(machine, "GET", f"/nanoborealis/agent/logs?lines={int(lines)}")["lines"])


def restart(machine: Machine) -> dict[str, Any]:
    return _call(machine, "POST", "/nanoborealis/agent/restart", {}, timeout=320)["agent"]


def usage(machine: Machine, fresh: bool = False) -> dict[str, Any]:
    return _call(machine, "GET", "/nanoborealis/agent/usage" + ("?fresh=1" if fresh else ""))


def set_key(machine: Machine, key: str) -> None:
    _call(machine, "POST", "/nanoborealis/agent/key", {"key": key}, timeout=320)


def devices(machine: Machine) -> list[dict[str, Any]]:
    return list(_call(machine, "GET", "/nanoborealis/devices")["devices"])


def remove_device(machine: Machine, device_id: str) -> None:
    _call(machine, "POST", "/nanoborealis/devices/remove", {"id": device_id})


def pool_info(machine: Machine) -> dict[str, Any]:
    """The pool this computer is in, for lending this device to it: its name and exo's exact version
    (see poolnode.PoolInfo). {"in_pool": False} when it isn't in one."""
    return _call(machine, "GET", "/nanoborealis/pool")



# The pool's page: what the planner proposes, and changes to it. Changes that move models run in the
# background on the computer, one at a time; pool_job follows the one running.

def pool_overview(machine: Machine, rebuild: bool = False) -> dict[str, Any]:
    """The pool's computers, the proposed loadout and its changes, the bench table and the policy;
    "error" when the pool node doesn't answer. Planning can refresh LiveBench's scores first."""
    return _call(machine, "GET", "/nanoborealis/pool/overview" + ("?rebuild=1" if rebuild else ""), timeout=240)


def pool_job(machine: Machine) -> dict[str, Any]:
    return dict(_call(machine, "GET", "/nanoborealis/pool/job")["job"])


def pool_apply(machine: Machine, rebuild: bool = False) -> dict[str, Any]:
    return dict(_call(machine, "POST", "/nanoborealis/pool/apply", {"rebuild": rebuild})["job"])


def pool_drain(machine: Machine, node: str) -> dict[str, Any]:
    return dict(_call(machine, "POST", "/nanoborealis/pool/drain", {"node": node})["job"])


def pool_undrain(machine: Machine, node: str) -> None:
    _call(machine, "POST", "/nanoborealis/pool/undrain", {"node": node}, timeout=240)


def pool_bench(machine: Machine, trial: str | None = None, on: list[str] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"try": trial, "on": on or []} if trial else {}
    return dict(_call(machine, "POST", "/nanoborealis/pool/bench", body)["job"])


def pool_policy(machine: Machine, changes: dict[str, Any]) -> dict[str, Any]:
    return dict(_call(machine, "POST", "/nanoborealis/pool/policy", {"changes": changes})["policy"])


# The council's Hall (docs/council/README.md): rooms, the task board, votes and seats, where you
# speak as "you". NotPaired and AdminError as for the rest; a computer without a council says so.

def hall(machine: Machine, method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict[str, Any]:
    return _call(machine, method, "/nanoborealis/hall" + path, body if method == "POST" else None, timeout=timeout)


def pool_lend(machine: Machine, lending: bool, ports: list[int] | None = None) -> None:
    """Tell the computer this device lends itself to the pool (its nodes' API ports: one for the GPU,
    one for the processor), or stopped: a computer that coordinates the pool without a node of its
    own reaches the pool through these."""
    _call(machine, "POST", "/nanoborealis/pool/lend", {"lending": lending, "ports": ports or [52415]})
