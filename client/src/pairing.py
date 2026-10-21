# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Pairs this app with a NanoBorealis computer, and reaches it over TLS pinned to its certificate.

The computer's side is /usr/libexec/nanoborealis-remote, which explains the scheme. From here:

1. start: send the hash of a random number, get the computer's random number, and keep the
   certificate it presented;
2. reveal: send the number; the computer shows a PIN made from both numbers and its certificate;
3. the person types that PIN here, and it must match the PIN this app makes from the certificate
   it saw. A machine posing as the computer can't arrange that;
4. finish: send the PIN; the computer answers with a long random password for this device.

Every later connection trusts that one certificate only, and carries the device's password.
Nothing here imports Flet, and every call blocks: run them in a thread.
"""

from __future__ import annotations

import getpass
import hashlib
import http.client
import json
import os
import platform
import secrets
import socket
import ssl
from dataclasses import asdict, dataclass
from typing import Any

PAIR_PORT = 8766
DEVICE_HEADER = "X-NanoBorealis-Device"


class PairingError(Exception):
    """Pairing didn't work; the message says why, for people."""


class WrongPin(PairingError):
    """The PIN typed here isn't the one the computer should be showing."""


class NotPaired(PairingError):
    """The computer doesn't know this device (never paired, or removed), or isn't the one it paired with."""


def pairing_pin(client_nonce: bytes, server_nonce: bytes, certificate_der: bytes) -> str:
    """The PIN both sides derive. The computer has the same function."""
    digest = hashlib.sha256(b"nanoborealis-pair-v1" + client_nonce + server_nonce
                            + hashlib.sha256(certificate_der).digest()).digest()
    return f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"


def device_name() -> str:
    system = {"Windows": "Windows", "Darwin": "Mac", "Linux": "Linux"}.get(platform.system(), platform.system())
    return f"{socket.gethostname().split('.')[0]} ({system})"[:48]


@dataclass
class Machine:
    """A computer this app has paired with."""

    host: str
    port: int
    name: str
    certificate: str  # PEM: the only certificate trusted for this computer
    password: str  # this device's password there
    device_id: str

    @property
    def key(self) -> str:
        return hashlib.sha256(ssl.PEM_cert_to_DER_cert(self.certificate)).hexdigest()[:16]

    @property
    def base_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"https://{host}:{self.port}"

    def context(self) -> ssl.SSLContext:
        return pinned_context(self.certificate)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_json(data: dict[str, Any]) -> Machine | None:
        try:
            return Machine(str(data["host"]), int(data["port"]), str(data["name"]), str(data["certificate"]),
                           str(data["password"]), str(data["device_id"]))
        except (KeyError, TypeError, ValueError):
            return None


NO_CHECK_TIME = 0x200000  # OpenSSL's X509_V_FLAG_NO_CHECK_TIME


def pinned_context(certificate_pem: str) -> ssl.SSLContext:
    """TLS that trusts this one certificate and nothing else."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False  # addresses change; the certificate is what identifies the computer
    context.verify_mode = ssl.CERT_REQUIRED
    # Nor do its dates: it's this exact certificate or nothing, and a computer whose clock runs
    # ahead makes one that looks "not yet valid" to everyone else.
    context.verify_flags |= NO_CHECK_TIME
    context.load_verify_locations(cadata=certificate_pem)
    return context


def _first_contact_context() -> ssl.SSLContext:
    # Before pairing, any certificate is accepted; the PIN is what proves it's the right one.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def hello(host: str, port: int = PAIR_PORT, timeout: float = 5) -> tuple[dict[str, Any], str] | None:
    """What a NanoBorealis computer says about itself before pairing (its name, what it offers) and
    the key of the certificate it presents; None if nothing, or something else, answers there."""
    try:
        status, data, certificate = call(host, port, "GET", "/nanoborealis/hello", context=_first_contact_context(),
                                         timeout=timeout)
    except PairingError:
        return None
    if status != 200 or data.get("service") != "nanoborealis-remote":
        return None
    return data, hashlib.sha256(certificate).hexdigest()[:16]


def probe_key(host: str, port: int = PAIR_PORT, timeout: float = 10) -> str:
    """The key (Machine.key) of the certificate the computer at host:port presents. Used to
    recognise a computer this app has paired with at an address it has since moved to."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with _first_contact_context().wrap_socket(raw) as tls:
                certificate = tls.getpeercert(binary_form=True) or b""
    except (OSError, ssl.SSLError) as e:
        raise PairingError(f"Can't reach {host} (port {port}): {getattr(e, 'strerror', None) or e}") from e
    return hashlib.sha256(certificate).hexdigest()[:16]


def split_address(raw: str) -> tuple[str, int]:
    """'192.168.1.20' or 'laptop.local:8766' -> (host, port)."""
    text = raw.strip().removeprefix("https://").removeprefix("http://").split("/", 1)[0]
    if not text:
        raise ValueError("Enter the computer's address.")
    if text.startswith("["):  # [IPv6]:port
        host, _, rest = text[1:].partition("]")
        port = rest.removeprefix(":")
    elif text.count(":") == 1:
        host, port = text.split(":")
    else:
        host, port = text, ""
    try:
        number = int(port) if port else PAIR_PORT
    except ValueError:
        raise ValueError("Use an address like 192.168.1.20 or laptop.local.") from None
    if number == 8765:
        number = PAIR_PORT  # the agent's own port, which isn't reachable from other devices
    return host, number


