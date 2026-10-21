# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Finds NanoBorealis machines on the local network, the way computers find printers (mDNS), and
finds a paired one again after the router gives it a new address (locate).

A NanoBorealis machine announces `_nanoborealis._tcp` while remote access is on (the default;
`nanoborealis remote on|off`), with the port where it pairs devices. Machines set up before
pairing announced the WebUI's own port, 8765, instead; the app pairs on 8766 either way.
Nothing here imports Flet.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pairing import Machine

SERVICE = "_nanoborealis._tcp.local."
PAIR_PORT = 8766


@dataclass(frozen=True)
class Found:
    name: str  # "NanoBorealis on laptop"
    host: str  # laptop.local
    address: str
    port: int


def browse(seconds: float = 3.0) -> list[Found]:
    """NanoBorealis machines that answer within `seconds`. Empty if mDNS isn't available."""
    try:
        from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        return []
    found: dict[str, Found] = {}

    class Listener(ServiceListener):
        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            self.resolve(zc, type_, name)

        def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            self.resolve(zc, type_, name)

        def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            found.pop(name, None)

        def resolve(self, zc: Zeroconf, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name, timeout=1500)
            addresses = info.parsed_addresses() if info else []
            if not info or not addresses:
                return
            label = name[: -len(type_) - 1] if name.endswith("." + type_) else name
            port = info.port if info.port and info.port != 8765 else PAIR_PORT
            found[name] = Found(label, (info.server or "").rstrip("."), addresses[0], port)

    try:
        zc = Zeroconf(ip_version=IPVersion.V4Only)
    except OSError:
        return []  # no multicast on this network or platform
    browser = ServiceBrowser(zc, SERVICE, Listener())
    try:
        time.sleep(seconds)
    finally:
        browser.cancel()
        zc.close()
    return sorted(found.values(), key=lambda f: f.name)


def own_addresses() -> list[str]:
    """This device's own IPv4 addresses on private networks (one per network it's on)."""
    found = set()
    try:
        import ifaddr  # comes with zeroconf
        for adapter in ifaddr.get_adapters():
            found.update(ip.ip for ip in adapter.ips if isinstance(ip.ip, str))
    except Exception:
        pass
    try:  # the address this device reaches the world from; no packet is sent
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in found if neighbours(a))


def sweep(port: int = PAIR_PORT) -> list[Found]:
    """NanoBorealis computers answering on the pairing port around this device's own addresses (the
    /24 of each private network it's on), for networks whose router drops mDNS. Each computer once,
    by its certificate, however many addresses it answers at. Blocks for a few seconds."""
    from pairing import hello
    candidates = sorted({a for own in own_addresses() for a in neighbours(own)})
    if not candidates:
        return []
    with ThreadPoolExecutor(128) as pool:
        open_ones = [a for a, up in zip(candidates, pool.map(lambda a: _answers(a, port), candidates)) if up]
        answers = list(pool.map(lambda a: hello(a, port), open_ones))
    found: dict[str, Found] = {}
    for address, answer in zip(open_ones, answers):
        if answer is not None:
            info, key = answer
            found.setdefault(key, Found(f"NanoBorealis on {info.get('machine') or address}", "", address, port))
    return list(found.values())


def find(seconds: float = 3.0) -> list[Found]:
    """NanoBorealis computers on this device's networks: those announcing themselves (mDNS), and
    those answering on the pairing port nearby (sweep), since some routers drop mDNS."""
    with ThreadPoolExecutor(2) as pool:
        announced, swept = pool.submit(browse, seconds), pool.submit(sweep)
        found = {f.address: f for f in swept.result()}
        found.update({f.address: f for f in announced.result()})
    return sorted(found.values(), key=lambda f: (f.name, f.address))


def _answers(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def neighbours(host: str) -> list[str]:
    """The other addresses of host's home network (its /24), nearest first: where a router puts a
    computer that comes back with a new address. Empty unless host is a private IPv4 address."""
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError:
        return []
    if not address.is_private or address.is_loopback or address.is_link_local:
        return []
    network = ipaddress.IPv4Network(f"{address}/24", strict=False)
    others = [str(a) for a in network.hosts() if a != address]
    return sorted(others, key=lambda a: abs(int(ipaddress.IPv4Address(a)) - int(address)))


def locate(machine: Machine, seconds: float = 2.0) -> str | None:
    """Where a paired computer is now, when it no longer answers at its address (the router gave it
    a new one while it slept, say): the address among the NanoBorealis computers announcing
    themselves, else among the addresses around its old one, where its own certificate answers.
    None when it isn't found (off, asleep, or on another network). Blocks for a few seconds."""
    from pairing import PairingError, probe_key

    try:
        if ipaddress.ip_address(machine.host).is_loopback:
            return None  # this computer itself, which doesn't move
    except ValueError:
        return None  # a name (laptop.local): it follows the computer by itself

    def is_it(address: str) -> bool:
        try:
            return probe_key(address, machine.port, timeout=5) == machine.key
        except PairingError:
            return False

    for found in browse(seconds):
        if found.address != machine.host and is_it(found.address):
            return found.address
    candidates = neighbours(machine.host)
    if not candidates:
        return None
    with ThreadPoolExecutor(64) as pool:
        open_ones = [a for a, up in zip(candidates, pool.map(lambda a: _answers(a, machine.port), candidates)) if up]
    return next((a for a in open_ones if is_it(a)), None)
