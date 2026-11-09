#!/usr/bin/env python3
"""The voice image's WebSocket check (spec 0034, B4), run by the `images` workflow.

Twilio's ConversationRelay runs a live call over a WebSocket at /twilio/relay/{call_id}. If the image
has no WebSocket library, uvicorn answers that upgrade with a 404 and every live call dies before the
first word. The health check cannot see this, so this probe fails unless a running voice service:

1. answers an unsigned POST to /twilio/status/{id} with 401. Only live mode mounts the Twilio routes,
   and a live config with any key missing quietly runs the text line instead, whose 404 on the relay
   would look exactly like a missing WebSocket library. This step says which of the two broke.
2. answers an RFC 6455 upgrade to /twilio/relay/{id} with 101 and the Sec-WebSocket-Accept value the
   key requires, so a WebSocket server answered, not a plain HTTP reply.

Run it against a voice service started with DIALBACK_VOICE=live and every live setting filled in:

    python3 scripts/ci/ws_probe.py http://localhost:8081
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import sys
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from urllib.parse import urlsplit

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455, section 1.3
TIMEOUT_S = 5


def live_routes_mounted(base_url: str, call_id: str) -> tuple[bool, str]:
    """True when an unsigned status callback is refused with 401, which only the live routes do."""
    req = urllib.request.Request(f"{base_url}/twilio/status/{call_id}", data=b"CallStatus=completed",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            status = resp.status
    except urllib.error.HTTPError as err:
        status = err.code
    return status == 401, f"unsigned POST /twilio/status/{{id}} answered {status}, want 401"


def websocket_upgrade(base_url: str, call_id: str) -> tuple[bool, str]:
    """True when the relay route answers a WebSocket handshake with 101 and the right accept value."""
    parts = urlsplit(base_url)
    host, port = parts.hostname or "localhost", parts.port or 80
    key = base64.b64encode(os.urandom(16)).decode()
    want_accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    handshake = (f"GET /twilio/relay/{call_id} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                 "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                 f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
    head = b""
    with socket.create_connection((host, port), timeout=TIMEOUT_S) as sock:
        sock.sendall(handshake.encode())
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                break
            head += chunk
    lines = head.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
    status_line = lines[0]
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    is_101 = status_line.split(" ")[1:2] == ["101"]
    accept_ok = headers.get("sec-websocket-accept") == want_accept
    detail = (f"upgrade GET /twilio/relay/{{id}} answered {status_line or '(nothing)'!r}, want 101; "
              f"Sec-WebSocket-Accept {'matches' if accept_ok else 'missing or wrong'}")
    return is_101 and accept_ok, detail


CHECKS: tuple[Callable[[str, str], tuple[bool, str]], ...] = (live_routes_mounted, websocket_upgrade)
HINTS = {
    live_routes_mounted: "the live routes are not mounted: a live setting is missing (the service log says "
                         "'running the text line'), so the WebSocket was not tested",
    websocket_upgrade: "the live routes are up but the WebSocket upgrade failed: check that the image installs "
                       "uvicorn[standard] (uvicorn logs 'No supported WebSocket library detected')",
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} BASE_URL   (for example http://localhost:8081)", file=sys.stderr)
        return 2
    base_url = argv[1].rstrip("/")
    call_id = str(uuid.uuid4())
    for check in CHECKS:
        try:
            ok, detail = check(base_url, call_id)
            hint = HINTS[check]
        except OSError as err:  # refused, reset or timed out: no answer is a failure
            ok, detail = False, f"{check.__name__}: {err}"
            hint = f"nothing answered at {base_url}: is the service running on that port?"
        print(("ok    " if ok else "FAIL  ") + detail)
        if not ok:
            print(f"      {hint}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
