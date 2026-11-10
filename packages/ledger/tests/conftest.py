"""Every ledger test runs without a model key and without a network for the SDK (spec 0049, decision 8).

So no test can call the Anthropic API by accident: the key and the reader flag are removed from the
environment, and the SDK's real HTTP transport refuses to send. Tests that exercise the LLM reader
fake the API with `httpx2.MockTransport`, under the real SDK.
"""
import contextlib

import httpx2
import pytest

_UNSET = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_BASE_URL", "DIALBACK_READER")


@contextlib.contextmanager
def no_network(monkeypatch):
    """Block the SDK's real HTTP transport. Each blocked call is recorded, and the test fails when the
    guard closes, even if the code under test caught the error (the SDK retries it and the reader
    turns it into ReaderFailed, so a test expecting a failure would otherwise pass; review G19)."""
    blocked: list[str] = []

    def refuse(self, request):
        blocked.append(request.url.host)
        raise RuntimeError(f"a test tried to reach {request.url.host} over the network")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", refuse)
    yield blocked
    if blocked:
        pytest.fail(f"a test reached for the network {len(blocked)} times ({', '.join(sorted(set(blocked)))})",
                    pytrace=False)


@pytest.fixture(autouse=True)
def _no_model_calls(monkeypatch):
    for name in _UNSET:
        monkeypatch.delenv(name, raising=False)
    with no_network(monkeypatch):
        yield
