"""The worker fetches each stored email from Resend and records it without its body (spec 0085, Task 4)."""
from __future__ import annotations

import json
import logging
import uuid

import httpx
import pytest
from dialback_core.db import system_tx
from dialback_core.email_door.masking import mask_digit_runs
from dialback_core.email_door.resend import ResendClient, ResendError, html_to_text
from flows import FakeResend, assert_nowhere, received
from test_email_route import SECRET, _now, demo, sign  # noqa: F401  (demo is a fixture)

pytestmark = pytest.mark.anyio

BODY_MARKER = "BODYMARKER-7f3"
ACCOUNT = "000123454821"


async def deliver(w, email_id: str | None = None, sender: str = "Dana <ar@vendor.example>") -> str:
    email_id = email_id or f"em_{uuid.uuid4().hex}"
    raw = json.dumps({"type": "email.received", "created_at": "2026-11-10T00:00:00Z",
                      "data": {"email_id": email_id, "from": sender, "to": ["inbox@x.example"], "message_id": f"<{email_id}@v.example>"}}).encode()
    r = await w.core.post("/webhooks/email", content=raw, headers=sign(raw, msg_id=f"msg_{uuid.uuid4().hex}", ts=_now()))
    assert r.status_code == 200, r.text
    return email_id


async def rows(w) -> list[dict]:
    async with system_tx(w.pool) as conn:
        return await (await conn.execute("select * from app.inbound_emails where dataset_id = %s order by id", (w.dataset.id,))).fetchall()


async def delivery(w) -> dict:
    async with system_tx(w.pool) as conn:
        return await (await conn.execute("select * from app.inbound_webhooks where source = 'resend' and dataset_id = %s", (w.dataset.id,))).fetchone()


async def set_vendors(w, vendors: list[uuid.UUID]) -> None:
    async with system_tx(w.pool) as conn:
        await conn.execute("update app.datasets set email_door_vendors = %s where id = %s", (vendors, w.dataset.id))


async def test_a_delivery_is_fetched_and_recorded_without_its_body(demo, caplog):
    caplog.set_level(logging.DEBUG)
    await set_vendors(demo, [uuid.uuid4()])
    email_id = await deliver(demo)
    demo.worker.resend = FakeResend({email_id: received(email_id, text=f"{BODY_MARKER} wire it", subject=f"Details for {ACCOUNT}")})
    await demo.worker.tick(demo.dataset.id)
    (row,) = await rows(demo)
    assert (row["outcome"], row["sender_domain"], row["raw_sha256"], row["resend_email_id"]) == ("not_flagged", "vendor.example", "ab" * 32, email_id)
    assert row["sender"] == "Dana <ar@vendor.example>" and row["has_attachments"] is False and row["processed_at"] is not None
    assert row["subject"] == "Details for ********4821"
    assert (await delivery(demo))["processed_at"] is not None
    await assert_nowhere(demo.pool, caplog, BODY_MARKER)
    await assert_nowhere(demo.pool, caplog, ACCOUNT)


async def test_the_subject_is_stored_masked(demo, caplog):
    email_id = await deliver(demo)
    demo.worker.resend = FakeResend({email_id: received(email_id, subject="New acct 0001 2345 4821")})
    await demo.worker.tick(demo.dataset.id)
    assert (await rows(demo))[0]["subject"] == "New acct **** **** 4821"
    await assert_nowhere(demo.pool, caplog, ACCOUNT)


def test_mask_digit_runs():
    assert mask_digit_runs("New acct 0001 2345 4821") == "New acct **** **** 4821"
    assert mask_digit_runs("acct 0001-2345-4821 ok") == "acct ****-****-4821 ok"
    assert mask_digit_runs("000123454821") == "********4821"
    assert mask_digit_runs("12345") == "*2345"
    assert mask_digit_runs("PO 1234 and 55") == "PO 1234 and 55"       # short runs stay
    assert mask_digit_runs("no digits") == "no digits"


