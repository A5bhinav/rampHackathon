"""Spec 0049: the LLM document reader (`DIALBACK_READER=llm`).

Every test fakes the Anthropic API at the HTTP transport (`httpx2.MockTransport`), under the real
SDK, so the adapter's handling of errors and stop reasons and the SDK's own logging run as they would
live. No test calls the API: conftest.py removes the key and blocks the real transport.
"""
import copy
import json
import logging
import re

import anthropic
import httpx2
import pytest
from conftest import no_network
from dialback_ledger import demo
from dialback_ledger.custody import custody_for
from dialback_ledger.flags import ReaderMode, reader_mode
from dialback_ledger.ingest import ingest, ingest_all
from dialback_ledger.llm_reader import (
    API_BASE_URL,
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
    llm_version,
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


def raw_body(content: bytes, content_type: str):
    return lambda request: httpx2.Response(200, content=content,
                                           headers={"content-type": content_type, "request-id": "req_test_raw"})


def full(content) -> httpx2.Response:
    return httpx2.Response(200, json=message(content), headers={"request-id": "req_test_raw"})


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
    # Malformed replies (review G12): each used to escape as a raw exception.
    "bad reply: an invalid JSON body": (raw_body(b"not json", "application/json"), "api_error", 1),
    "bad reply: a text/plain body": (raw_body(b"hello", "text/plain"), "api_error", 1),
    "bad reply: content null": (lambda request: full(None), "schema", 1),
    "bad reply: a text block with no text": (lambda request: full([{"type": "text"}]), "schema", 1),
    "bad reply: deeply nested JSON": (replying("[" * 200_000 + "]" * 200_000), "schema", 1),
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


def test_the_network_guard_fails_a_test_that_reached_for_the_network():
    # Review G19: the guard's error was retried by the SDK and became ReaderFailed, so a test that
    # expected a failure passed even with a real client. The guard records the call and fails.
    real = LlmReader(AnthropicLabelModel(anthropic.Anthropic(api_key="sk-test-key", base_url=API_BASE_URL,
                                                             max_retries=0)))       # no fake transport
    with pytest.MonkeyPatch.context() as mp:
        with (pytest.raises(pytest.fail.Exception, match="reached for the network"), no_network(mp) as blocked,
              pytest.raises(ReaderFailed)):
            ingest_all([BAD], extractor_version="test", reader=real)
        assert blocked == ["api.anthropic.com"]


# The demo (its database tests are in test_demo.py) -------------------------------------------------

@pytest.mark.parametrize("argv", [["demo", "load"], ["demo", "request", "--vendor", "acme"]])
def test_missing_key_refuses_to_start(monkeypatch, capsys, argv):
    monkeypatch.setenv("DIALBACK_READER", "llm")
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:9/never")   # refused before it's reached
    assert demo.main(argv) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    with pytest.raises(ReaderUnavailable):
        reader_for(ReaderMode.LLM, env={"ANTHROPIC_API_KEY": "   "})                # a blank key is no key


# What reaches the model ---------------------------------------------------------------------------

def test_long_digit_runs_never_reach_the_model():
    calls = []
    doc = page(span("Remit to account 000123456789, routing 021000021"),
               span("Invoice 12345678-A, PO 9876543, ref 123456"),
               span("Call 2065550117 or (206) 555-0142"),
               span("Old line 2065550124", color="#ffffff"))                 # hidden: never sent at all
    ingest_all([doc], extractor_version="test", reader=llm_reader(like_the_stub, calls))
    (request,) = calls
    assert re.findall(r"\d{7,}", request.content.decode()) == ["2065550117"]   # the one left is a phone candidate
    assert sent_text(request) == ("Remit to account [digits removed], routing [digits removed]\n"
                                  "Invoice [digits removed]-A, PO [digits removed], ref 123456\n"
                                  "Call 2065550117 or (206) 555-0142")


def test_long_digit_runs_in_an_extension_never_reach_the_model():
    # Review G15: a candidate's span covers its extension, so a long run there was kept.
    calls = []
    doc = page(span("Call (206) 555-0142 ext. 000123456789"), span("Phone 206-555-0117 x123456789"),
               span("Main 2065550124"))
    ingest_all([doc], extractor_version="test", reader=llm_reader(like_the_stub, calls))
    (request,) = calls
    assert re.findall(r"\d{7,}", request.content.decode()) == ["2065550124"]    # the phone's own digits stay
    assert sent_text(request) == ("Call (206) 555-0142 ext. [digits removed]\n"
                                  "Phone 206-555-0117 x[digits removed]\n"
                                  "Main 2065550124")


def test_the_api_base_url_is_pinned(monkeypatch):
    # Review G16: a host's ANTHROPIC_BASE_URL could choose where document text goes.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://attacker.invalid")
    calls = []
    reader = llm_reader(like_the_stub, calls)
    ingest_all([BAD], extractor_version="test", reader=reader)
    (request,) = calls
    assert (request.url.scheme, request.url.host) == ("https", "api.anthropic.com")
    assert str(reader.model.client.base_url).rstrip("/") == API_BASE_URL == "https://api.anthropic.com"


def test_a_quote_with_a_placeholder_is_dropped():
    doc = page(span("Acct 123456789 AR (206) 555-0142"), span("Ref 7654321, main 206-555-0117"))

    def model(request):
        first = sent_text(request).split("\n")[0]          # the line as the model saw it, placeholder and all
        return answer({"labels": [{"quote": first, "role": "ar", "party": "vendor"},
                                  {"quote": "main 206-555-0117", "role": "main", "party": "vendor"}]})
    result = ingest_all([doc], extractor_version="test", reader=llm_reader(model))
    got = {o["value_normalized"]: (o["role"], o["party"]) for o in result.observations}
    assert got == {"+12065550142": ("unknown", "unknown"), "+12065550117": ("main", "vendor")}
    assert [(raw["quote"], why) for _, raw, why in result.dropped] == [
        ("Acct [digits removed] AR (206) 555-0142", "the quote isn't in the visible text at a candidate")]


def test_a_document_without_a_visible_number_makes_no_call():
    calls = []
    doc = page(span("Thanks for your business."), span("Acct 123456789"), span("206-555-0124", color="#ffffff"))
    result = ingest_all([doc], extractor_version="test", reader=llm_reader(like_the_stub, calls))
    assert calls == []                                      # nothing to label, so the text never leaves
    assert [(o["value_normalized"], o["visibility"]) for o in result.observations] == [("+12065550124", "white")]


TEAM = "+12025550100"   # fictional here; on the day, the operator types the team phone


def test_team_numbers_never_reach_the_model():
    seed = demo.load_seed()["vendors"]["acme"]
    fictional = seed["phones"]["ar"]
    calls = []
    reader = llm_reader(like_the_stub, calls)
    result = demo.labeled(seed["documents"], reader, (fictional, TEAM))
    sent = "\n".join(r.content.decode() for r in calls)
    assert calls and any(f in sent for f in demo._formats(fictional))   # the reader saw the fictional line
    for f in [*demo._formats(TEAM), TEAM, TEAM[2:]]:
        assert f not in sent, f
    stored = {o["value_normalized"] for o in result.observations}
    assert TEAM in stored and fictional not in stored                    # the team phone is in the stored rows
    assert not any(f in (o["quote"] or "") for o in result.observations for f in demo._formats(fictional))
    assert {o["extractor_version"] for o in result.observations} == {reader.version}


def test_swapping_the_rows_matches_swapping_the_text():
    # Before spec 0049 the demo swapped the team phone into the documents' text before ingesting them.
    # Swapping it into the rows afterward must store exactly the same rows.
    for ref, v in demo.load_seed()["vendors"].items():
        swap = (v["phones"]["ar"], TEAM)
        before = ingest_all([demo._with_phone(d, swap) for d in v["documents"]], extractor_version="stub",
                            reader=StubReader())
        after = demo.labeled(v["documents"], StubReader(), swap)
        assert (after.documents, after.observations) == (before.documents, before.observations), ref
        assert any(o["value_normalized"] == TEAM for o in after.observations), ref


# Logs --------------------------------------------------------------------------------------------

SECRET_TEXT = "Wire questions to Dana Whitfield"     # document text: must never reach a log line
SECRET_OUTPUT = "the model's private words"           # model output: likewise
SDK_LOGGERS = ("anthropic", "httpx2", "httpcore2")


def test_no_text_in_logs(caplog):
    doc = page(span(SECRET_TEXT), span(LINE)) | {"id": "doc-secret"}
    handlers = [handler for handler, _, _ in FAILURES.values()] + [
        replying(SECRET_OUTPUT),                                                          # not JSON
        replying({"labels": [{"quote": LINE, "role": "ar", "party": "vendor", "why": SECRET_OUTPUT}]}),
        replying({"labels": [{"quote": SECRET_OUTPUT, "role": "ar", "party": "vendor"}]}),  # dropped by grounding
        like_the_stub]                                                                    # a good reply
    saved = {name: logging.getLogger(name).level for name in SDK_LOGGERS}
    errors = []
    try:
        for name in SDK_LOGGERS:          # what ANTHROPIC_LOG=debug leaves behind
            logging.getLogger(name).setLevel(logging.DEBUG)
        caplog.set_level(logging.DEBUG)
        for handler in handlers:
            try:
                ingest_all([doc], extractor_version="test", reader=llm_reader(handler))
            except ReaderFailed as exc:
                errors.append(exc)
    finally:
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)
    logged = "\n".join(logging.Formatter().format(r) for r in caplog.records)   # with any traceback
    raised = "\n".join(f"{e!r} {e.__cause__!r} {e.__context__!r}" for e in errors)
    assert len(errors) == len(FAILURES) + 2
    for secret in (SECRET_TEXT, "Dana", SECRET_OUTPUT, "555-0142", "5550142"):
        assert secret not in logged, secret
        assert secret not in raised, secret
    # What it does log: counts and the API's request id.
    ours = [r.getMessage() for r in caplog.records if r.name == "dialback_ledger.llm_reader"]
    assert any("req_test_ok" in m for m in ours) and any("req_test_500" in m for m in ours)


# Version -----------------------------------------------------------------------------------------

def test_extractor_version_tracks_model_prompt_and_schema():
    v = llm_version()
    assert re.fullmatch(r"llm-[0-9a-f]{12}", v) and llm_version() == v
    assert llm_version(model="claude-sonnet-5-5") != v
    assert llm_version(system=SYSTEM_PROMPT + " ") != v
    assert llm_version(user=(USER_BEFORE, USER_AFTER + " ")) != v
    schema = copy.deepcopy(SCHEMA)
    schema["properties"]["labels"]["items"]["properties"]["role"]["enum"].append("sales")
    assert llm_version(schema=schema) != v
    assert llm_version(effort="medium") != v                      # review G17
    assert llm_version(max_tokens=MAX_TOKENS * 2) != v
    assert reader_for(ReaderMode.LLM, env=KEY).version == v       # the reader names its configuration
    assert StubReader().version == "stub"
