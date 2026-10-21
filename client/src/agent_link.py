# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""Talks to a NanoBorealis agent over the nanobot gateway's WebUI protocol.

Same flow as nanobot's own WebUI: GET /webui/bootstrap returns a one-time WebSocket token and a
short-lived REST token, then typed JSON envelopes travel over the WebSocket in both directions.
The computer's remote-access service sits in front: every request goes over TLS pinned to the
paired computer's certificate and carries this device's password (see pairing.py), and the
service supplies the WebUI's own password. Nothing here imports Flet, so the protocol can be
exercised on its own (see dev/smoke.py).
"""

from __future__ import annotations

import asyncio
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import websockets

from pairing import DEVICE_HEADER, Machine

SESSION_KEY_PREFIX = "websocket:"  # nanobot stores WebUI chats as "websocket:<chat_id>"

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class AuthError(Exception):
    """The computer doesn't accept this device (never paired, or removed), or isn't the one it paired with."""


class WrongComputer(AuthError):
    """Another computer answers at the paired one's address: it has moved (or was reinstalled)."""


class LinkError(Exception):
    """The agent could not be reached, or answered with something unexpected."""


class Unreachable(LinkError):
    """Nothing answers at the computer's address: it's off, asleep, or has moved."""


NOT_PAIRED = "This computer doesn't know this device anymore. Pair it again."
WRONG_COMPUTER = ("The computer at this address isn't the one this app paired with. If you reinstalled it, "
                  "pair again.")
LOOK_EVERY = 120.0  # seconds between looks for a computer that stopped answering at its address