async def test_html_only_email_becomes_text(pool):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/emails/receiving/em_1" and request.headers["authorization"] == "Bearer re_key"
        return httpx.Response(200, json={"from": "a@b.example", "to": ["i@x.example"], "reply_to": [], "subject": "S", "html":
                                         "<style>p{}</style><p>Please  update</p><div>acct <b>1234</b></div>", "text": None,
                                         "headers": {"Message-ID": "<m@b>", "References": "<r1@b> <r2@b>"}, "authentication": None,
                                         "message_id": "<m@b>", "raw": "RAW", "attachments": [{"filename": "x"}]})
    client = ResendClient("re_key", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    email = await client.get_received("em_1")
    assert email.text == "Please update\nacct 1234"
    assert email.has_attachments and email.authentication is None and email.headers["references"] == "<r1@b> <r2@b>"
    assert len(email.raw_sha256) == 64 and "RAW" not in repr(email)
    assert html_to_text("a<br>b<script>x()</script>") == "a\nb"


@pytest.mark.parametrize("status,retryable", [(429, True), (500, True), (503, True), (404, False), (401, False)])
async def test_resend_status_classes(status, retryable):
    client = ResendClient("k", httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, text="SECRET-BODY"))))
    with pytest.raises(ResendError) as err:
        await client.get_received("em_1")
    assert err.value.retryable is retryable and "SECRET-BODY" not in str(err.value)


async def test_a_network_error_is_retryable():
    def boom(request):
        raise httpx.ConnectError("down")
    client = ResendClient("k", httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    with pytest.raises(ResendError) as err:
        await client.get_received("em_1")
    assert err.value.retryable


async def test_thread_id_prefers_references_then_in_reply_to(demo):
    ids = [await deliver(demo) for _ in range(3)]
    demo.worker.resend = FakeResend({
        ids[0]: received(ids[0], headers={"references": "<root@v> <mid@v>", "in-reply-to": "<mid@v>", "message-id": "<m0@v>"}),
        ids[1]: received(ids[1], headers={"in-reply-to": "<parent@v>", "message-id": "<m1@v>"}),
        ids[2]: received(ids[2], message_id="<m2@v>")})
    await demo.worker.tick(demo.dataset.id)
    by_id = {r["resend_email_id"]: r["thread_id"] for r in await rows(demo)}
    assert by_id == {ids[0]: "<root@v>", ids[1]: "<parent@v>", ids[2]: "<m2@v>"}


async def test_resend_failing_five_times_is_listed_failed_and_opens_nothing(demo):
    email_id = await deliver(demo)
    demo.worker.resend = FakeResend({email_id: ResendError("resend answered 503", retryable=True)})
    for n in range(1, 5):
        await demo.worker.tick(demo.dataset.id)
        (row,) = await rows(demo)
        assert (row["outcome"], row["tries"]) == ("pending", n)
        assert (await delivery(demo))["processed_at"] is None
    await demo.worker.tick(demo.dataset.id)
    (row,) = await rows(demo)
    assert (row["outcome"], row["tries"], row["error"]) == ("failed", 5, "resend answered 503")
    assert (await delivery(demo))["processed_at"] is not None
    assert len(demo.worker.resend.fetched) == 5
    await demo.worker.tick(demo.dataset.id)
    assert len(demo.worker.resend.fetched) == 5
    async with system_tx(demo.pool) as conn:
        assert (await (await conn.execute("select count(*) as n from app.cases where dataset_id = %s", (demo.dataset.id,))).fetchone())["n"] == 0


async def test_a_non_retryable_error_fails_at_once(demo):
    email_id = await deliver(demo)
    demo.worker.resend = FakeResend({email_id: ResendError("resend answered 404")})
    await demo.worker.tick(demo.dataset.id)
    (row,) = await rows(demo)
    assert row["outcome"] == "failed" and (await delivery(demo))["processed_at"] is not None


async def test_door_off_dataset_is_recorded_door_off(demo):
    email_id = await deliver(demo)
    demo.worker.resend = FakeResend({email_id: received(email_id)})
    await demo.worker.tick(demo.dataset.id)
    assert (await rows(demo))[0]["outcome"] == "door_off"


async def test_a_second_notice_for_the_same_email_is_not_recorded_twice(demo):
    email_id = await deliver(demo)
    await deliver(demo, email_id)
    demo.worker.resend = FakeResend({email_id: received(email_id)})
    await demo.worker.tick(demo.dataset.id)
    assert len(await rows(demo)) == 1 and demo.worker.resend.fetched == [email_id]
