"""The worker fetches each stored email from Resend and records it without its body (spec 0085, Task 4)."""
from __future__ import annotations

import json
import logging
import uuid

import httpx
import pytest
from dialback_core.db import system_tx
from dialback_core.email_door import inbox
from dialback_core.email_door.door import handle_email
from dialback_core.email_door.masking import mask_digit_runs
from dialback_core.email_door.resend import ResendClient, ResendError, html_to_text
from flows import FakeResend, assert_nowhere, received
from test_email_route import SECRET, _now, demo, sign  # noqa: F401  (demo is a fixture)

pytestmark = pytest.mark.anyio

BODY_MARKER = "BODYMARKER-7f3"
ACCOUNT = "000123454821"


async def deliver(w, email_id: str | None = None, sender: str = "Dana <ar@vendor.example>", kind: str = "email.received") -> str:
    email_id = email_id or f"em_{uuid.uuid4().hex}"
    raw = json.dumps({"type": kind, "created_at": "2026-11-10T00:00:00Z",
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
    caplog.set_level(logging.DEBUG)
    email_id = await deliver(demo)
    demo.worker.resend = FakeResend({email_id: received(email_id, subject="New acct 0001 2345 4821")})
    await demo.worker.tick(demo.dataset.id)
    assert (await rows(demo))[0]["subject"] == "New acct **** **** 4821"
    await assert_nowhere(demo.pool, caplog, ACCOUNT)
    await assert_nowhere(demo.pool, caplog, "0001 2345 4821")


def test_mask_digit_runs():
    assert mask_digit_runs("New acct 0001 2345 4821") == "New acct **** **** 4821"
    assert mask_digit_runs("acct 0001-2345-4821 ok") == "acct ****-****-4821 ok"
    assert mask_digit_runs("000123454821") == "********4821"
    assert mask_digit_runs("12345") == "*2345"
    assert mask_digit_runs("PO 1234 and 55") == "PO 1234 and 55"       # short runs stay
    assert mask_digit_runs("no digits") == "no digits"


@pytest.mark.parametrize("subject,masked", [
    ("acct 0001.2345.4821", "acct ****.****.4821"),
    ("acct 0001\u00a02345\u00a04821", "acct ****\u00a0****\u00a04821"),
    ("acct 0001  2345 4821", "acct ****  **** 4821"),
    ("acct 0001 / 2345_4821", "acct **** / ****_4821"),
])
def test_mask_digit_runs_other_separators(subject, masked):
    assert mask_digit_runs(subject) == masked


async def test_the_stored_sender_and_notice_are_masked(demo, caplog):
    caplog.set_level(logging.DEBUG)
    email_id = await deliver(demo, sender="Acct 0001 2345 4821 <ar@vendor.example>")
    assert json.loads((await delivery(demo))["raw_body"])["data"]["from"] == "Acct **** **** 4821 <ar@vendor.example>"
    demo.worker.resend = FakeResend({email_id: received(email_id)})
    await demo.worker.tick(demo.dataset.id)
    assert (await rows(demo))[0]["sender"] == "Acct **** **** 4821 <ar@vendor.example>"
    await assert_nowhere(demo.pool, caplog, "0001 2345 4821")


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def resend_json(**changes) -> dict:
    base = {"from": "a@b.example", "to": ["i@x.example"], "reply_to": [], "subject": "S", "html": None, "text": None,
            "headers": {"Message-ID": "<m@b>"}, "authentication": None, "message_id": "<m@b>", "raw": "RAW", "attachments": []}
    return base | changes


async def test_html_only_email_becomes_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/emails/receiving/em_1" and request.headers["authorization"] == "Bearer re_key"
        return httpx.Response(200, json=resend_json(
            html="<style>p{}</style><p>Please  update</p><div>acct <b>1234</b></div>",
            headers={"Message-ID": "<m@b>", "References": "<r1@b> <r2@b>"}, attachments=[{"filename": "x"}]))
    async with mock_client(handler) as http:
        email = await ResendClient("re_key", http).get_received("em_1")
    assert email.text == "Please update\nacct 1234" and email.html_text == email.text
    assert email.has_attachments and email.authentication is None and email.headers["references"] == "<r1@b> <r2@b>"
    assert len(email.raw_sha256) == 64 and "RAW" not in repr(email)
    assert html_to_text("a<br>b<script>x()</script>") == "a\nb"


def test_html_with_an_unclosed_head_keeps_its_body():
    html = "<html><head><meta charset=utf-8><body><p>Our bank details changed, acct 1234</p></body></html>"
    assert html_to_text(html) == "Our bank details changed, acct 1234"


async def test_both_parts_are_kept_and_neither_is_in_the_repr():
    async with mock_client(lambda r: httpx.Response(200, json=resend_json(
            text="TEXTPART-harmless", html="<p>HTMLPART-send money to 999</p>"))) as http:
        email = await ResendClient("k", http).get_received("em_1")
    assert email.text == "TEXTPART-harmless" and email.html_text == "HTMLPART-send money to 999"
    assert "TEXTPART" not in repr(email) and "HTMLPART" not in repr(email)


async def test_the_email_id_is_quoted_in_the_path():
    seen = []
    async with mock_client(lambda r: seen.append(r.url.raw_path.decode()) or httpx.Response(200, json=resend_json())) as http:
        await ResendClient("k", http).get_received("a/b?c")
    assert seen == ["/emails/receiving/a%2Fb%3Fc"]


@pytest.mark.parametrize("status,retryable", [(429, True), (500, True), (503, True), (404, False), (401, False)])
async def test_resend_status_classes(status, retryable):
    async with mock_client(lambda r: httpx.Response(status, text="SECRET-BODY")) as http:
        with pytest.raises(ResendError) as err:
            await ResendClient("k", http).get_received("em_1")
    assert err.value.retryable is retryable and "SECRET-BODY" not in str(err.value)


async def test_a_network_error_is_retryable():
    def boom(request):
        raise httpx.ConnectError("down")
    async with mock_client(boom) as http:
        with pytest.raises(ResendError) as err:
            await ResendClient("k", http).get_received("em_1")
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


async def test_resend_failing_five_times_is_listed_failed_and_opens_nothing(demo, monkeypatch):
    monkeypatch.setattr(inbox, "RETRY_SECONDS", (0, 0, 0, 0))
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


async def test_the_claim_skips_unsigned_and_other_event_types(demo):
    await deliver(demo, kind="email.bounced")
    bad = await demo.core.post("/webhooks/email", content=b"{}", headers={"svix-id": "msg_x", "svix-timestamp": "1", "svix-signature": "v1,AAAA"})
    assert bad.status_code == 401
    demo.worker.resend = FakeResend()
    await demo.worker.tick(demo.dataset.id)
    assert demo.worker.resend.fetched == [] and await rows(demo) == []
    async with system_tx(demo.pool) as conn:
        left = await (await conn.execute("select count(*) as n from app.inbound_webhooks where dataset_id = %s and processed_at is null",
                                         (demo.dataset.id,))).fetchone()
    assert left["n"] == 2


async def test_a_nul_byte_in_a_subject_or_sender_is_stripped(demo):
    await set_vendors(demo, [uuid.uuid4()])
    email_id = await deliver(demo, sender="Da\x00na <ar@vendor.example>")
    demo.worker.resend = FakeResend({email_id: received(email_id, subject="a\x00b 0001 2345 4821", message_id="<m\x00@v>")})
    await demo.worker.tick(demo.dataset.id)
    (row,) = await rows(demo)
    assert (row["outcome"], row["subject"], row["sender"], row["message_id"]) == ("not_flagged", "ab **** **** 4821", "Dana <ar@vendor.example>", "<m@v>")
    assert (await delivery(demo))["processed_at"] is not None


async def test_an_unexpected_error_on_one_email_does_not_stall_the_rest(demo, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(inbox, "RETRY_SECONDS", (0, 0, 0, 0))
    await set_vendors(demo, [uuid.uuid4()])
    bad, good = await deliver(demo), await deliver(demo)
    demo.worker.resend = FakeResend({bad: received(bad), good: received(good)})
    real = inbox.handle_email

    async def flaky(pool, settings, keys, resend, dataset_id, delivery_id, email):
        if email.email_id == bad:
            raise RuntimeError(f"boom {ACCOUNT}")
        return await real(pool, settings, keys, resend, dataset_id, delivery_id, email)
    monkeypatch.setattr(inbox, "handle_email", flaky)
    await demo.worker.tick(demo.dataset.id)  # does not raise
    by_id = {r["resend_email_id"]: r for r in await rows(demo)}
    assert by_id[good]["outcome"] == "not_flagged" and by_id[bad]["outcome"] == "pending" and by_id[bad]["tries"] == 1
    for _ in range(4):
        await demo.worker.tick(demo.dataset.id)
    by_id = {r["resend_email_id"]: r for r in await rows(demo)}
    assert (by_id[bad]["outcome"], by_id[bad]["tries"], by_id[bad]["error"]) == ("failed", 5, "RuntimeError")
    async with system_tx(demo.pool) as conn:
        assert (await (await conn.execute("select count(*) as n from app.inbound_webhooks where dataset_id = %s and processed_at is null",
                                          (demo.dataset.id,))).fetchone())["n"] == 0
    await assert_nowhere(demo.pool, caplog, ACCOUNT)


async def test_retries_are_spaced_and_not_made_early(demo):
    email_id = await deliver(demo)
    demo.worker.resend = FakeResend({email_id: ResendError("resend answered 503", retryable=True)})

    async def wait_seconds() -> float:
        async with system_tx(demo.pool) as conn:
            return float((await (await conn.execute(
                "select extract(epoch from next_try_at - now()) as s from app.inbound_emails where dataset_id = %s",
                (demo.dataset.id,))).fetchone())["s"])
    await demo.worker.tick(demo.dataset.id)
    assert 20 < await wait_seconds() <= 30
    await demo.worker.tick(demo.dataset.id)   # too early: left alone
    assert demo.worker.resend.fetched == [email_id] and (await delivery(demo))["processed_at"] is None
    async with system_tx(demo.pool) as conn:
        await conn.execute("update app.inbound_emails set next_try_at = now() - interval '1 second' where dataset_id = %s", (demo.dataset.id,))
    await demo.worker.tick(demo.dataset.id)
    assert demo.worker.resend.fetched == [email_id, email_id] and 100 < await wait_seconds() <= 120


async def test_handle_email_acts_once_per_email(demo):
    email_id = await deliver(demo)
    demo.worker.resend = FakeResend({email_id: received(email_id)})
    await demo.worker.tick(demo.dataset.id)
    delivery_row = await delivery(demo)
    again = received(email_id, subject="changed later")
    outcome = await handle_email(demo.pool, demo.worker.settings, demo.worker.keys, demo.worker.resend, demo.dataset.id, delivery_row["id"], again)
    assert outcome == "door_off" and (await rows(demo))[0]["subject"] == "New bank details"
