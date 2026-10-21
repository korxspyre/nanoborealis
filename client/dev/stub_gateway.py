# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (C) 2026 korxspyre
"""A stand-in for the nanobot gateway's WebUI endpoints, just enough to test pairing and the
remote-access service without a real agent:

    python dev/stub_gateway.py 18765 test-secret

GET /webui/bootstrap (with the WebUI password) issues a one-time WebSocket token and an API token,
GET /api/sessions lists one chat, and the WebSocket answers new_chat, attach and message. Like the
real gateway, it builds ws_url from the Host header it was sent.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import urllib.parse

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18765
SECRET = sys.argv[2] if len(sys.argv) > 2 else "test-secret"
ws_tokens: set[str] = set()
api_tokens: set[str] = set()


def json_response(status: int, payload: dict) -> Response:
    body = json.dumps(payload).encode()
    headers = Headers([("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
    return Response(status, "OK" if status == 200 else "Error", headers, body)


def process_request(connection, request):
    path = urllib.parse.urlsplit(request.path)
    bearer = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if path.path == "/webui/bootstrap":
        if bearer != SECRET:
            return json_response(401, {"error": "unauthorized"})
        token, api = secrets.token_hex(8), secrets.token_hex(8)
        ws_tokens.add(token)
        api_tokens.add(api)
        host = request.headers.get("Host", f"127.0.0.1:{PORT}")
        return json_response(200, {"token": token, "ws_url": f"ws://{host}/", "api_token": api,
                                   "expires_in": 300, "model_name": "stub-model"})
    if path.path == "/api/sessions":
        if bearer not in api_tokens:
            return json_response(401, {"error": "unauthorized"})
        return json_response(200, {"sessions": [{"key": "websocket:chat-1", "title": "A chat"}]})
    if request.headers.get("Upgrade", "").lower() != "websocket":
        return json_response(404, {"error": "not found"})
    token = dict(urllib.parse.parse_qsl(path.query)).get("token", "")
    if token not in ws_tokens:
        return json_response(401, {"error": "bad token"})
    ws_tokens.discard(token)  # one-time, as in nanobot
    return None


async def chat(ws) -> None:
    async for raw in ws:
        message = json.loads(raw)
        if message.get("type") == "new_chat":
            await ws.send(json.dumps({"event": "attached", "chat_id": secrets.token_hex(4)}))
        elif message.get("type") == "attach":
            await ws.send(json.dumps({"event": "attached", "chat_id": message.get("chat_id")}))
        elif message.get("type") == "message":
            chat_id = message.get("chat_id")
            await ws.send(json.dumps({"event": "delta", "chat_id": chat_id, "text": "hello back"}))
            await ws.send(json.dumps({"event": "turn_end", "chat_id": chat_id}))


async def main() -> None:
    async with serve(chat, "127.0.0.1", PORT, process_request=process_request):
        print(f"stub gateway on 127.0.0.1:{PORT}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
