"""POST /webhooks/email: Resend's signed `email.received` notices (spec 0085).

Same shape as the Ramp webhook: verify the signature over the raw body before parsing, store the
raw delivery (unique on Svix's message id), answer at once, 2xx for duplicates too. The notice
carries no email body; the worker fetches the email later. A bad signature is stored for
forensics, raises an alert, and gets 401. Check order: off (404), no current demo dataset (503,
so Resend retries), signature (401), store (200).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from ..db import system_tx
from ..flags import EmailMode
from . import signature

log = logging.getLogger(__name__)
router = APIRouter()

MAX_BODY = 64 * 1024
KEPT_HEADERS = ("content-type", "svix-id", "svix-timestamp", "user-agent")  # never the signature itself


def _reply(status: int, body: dict) -> JSONResponse:
    return JSONResponse(body, status_code=status)


@router.post("/webhooks/email")
async def email_webhook(request: Request) -> JSONResponse:
    settings, pool = request.app.state.settings, request.app.state.pool
    if settings.email_mode is not EmailMode.RESEND:
        return _reply(404, {"error": "not found"})
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return _reply(413, {"error": "payload too large"})
    headers = {k: v for k, v in request.headers.items() if k.lower() in KEPT_HEADERS}
    text = raw.decode("utf-8", errors="replace")

    async with system_tx(pool) as conn:
        dataset = await (await conn.execute(
            "select id from app.datasets where kind = 'demo' and retired_at is null order by created_at desc limit 1")).fetchone()
        if dataset is None:
            return _reply(503, {"error": "no current demo dataset"})
        dataset_id = dataset["id"]

        if not signature.verify(settings.resend_webhook_secret or "", request.headers, raw, int(time.time())):
            await conn.execute(
                """insert into app.inbound_webhooks (source, dataset_id, event_id, signature_valid, headers, raw_body, error, received_at)
                   values ('resend', %s, %s, false, %s, %s, 'bad signature', app.now(%s)) on conflict (source, event_id) do nothing""",
                (dataset_id, "unsigned:" + hashlib.sha256(raw).hexdigest(), Jsonb(headers), text, dataset_id))
            await conn.execute(
                """insert into app.alerts (dataset_id, kind, severity, message, raised_at)
                   values (%s, 'bad_webhook_signature', 'critical', 'A Resend webhook arrived with a bad signature and was refused.', app.now(%s))""",
                (dataset_id, dataset_id))
            log.warning("refused a Resend webhook with a bad signature for dataset %s", dataset_id)
            return _reply(401, {"error": "bad signature"})

        try:
            payload = json.loads(raw)
            event_type = payload.get("type") if isinstance(payload, dict) else None
        except ValueError:
            event_type = None
        inserted = await (await conn.execute(
            """insert into app.inbound_webhooks (source, dataset_id, event_id, event_type, signature_valid, headers, raw_body, received_at)
               values ('resend', %s, %s, %s, true, %s, %s, app.now(%s)) on conflict (source, event_id) do nothing returning id""",
            (dataset_id, request.headers["svix-id"], event_type, Jsonb(headers), text, dataset_id))).fetchone()
    return _reply(200, {"status": "accepted" if inserted else "duplicate"})
