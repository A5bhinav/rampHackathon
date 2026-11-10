"""Spec 0049: the LLM document reader (`DIALBACK_READER=llm`).

Every test fakes the Anthropic API at the HTTP transport (`httpx2.MockTransport`), under the real
SDK, so the adapter's handling of errors and stop reasons and the SDK's own logging run as they would
live. No test calls the API: conftest.py removes the key and blocks the real transport.
"""
import copy
import json

import anthropic
import httpx2
import pytest
from dialback_ledger.custody import custody_for
from dialback_ledger.flags import ReaderMode, reader_mode
from dialback_ledger.ingest import ingest, ingest_all
from dialback_ledger.llm_reader import (
    EFFORT,
    MAX_TOKENS,
    MODEL,
    SCHEMA,
    SYSTEM_PROMPT,
    TIMEOUT_S,
    USER_AFTER,
    USER_BEFORE,
    AnthropicLabelModel,
    LlmReader,
)
from dialback_ledger.reader import ReaderFailed, ReaderUnavailable, StubReader, reader_for
from jsonschema import Draft202012Validator
from test_extract import page, span

KEY = {"ANTHROPIC_API_KEY": "sk-test-key"}


def llm_reader(handler, calls: list | None = None) -> LlmReader:
    """The reader `DIALBACK_READER=llm` builds, with the API faked at the transport. Each request the
    SDK sends is appended to `calls`."""
    def transport(request):
        if calls is not None:
            calls.append(request)
        return handler(request)
    return reader_for(ReaderMode.LLM, env=KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(transport)))


# The fake API ------------------------------------------------------------------------------------

THINKING = {"type": "thinking", "thinking": "", "signature": "sig"}   # thinking can come before the text


def message(content: list, stop_reason: str = "end_turn") -> dict:
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": MODEL, "content": content,
            "stop_reason": stop_reason, "stop_sequence": None, "usage": {"input_tokens": 50, "output_tokens": 20}}


def answer(payload, stop_reason: str = "end_turn") -> httpx2.Response:
    """A 200 whose text block holds `payload` (JSON, or a raw string), after a thinking block."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return httpx2.Response(200, json=message([THINKING, {"type": "text", "text": text}], stop_reason),
                           headers={"request-id": "req_test_ok"})


def sent_text(request) -> str:
    """The document text the model was given, between the user turn's fixed delimiters."""
    turn = json.loads(request.content)["messages"][0]["content"]
    assert turn.startswith(USER_BEFORE) and turn.endswith(USER_AFTER)
    return turn[len(USER_BEFORE):len(turn) - len(USER_AFTER)]


def like_the_stub(request) -> httpx2.Response:
    """A fake model that answers what the keyword stub would, quoting whole lines."""
    return answer({"labels": StubReader().label(sent_text(request))})


def http_error(status: int):
    def handler(request):
        return httpx2.Response(status, json={"type": "error", "error": {"type": "api_error", "message": "boom"}},
                               headers={"request-id": f"req_test_{status}", "retry-after-ms": "1"})
    return handler


def timeout(request):
    raise httpx2.ReadTimeout("timed out", request=request)


def unreachable(request):
    raise httpx2.ConnectError("connection refused", request=request)


def refusal(request):
    body = message([], "refusal") | {"stop_details": {"type": "refusal", "category": None, "explanation": None}}
    return httpx2.Response(200, json=body, headers={"request-id": "req_test_refused"})


def cut_off(request):
    return answer('{"labels": [{"quote": "AR: (206) 555-0142", "ro', stop_reason="max_tokens")


def no_text(request):
    return httpx2.Response(200, json=message([THINKING]), headers={"request-id": "req_test_empty"})


def replying(payload):
    return lambda request: answer(payload)


LINE = "AR: (206) 555-0142"
OFF_SCHEMA = {
    "schema: a role off the list": {"labels": [{"quote": LINE, "role": "head office", "party": "vendor"}]},
    "schema: a party off the list": {"labels": [{"quote": LINE, "role": "ar", "party": "us"}]},
    "schema: an extra field": {"labels": [{"quote": LINE, "role": "ar", "party": "vendor", "e164": "+12065550142"}]},
    "schema: no party": {"labels": [{"quote": LINE, "role": "ar"}]},
    "schema: a quote that isn't a string": {"labels": [{"quote": 2065550142, "role": "ar", "party": "vendor"}]},
    "schema: labels not a list": {"labels": {"quote": LINE, "role": "ar", "party": "vendor"}},
    "schema: an extra top-level key": {"labels": [], "note": "all good"},
    "schema: no labels key": {"label": []},
}
# case: (handler, the failure's reason, how many requests the SDK sends: retried ones go 1 + 2 times)
RETRIED = 1 + anthropic.DEFAULT_MAX_RETRIES
FAILURES = {
    "api error (500, retried)": (http_error(500), "api_error", RETRIED),
    "api error (400)": (http_error(400), "api_error", 1),
    "api error (unreachable, retried)": (unreachable, "api_error", RETRIED),
    "timeout (retried)": (timeout, "timeout", RETRIED),
    "refusal": (refusal, "refusal", 1),
    "max_tokens": (cut_off, "max_tokens", 1),
    "schema: not JSON": (replying("AR is the vendor's line"), "schema", 1),
    "schema: no text block": (no_text, "schema", 1),
    **{case: (replying(payload), "schema", 1) for case, payload in OFF_SCHEMA.items()},
}
GOOD = page(span("Main: 206-555-0117")) | {"id": "doc-good"}
BAD = page(span("Acme Supply Co."), span(LINE)) | {"id": "doc-bad"}


