"""Spec 0049: the LLM document reader (`DIALBACK_READER=llm`).

Every test fakes the Anthropic API at the HTTP transport (`httpx2.MockTransport`), under the real
SDK, so the adapter's handling of errors and stop reasons and the SDK's own logging run as they would
live. No test calls the API: conftest.py removes the key and blocks the real transport.
"""
import anthropic
import httpx2
from dialback_ledger.flags import ReaderMode, reader_mode
from dialback_ledger.llm_reader import TIMEOUT_S, AnthropicLabelModel, LlmReader
from dialback_ledger.reader import StubReader, reader_for

KEY = {"ANTHROPIC_API_KEY": "sk-test-key"}


def llm_reader(handler) -> LlmReader:
    """The reader `DIALBACK_READER=llm` builds, with the API faked at the transport."""
    return reader_for(ReaderMode.LLM, env=KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


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