def _with_query(url: str, **params: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query) + list(params.items())
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _get_json(url: str, headers: dict[str, str], context: ssl.SSLContext, timeout: float = 15) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError(NOT_PAIRED) from e
        if e.code == 502:
            raise LinkError("The agent isn't running on that computer yet.") from e
        raise LinkError(f"The agent answered HTTP {e.code} for {urllib.parse.urlsplit(url).path}.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise WrongComputer(WRONG_COMPUTER) from e
        raise Unreachable(f"Cannot reach the agent: {reason}") from e
    except json.JSONDecodeError as e:
        raise LinkError("The agent's answer was not JSON. Is this a NanoBorealis address?") from e


@dataclass(frozen=True)
class Bootstrap:
    ws_token: str
    ws_url: str
    api_token: str | None
    expires_in: float
    model_name: str | None


class AgentLink:
    """One client connection to the agent. Call run() in a task; it reconnects by itself, and with
    `locate` (discovery.locate) it finds the computer again when its address changes."""

    def __init__(self, machine: Machine, on_event: EventHandler, *, client_id: str | None = None,
                 locate: Callable[[Machine], str | None] | None = None):
        self.machine = machine
        self._locate = locate
        self.base_url = machine.base_url
        self._context = machine.context()
        self._device = {DEVICE_HEADER: machine.password}
        self._on_event = on_event
        self.client_id = client_id or f"nanoborealis-client-{uuid.uuid4().hex[:12]}"
        self.model_name: str | None = None
        # The chat the UI is showing; re-attached after every reconnect so replies keep arriving.
        self.chat_id: str | None = None
        self._ws: Any = None
        self._stopped = False
        self._api_token: str | None = None
        self._api_token_expiry = 0.0
        self._new_chat_waiters: list[asyncio.Future[str]] = []
        self._attach_waiters: dict[str, asyncio.Future[str]] = {}
        self._requests: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.limits: dict[str, Any] = {}

    @property
    def connected(self) -> bool:
        return self._ws is not None

    # -- HTTP ----------------------------------------------------------------

    async def bootstrap(self) -> Bootstrap:
        data = await asyncio.to_thread(_get_json, f"{self.base_url}/webui/bootstrap", self._device, self._context)
        token = data.get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise LinkError("The agent did not issue a connection token.")
        # The WebSocket goes to the address this app used, over TLS: the agent behind the
        # remote-access service can't know either, so only its path is taken.
        given = data.get("ws_url")
        path = urllib.parse.urlsplit(given) if isinstance(given, str) and "://" in given else \
            urllib.parse.urlsplit(str(data.get("ws_path") or "/"))
        ws_url = urllib.parse.urlunsplit(
            ("wss", urllib.parse.urlsplit(self.base_url).netloc, path.path or "/", path.query, ""))
        expires_in = float(data.get("expires_in") or 300)
        api_token = data.get("api_token") if isinstance(data.get("api_token"), str) else None
        if api_token:
            self._api_token = api_token
            self._api_token_expiry = time.monotonic() + expires_in - 30
        model_name = data.get("model_name") if isinstance(data.get("model_name"), str) else None
        if isinstance(data.get("limits"), dict):
            self.limits = data["limits"]
        return Bootstrap(token, ws_url, api_token, expires_in, model_name)

    async def _api_get(self, path: str, timeout: float = 15) -> Any:
        if not self._api_token or time.monotonic() > self._api_token_expiry:
            await self.bootstrap()
        if not self._api_token:
            raise LinkError("The agent did not issue an API token.")
        headers = {**self._device, "Authorization": f"Bearer {self._api_token}"}
        return await asyncio.to_thread(_get_json, f"{self.base_url}{path}", headers, self._context, timeout)

    async def api_get(self, path: str, timeout: float = 30) -> Any:
        """One of the WebUI's REST reads: /api/settings, /api/commands, /api/webui/automations..."""
        return await self._api_get(path, timeout)

    async def list_chats(self) -> list[dict[str, Any]]:
        """Saved WebUI chats, newest first. Each row carries 'chat_id' plus nanobot's fields."""
        data = await self._api_get("/api/sessions")
        rows = data.get("sessions", []) if isinstance(data, dict) else []
        chats = []
        for row in rows:
            key = row.get("key") if isinstance(row, dict) else None
            if isinstance(key, str) and key.startswith(SESSION_KEY_PREFIX):
                chats.append({**row, "chat_id": key[len(SESSION_KEY_PREFIX):]})
        return chats

    async def load_thread(self, chat_id: str, limit: int = 200) -> dict[str, Any]:
        key = urllib.parse.quote(f"{SESSION_KEY_PREFIX}{chat_id}", safe="")
        data = await self._api_get(f"/api/sessions/{key}/webui-thread?limit={limit}")
        return data if isinstance(data, dict) else {}

    # -- WebSocket -----------------------------------------------------------

    async def run(self) -> None:
        """Stay connected until close(). Raises AuthError at once when the computer turns this device
        down; other failures are retried. When nothing, or another computer, answers at its address,
        it's looked for (locate) now and then, and followed to where it is now (a "moved" event)."""
        delay = 1.0
        misses = 0  # failures in a row where the computer wasn't there
        looked = -LOOK_EVERY
        while not self._stopped:
            detail = ""
            gone = False
            try:
                boot = await self.bootstrap()
                self.model_name = boot.model_name or self.model_name
                url = _with_query(boot.ws_url, client_id=self.client_id, token=boot.ws_token)
                async with websockets.connect(url, ssl=self._context, additional_headers=self._device,
                                              max_size=32 * 1024 * 1024, open_timeout=15) as ws:
                    self._ws = ws
                    delay = 1.0
                    await self._emit({"event": "link_up", "model_name": self.model_name})
                    if self.chat_id:
                        await self._send({"type": "attach", "chat_id": self.chat_id})
                    async for raw in ws:
                        if isinstance(raw, str):
                            await self._dispatch(raw)
                    detail = "The agent closed the connection."
            except (WrongComputer, ssl.SSLCertVerificationError):
                detail = f"Another computer answers at {self.machine.host} now."
                gone, misses = True, max(misses, 1)
            except AuthError:
                raise
            except websockets.InvalidStatus as e:
                if e.response.status_code in (401, 403):
                    raise AuthError(NOT_PAIRED) from e
                detail = f"The agent answered HTTP {e.response.status_code}."
            except (LinkError, OSError, asyncio.TimeoutError, websockets.WebSocketException) as e:
                detail = str(e) or type(e).__name__
                gone = isinstance(e, (Unreachable, OSError, asyncio.TimeoutError))
            finally:
                self._ws = None
                self._fail_waiters()
            if self._stopped:
                break
            misses = misses + 1 if gone else 0
            if misses >= 2 and self._locate is not None and time.monotonic() - looked >= LOOK_EVERY:
                looked = time.monotonic()
                await self._emit({"event": "link_down", "detail": f"Looking for {self.machine.name} on the network...",
                                  "retry_in": 0})
                address = await asyncio.to_thread(self._locate, self.machine)
                if address and address != self.machine.host and not self._stopped:
                    self.machine.host = address
                    self.base_url = self.machine.base_url
                    await self._emit({"event": "moved", "host": address})
                    delay, misses = 1.0, 0
                    continue
            await self._emit({"event": "link_down", "detail": detail, "retry_in": delay})
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def close(self) -> None:
        self._stopped = True
        ws = self._ws
        if ws is not None:
            await ws.close()

    async def _dispatch(self, raw: str) -> None:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            return
        if event["event"] == "webui_response":
            waiter = self._requests.pop(str(event.get("request_id")), None)
            if waiter is not None and not waiter.done():
                waiter.set_result(event)
            return
        if event["event"] == "attached":
            chat_id = event.get("chat_id")
            waiter = self._attach_waiters.pop(chat_id, None) if isinstance(chat_id, str) else None
            if waiter is None and self._new_chat_waiters:
                waiter = self._new_chat_waiters.pop(0)
            if waiter is not None and not waiter.done():
                waiter.set_result(chat_id)
        elif event["event"] in ("runtime_model_updated", "turn_model_updated"):
            if isinstance(event.get("model_name"), str) and event["event"] == "runtime_model_updated":
                self.model_name = event["model_name"]
        await self._emit(event)

    async def _emit(self, event: dict[str, Any]) -> None:
        try:
            await self._on_event(event)
        except Exception as e:  # a UI bug must not take the connection down
            print(f"nanoborealis-client: event handler failed on {event.get('event')}: {e!r}")

    async def _send(self, envelope: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            raise LinkError("Not connected to the agent.")
        await ws.send(json.dumps(envelope, ensure_ascii=False))

    def _fail_waiters(self) -> None:
        for waiter in [*self._new_chat_waiters, *self._attach_waiters.values(), *self._requests.values()]:
            if not waiter.done():
                waiter.set_exception(LinkError("Connection lost."))
        self._new_chat_waiters.clear()
        self._attach_waiters.clear()
        self._requests.clear()

    # -- Commands ------------------------------------------------------------

    async def new_chat(self, workspace_scope: dict[str, Any] | None = None) -> str:
        """Create a chat on the agent and return its id. workspace_scope: {"project_path": a folder in
        the agent's container, "access_mode": "restricted" (only that folder) or "full"}."""
        waiter: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._new_chat_waiters.append(waiter)
        try:
            await self._send({"type": "new_chat", **({"workspace_scope": workspace_scope} if workspace_scope else {})})
            chat_id = await asyncio.wait_for(waiter, 20)
        finally:
            if waiter in self._new_chat_waiters:
                self._new_chat_waiters.remove(waiter)
        self.chat_id = chat_id
        return chat_id

    async def attach(self, chat_id: str) -> None:
        """Subscribe to an existing chat so its replies (including in-flight ones) arrive here."""
        waiter: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._attach_waiters[chat_id] = waiter
        try:
            await self._send({"type": "attach", "chat_id": chat_id})
            await asyncio.wait_for(waiter, 20)
        finally:
            self._attach_waiters.pop(chat_id, None)
        self.chat_id = chat_id

    async def send_message(self, chat_id: str, text: str, media: list[dict[str, str]] | None = None,
                           turn_id: str | None = None, **extra: Any) -> str:
        """Send a message as the WebUI does (webui: true), so the agent titles new chats, echoes it
        to this chat's other devices, and ties the turn's events to turn_id. Returns turn_id.
        media: [{"data_url": "data:<mime>;base64,...", "name": ...}]; extra: e.g. intent="create_automation"."""
        turn_id = turn_id or f"turn-{uuid.uuid4().hex[:16]}"
        envelope: dict[str, Any] = {"type": "message", "chat_id": chat_id, "content": text, "webui": True,
                                    "turn_id": turn_id, **extra}
        if media:
            envelope["media"] = media
        await self._send(envelope)
        return turn_id

    async def set_workspace_scope(self, chat_id: str, workspace_scope: dict[str, Any]) -> None:
        """Move a chat to another project folder (refused while it's working)."""
        await self._send({"type": "set_workspace_scope", "chat_id": chat_id, "workspace_scope": workspace_scope})

    async def request(self, action: str, payload: dict[str, Any] | None = None, timeout: float = 30) -> Any:
        """A WebUI management request (settings, presets, automations, skills, chats), answered by
        webui_response. Raises LinkError with the agent's reason when it's turned down."""
        request_id = f"nb-{uuid.uuid4().hex}"
        waiter: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._requests[request_id] = waiter
        try:
            await self._send({"type": "webui_request", "request_id": request_id, "action": action,
                              "payload": payload or {}})
            answer = await asyncio.wait_for(waiter, timeout)
        finally:
            self._requests.pop(request_id, None)
        if not answer.get("ok"):
            error = answer.get("error") if isinstance(answer.get("error"), dict) else {}
            raise LinkError(str(error.get("message") or f"The agent turned down {action}."))
        return answer.get("result")

    async def new_temporary_chat(self) -> str:
        """A chat nothing is saved from (for checks), closed with discard_temporary_chat."""
        waiter: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._new_chat_waiters.append(waiter)
        try:
            await self._send({"type": "new_temporary_chat"})
            return await asyncio.wait_for(waiter, 20)
        finally:
            if waiter in self._new_chat_waiters:
                self._new_chat_waiters.remove(waiter)

    async def discard_temporary_chat(self, chat_id: str) -> None:
        await self._send({"type": "discard_temporary_chat", "chat_id": chat_id})

    async def stop_turn(self, chat_id: str) -> None:
        """Cancel the agent's current turn in this chat (nanobot's /stop command)."""
        await self.send_message(chat_id, "/stop")