# Mode and wiring ---------------------------------------------------------------------------------

def test_llm_mode_builds_the_reader():
    env = KEY | {"DIALBACK_READER": "llm"}
    reader = reader_for(reader_mode(env), env=env)
    assert isinstance(reader, LlmReader)
    assert isinstance(reader.model, AnthropicLabelModel)
    client = reader.model.client
    assert isinstance(client, anthropic.Anthropic)
    assert client.api_key == "sk-test-key"
    assert client.timeout == TIMEOUT_S == 30.0
    assert client.max_retries == anthropic.DEFAULT_MAX_RETRIES   # the SDK's default retries


def test_default_stays_stub(monkeypatch):
    assert reader_mode({}) is ReaderMode.STUB
    assert isinstance(reader_for(reader_mode({})), StubReader)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")       # a key alone never switches the reader
    assert reader_mode() is ReaderMode.STUB
    assert isinstance(reader_for(reader_mode()), StubReader)


def test_the_request_is_pinned():
    calls = []
    ingest_all([BAD], extractor_version="test", reader=llm_reader(like_the_stub, calls))
    (request,) = calls
    body = json.loads(request.content)
    # No tools, no thinking setting (Haiku 5.5 thinks adaptively), no sampling parameters.
    assert set(body) == {"model", "max_tokens", "system", "messages", "output_config"}
    assert (body["model"], body["max_tokens"], MODEL, MAX_TOKENS) == ("claude-haiku-5-5", 8192, "claude-haiku-5-5", 8192)
    assert body["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert EFFORT == "low"
    assert body["system"] == SYSTEM_PROMPT
    assert body["messages"] == [{"role": "user", "content": USER_BEFORE + "Acme Supply Co.\n" + LINE + USER_AFTER}]
    # The key from the environment is the one sent (SDK 2.x sends it as a bearer token).
    assert request.headers.get("authorization") == "Bearer sk-test-key" or request.headers.get("x-api-key") == "sk-test-key"
    assert request.extensions["timeout"] == {"connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0}


def test_the_schema_is_the_specs():
    label = {"type": "object", "additionalProperties": False, "required": ["quote", "role", "party"],
             "properties": {"quote": {"type": "string"},
                            "role": {"type": "string", "enum": ["main", "ar", "billing", "fax", "unknown"]},
                            "party": {"type": "string", "enum": ["vendor", "customer", "bank", "unknown"]}}}
    assert SCHEMA == {"type": "object", "additionalProperties": False, "required": ["labels"],
                      "properties": {"labels": {"type": "array", "items": label}}}


# Fail closed -------------------------------------------------------------------------------------

def test_a_good_reply_is_grounded_and_stored():
    doc = page(span("Accounts receivable: (206) 555-0142"), span("Our bank: 206-555-0186"), span("Fax 206-555-0199"))
    reader = llm_reader(replying({"labels": [
        {"quote": "Accounts receivable: (206) 555-0142", "role": "ar", "party": "vendor"},
        {"quote": "206-555-0186", "role": "unknown", "party": "bank"},
        {"quote": "Call (206) 555-0177", "role": "main", "party": "vendor"},   # not in the document: dropped
    ]}))
    result = ingest_all([doc], extractor_version="test", reader=reader)
    got = {o["value_normalized"]: (o["role"], o["party"], o["quote"]) for o in result.observations}
    assert got == {"+12065550142": ("ar", "vendor", "Accounts receivable: (206) 555-0142"),
                   "+12065550186": ("unknown", "bank", "206-555-0186"),
                   "+12065550199": ("unknown", "unknown", "206-555-0199")}       # skipped by the model: unknown
    assert [why for _, _, why in result.dropped] == ["the quote isn't in the visible text at a candidate"]


@pytest.mark.parametrize("case", FAILURES)
def test_each_failure_is_not_stored(case):
    handler, reason, requests = FAILURES[case]
    calls = []
    stored = ingest_all([GOOD], extractor_version="test", reader=StubReader())
    before = copy.deepcopy(stored)
    with pytest.raises(ReaderFailed) as failed:
        ingest(BAD, custody_for(BAD), extractor_version="test", reader=llm_reader(handler, calls), result=stored)
    assert (failed.value.reason, failed.value.document_id) == (reason, "doc-bad")
    assert stored == before                       # no document row and no observations for the failed document
    assert len(calls) == requests
    if case in OFF_SCHEMA:                        # it really is off the schema, not just off this reader's check
        assert not Draft202012Validator(SCHEMA).is_valid(OFF_SCHEMA[case])


def test_never_falls_back_to_the_stub(monkeypatch):
    seen = []
    monkeypatch.setattr(StubReader, "label", lambda self, text: seen.append(text) or [])
    for handler, _, _ in FAILURES.values():
        with pytest.raises(ReaderFailed):
            ingest_all([BAD], extractor_version="test", reader=llm_reader(handler))
    monkeypatch.setenv("DIALBACK_READER", "llm")       # llm mode without its key: refused, not the stub
    with pytest.raises(ReaderUnavailable):
        ingest_all([BAD], extractor_version="test", reader=reader_for(reader_mode()))
    assert seen == []
