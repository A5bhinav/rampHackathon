"""Every ledger test runs without a model key and without a network for the SDK (spec 0049, decision 8).

So no test can call the Anthropic API by accident: the key and the reader flag are removed from the
environment, and the SDK's real HTTP transport refuses to send. Tests that exercise the LLM reader
fake the API with `httpx2.MockTransport`, under the real SDK.
"""
import httpx2
import pytest

_UNSET = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_BASE_URL", "DIALBACK_READER")


@pytest.fixture(autouse=True)
def _no_model_calls(monkeypatch):
    for name in _UNSET:
        monkeypatch.delenv(name, raising=False)

    def refuse(self, request):
        raise RuntimeError(f"a test tried to reach {request.url.host} over the network")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", refuse)
