"""POST /webhooks/ramp/{dataset_id}: Ramp's signed webhooks (spec 0002, D3).

Verify the signature over the raw body before parsing anything, store the raw delivery
(unique on Ramp's event id), and answer at once: well inside Ramp's 10-second limit, and
2xx for duplicates too. The worker does the Ramp calls later. A bad signature is stored for
forensics, raises an alert, and gets 401.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from .db import system_tx

log = logging.getLogger(__name__)
router = APIRouter()

MAX_BODY = 64 * 1024
KEPT_HEADERS = ("content-type", "x-ramp-signature", "x-ramp-event-id", "user-agent")


def signature_ok(secret: bytes, raw: bytes, header: str | None) -> bool:
    """hex(HMAC-SHA256(secret, raw body)), compared in constant time (encoding to confirm, spec 0002 Q3)."""
    if not header:
        return False
    expected = hmac.new(secret, raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip().lower())


def _reply(status: int, body: dict) -> JSONResponse:
    return JSONResponse(body, status_code=status)


@router.post("/webhooks/ramp/{dataset_id}")
async def ramp_webhook(dataset_id: UUID, request: Request) -> JSONResponse:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return _reply(413, {"error": "payload too large"})
    settings, pool = request.app.state.settings, request.app.state.pool
    valid = signature_ok(settings.ramp_webhook_secret, raw, request.headers.get("x-ramp-signature"))
    headers = {k: v for k, v in request.headers.items() if k.lower() in KEPT_HEADERS}

    async with system_tx(pool) as conn:
        dataset = await (await conn.execute(
            "select id from app.datasets where id = %s and retired_at is null", (dataset_id,))).fetchone()
        if dataset is None:
            return _reply(404, {"error": "unknown dataset"})

        if not valid:
            event_id = "unsigned:" + hashlib.sha256(raw).hexdigest()
            await conn.execute(
                """insert into app.inbound_webhooks (dataset_id, event_id, signature_valid, headers, raw_body, error, received_at)
                   values (%s, %s, false, %s, %s, 'bad signature', app.now(%s)) on conflict (source, event_id) do nothing""",
                (dataset_id, event_id, Jsonb(headers), raw.decode("utf-8", errors="replace"), dataset_id))
            await conn.execute(
                """insert into app.alerts (dataset_id, kind, severity, message, raised_at)
                   values (%s, 'bad_webhook_signature', 'critical', 'A Ramp webhook arrived with a bad signature and was refused.', app.now(%s))""",
                (dataset_id, dataset_id))
            log.warning("refused a Ramp webhook with a bad signature for dataset %s", dataset_id)
            return _reply(401, {"error": "bad signature"})

        try:
            payload = json.loads(raw)
            event_id = str(payload.get("id") or request.headers.get("x-ramp-event-id") or "")
            event_type = payload.get("type")
        except ValueError:
            payload, event_id, event_type = None, "", None
        if not event_id:
            event_id = "noid:" + hashlib.sha256(raw).hexdigest()
        inserted = await (await conn.execute(
            """insert into app.inbound_webhooks (dataset_id, event_id, event_type, signature_valid, headers, raw_body, received_at)
               values (%s, %s, %s, true, %s, %s, app.now(%s)) on conflict (source, event_id) do nothing returning id""",
            (dataset_id, event_id, event_type, Jsonb(headers), raw.decode("utf-8", errors="replace"), dataset_id))).fetchone()
    return _reply(200, {"status": "accepted" if inserted else "duplicate"})
