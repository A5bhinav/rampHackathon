"""The fixed reply and its suppression rules (spec 0088; design section 8). Pure decisions and wording."""
from __future__ import annotations

from dataclasses import replace

import pytest
from dialback_core.email_door.reply import (
    REPLY_TEXT,
    ReplyDecision,
    compose_reply,
    decide_reply,
    is_authenticated,
    reply_address,
)
from flows import received

INBOX = "inbox@dialback.example"
URL = "https://dialback.example/bank-changes"


REPLY_FROM = "Dialback <bank-changes@dialback.example>"


def decide(email=None, *, configured=True, inbox=INBOX, per_sender=0, per_hour=0, **changes) -> ReplyDecision:
    return decide_reply(email or received("e1", **changes), inbox_address=inbox, configured=configured,
                        sent_to_sender_24h=per_sender, sent_last_hour=per_hour, reply_from=REPLY_FROM)


def test_a_clean_authenticated_email_is_sent():
    assert decide() == ReplyDecision(True, "sent")


def test_not_configured_is_checked_first():
    assert decide(configured=False, authentication=None, headers={"auto-submitted": "auto-replied"}) == ReplyDecision(False, "not_configured")


@pytest.mark.parametrize("headers", [
    {"auto-submitted": "auto-replied"}, {"auto-submitted": "auto-generated"}, {"auto-submitted": ""},
    {"precedence": "bulk"}, {"precedence": "List"}, {"precedence": "junk"},
    {"list-id": "<news.vendor.example>"}, {"list-id": ""}])
def test_automatic_and_list_mail_is_suppressed(headers):
    assert decide(headers=headers) == ReplyDecision(False, "suppressed")


def test_auto_submitted_no_and_other_precedence_are_fine():
    assert decide(headers={"auto-submitted": "no", "precedence": "first-class"}).send is True


@pytest.mark.parametrize("sender", [
    f"Us <{INBOX}>", "INBOX@dialback.example", "noreply@vendor.example", "No Reply <No-Reply@vendor.example>",
    "MAILER-DAEMON@vendor.example", "mailer-daemon@mx.vendor.example", "", "not an address", "a@b.example\r\nBcc: x@y.example"])
def test_inbox_noreply_and_unparseable_senders_are_suppressed(sender):
    assert decide(sender=sender) == ReplyDecision(False, "suppressed")


@pytest.mark.parametrize("sender", ["x@dialback.example", "Anyone <hr@DIALBACK.example>", "bank-changes@dialback.example"])
def test_the_inbox_domain_and_our_own_from_address_are_never_replied_to(sender):
    assert decide(sender=sender) == ReplyDecision(False, "suppressed")


def test_the_raw_from_with_two_mailboxes_is_suppressed():
    """Production shape: Resend's `sender` is the bare first address, the raw header holds two mailboxes."""
    email = received("e1", sender="ar@vendor.example", headers={"from": "AR <ar@vendor.example>, Victim <victim@elsewhere.example>"})
    assert decide(email) == ReplyDecision(False, "suppressed")


def test_a_raw_from_naming_another_address_than_sender_is_suppressed():
    email = received("e1", sender="ar@vendor.example", headers={"from": "Dana <someone.else@vendor.example>"})
    assert decide(email) == ReplyDecision(False, "suppressed")


def test_a_missing_raw_from_is_suppressed():
    email = received("e1", sender="ar@vendor.example")
    assert decide(replace(email, headers={})) == ReplyDecision(False, "suppressed")


def test_a_matching_bare_sender_and_raw_from_is_sent():
    email = received("e1", sender="ar@vendor.example", headers={"from": "Dana <AR@Vendor.example>"})
    assert decide(email) == ReplyDecision(True, "sent")
    assert reply_address(email) == "ar@vendor.example"


def test_dkim_and_dmarc_are_compared_trimmed_and_lowercase():
    assert decide(authentication={"spf": "fail", "dkim": " PASS ", "dmarc": "fail"}).send is True
    assert is_authenticated({"dmarc": "gray"}) is False and is_authenticated(None) is False


def test_suppression_beats_unauthenticated():
    assert decide(headers={"precedence": "bulk"}, authentication=None) == ReplyDecision(False, "suppressed")


@pytest.mark.parametrize("auth", [None, {"spf": "pass", "dkim": "fail", "dmarc": "fail"},
                                  {"spf": "pass", "dkim": "gray", "dmarc": "unknown"}])
def test_neither_dmarc_nor_dkim_passing_is_unauthenticated(auth):
    assert decide(authentication=auth) == ReplyDecision(False, "unauthenticated")


@pytest.mark.parametrize("auth", [{"spf": "fail", "dkim": "pass", "dmarc": "fail"}, {"spf": "fail", "dkim": "fail", "dmarc": "pass"}])
def test_a_dkim_or_dmarc_pass_is_authenticated(auth):
    assert decide(authentication=auth).send is True


def test_one_reply_per_sender_per_day():
    assert decide(per_sender=1) == ReplyDecision(False, "rate_limited")


def test_twenty_replies_an_hour_is_the_cap():
    assert decide(per_hour=19).send is True
    assert decide(per_hour=20) == ReplyDecision(False, "rate_limited")


def test_unauthenticated_is_checked_before_the_rate():
    assert decide(authentication=None, per_sender=3) == ReplyDecision(False, "unauthenticated")


APPROVED = ("We never change payment details because of an email. Every change is confirmed using contact details we "
            "already have for you, not ones sent in the request. To change your payment details, contact your account "
            "contact at Cobalt Freight using details you already have for them. We will never ask you to confirm by "
            "replying to an email or calling a number someone sent you.")


def test_reply_text_is_the_approved_wording():
    _subject, text = compose_reply("Cobalt Freight", URL)
    assert text == APPROVED + "\n\n" + URL
    assert text.splitlines()[-1] == URL
    assert "{" not in text
    assert REPLY_TEXT.format(company="Cobalt Freight", url=URL) == text


def test_the_subject_is_fixed_and_never_the_senders():
    assert compose_reply("Co", URL)[0] == "Re: your message about payment details"


@pytest.mark.anyio
async def test_the_client_sends_an_idempotency_key_and_threads_the_reply():
    import json

    import httpx
    from dialback_core.email_door.resend import ResendClient
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers, json.loads(request.content)))
        return httpx.Response(200, json={"id": "out_1"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sent = await ResendClient("re_key", http).send(sender="a@dialback.example", to="b@vendor.example", subject="Re: x",
                                                        text="t", in_reply_to="<m@b>", idempotency_key="reply-em_1")
    [(method, path, headers, body)] = seen
    assert (sent, method, path) == ("out_1", "POST", "/emails")
    assert headers["idempotency-key"] == "reply-em_1" and headers["authorization"] == "Bearer re_key"
    assert body["to"] == ["b@vendor.example"] and body["headers"]["In-Reply-To"] == "<m@b>"
    assert body["headers"]["Auto-Submitted"] == "auto-replied"