def call(host: str, port: int, method: str, path: str, *, context: ssl.SSLContext, body: dict | None = None,
         headers: dict[str, str] | None = None, timeout: float = 20) -> tuple[int, dict, bytes]:
    """One HTTPS request. Returns the status, the JSON answer and the certificate presented."""
    connection = http.client.HTTPSConnection(host, port, context=context, timeout=timeout)
    try:
        connection.connect()
        certificate = connection.sock.getpeercert(binary_form=True) or b""
        payload = json.dumps(body).encode() if body is not None else None
        sent = {"Accept": "application/json", **(headers or {})}
        if payload is not None:
            sent["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=sent)
        response = connection.getresponse()
        raw = response.read()
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return response.status, data if isinstance(data, dict) else {}, certificate
    except ssl.SSLCertVerificationError as e:
        raise NotPaired("The computer at this address isn't the one this app paired with. If you "
                        "reinstalled it, pair again.") from e
    except (OSError, http.client.HTTPException) as e:
        raise PairingError(f"Can't reach {host}: {getattr(e, 'strerror', None) or e}") from e
    finally:
        connection.close()


class Pairing:
    """One pairing attempt with the computer at host:port."""

    def __init__(self, host: str, port: int = PAIR_PORT, name: str | None = None):
        self.host, self.port = host, port
        self.name = name or device_name()
        self.machine = host
        self._client_nonce = secrets.token_bytes(32)
        self._server_nonce = b""
        self._certificate = b""
        self._request = ""

    @property
    def certificate_pem(self) -> str:
        return ssl.DER_cert_to_PEM_cert(self._certificate)

    def _post(self, path: str, body: dict) -> dict:
        context = pinned_context(self.certificate_pem) if self._certificate else _first_contact_context()
        status, data, certificate = call(self.host, self.port, "POST", path, context=context, body=body)
        if not self._certificate:
            self._certificate = certificate
        if status != 200:
            message = data.get("error") or f"The computer answered HTTP {status}."
            raise WrongPin(message) if status == 403 else PairingError(message)
        return data

    def start(self) -> None:
        """Open the request. Then reveal() puts the PIN on the computer's screen."""
        data = self._post("/nanoborealis/pair/start",
                          {"device": self.name, "commit": hashlib.sha256(self._client_nonce).hexdigest()})
        try:
            self._request = str(data["request"])
            self._server_nonce = bytes.fromhex(str(data["server_nonce"]))
        except (KeyError, ValueError) as e:
            raise PairingError("That address answered, but not as a NanoBorealis computer.") from e
        self.machine = str(data.get("machine") or self.host)

    def reveal(self) -> None:
        self._post("/nanoborealis/pair/reveal", {"request": self._request, "client_nonce": self._client_nonce.hex()})

    def expected_pin(self) -> str:
        return pairing_pin(self._client_nonce, self._server_nonce, self._certificate)

    def finish(self, pin: str) -> Machine:
        """Check the PIN here first (a typo costs nothing), then have the computer check it too."""
        pin = "".join(ch for ch in pin if ch.isdigit())
        if pin != self.expected_pin():
            raise WrongPin(f"That's not the PIN {self.machine} should be showing. Check the number on its "
                           "screen. If it matches what you typed, something on your network is posing as "
                           "that computer: don't pair.")
        data = self._post("/nanoborealis/pair/finish", {"request": self._request, "pin": pin})
        try:
            return Machine(self.host, self.port, str(data.get("machine") or self.machine), self.certificate_pem,
                           str(data["password"]), str(data["device_id"]))
        except KeyError as e:
            raise PairingError("The computer didn't finish pairing. Try again.") from e


LOCAL_SOCKET = "/run/nanoborealis/local.sock"


def pair_here(path: str = LOCAL_SOCKET) -> Machine | None:
    """On a NanoBorealis computer, pair the app with that computer itself, with no PIN: the
    computer's remote-access service asks the kernel who is connecting, and pairs administrators
    signed in there. None when this isn't a NanoBorealis computer."""
    if not hasattr(socket, "AF_UNIX") or not os.path.exists(path):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as local:
            local.settimeout(15)
            local.connect(path)
            local.sendall((json.dumps({"device": f"This computer ({getpass.getuser()})"}) + "\n").encode())
            answer = json.loads(local.makefile("rb").readline() or b"{}")
    except (OSError, ValueError) as e:
        raise PairingError(f"This computer's NanoBorealis service didn't answer: {e}") from e
    if not isinstance(answer, dict) or "error" in answer:
        raise PairingError(str(answer.get("error") if isinstance(answer, dict) else "Unexpected answer."))
    try:
        return Machine("127.0.0.1", int(answer["port"]), str(answer["machine"]), str(answer["certificate"]),
                       str(answer["password"]), str(answer["device_id"]))
    except (KeyError, TypeError, ValueError) as e:
        raise PairingError("This computer's NanoBorealis service gave an incomplete answer.") from e


def unpair(machine: Machine) -> None:
    """Ask the computer to forget this device. Best effort: the app forgets it either way."""
    try:
        call(machine.host, machine.port, "POST", "/nanoborealis/unpair", context=machine.context(), body={},
             headers={DEVICE_HEADER: machine.password}, timeout=8)
    except PairingError:
        pass
