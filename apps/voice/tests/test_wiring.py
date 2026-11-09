"""wire.build_twilio_deps turns a validated live config into the TwilioDeps the routes need, and
returns None when there is no validated config, so create_app mounts no Twilio routes and the
service runs the text line (spec 0014, decision 5; R7). It assembles the real TwilioOriginator and
the CoreClient report seam; lookup_call is threaded in (its psycopg query lands with the ledger,
Q40/Q50) and agent_for defaults to the ScriptedAgent placeholder the post-lab agent spec replaces."""
from __future__ import annotations

import httpx
from dialback_voice.agent import ScriptedAgent
from dialback_voice.claude_agent import ClaudeAgent
from dialback_voice.settings import Settings, TwilioConfig
from dialback_voice.twilio.origination import TwilioOriginator
from dialback_voice.wire import build_twilio_deps

CFG = TwilioConfig(account_sid="ACtest00000000000000000000000000", auth_token=b"auth",
                   from_number="+14155550100", call_token_key=b"key", core_url="https://core.internal",
                   internal_token=b"itok", public_base_url="https://voice.example",
                   ws_base_url="wss://voice.example")


def test_no_validated_config_builds_no_deps():
    # Live requested but incomplete (twilio is None), and plain text mode: both get no live deps,
    # so create_app(twilio=None) mounts no Twilio routes.
    http = httpx.AsyncClient()
    assert build_twilio_deps(Settings(voice_mode="live", twilio=None),
                             lookup_call=lambda c: None, http=http, clock=lambda: None) is None
    assert build_twilio_deps(Settings(voice_mode="text"),
                             lookup_call=lambda c: None, http=http, clock=lambda: None) is None


def test_complete_config_builds_live_deps():
    http = httpx.AsyncClient()
    sentinel = lambda c: None  # stands in for the (deferred) psycopg lookup
    clk = lambda: None
    deps = build_twilio_deps(Settings(voice_mode="live", twilio=CFG),
                             lookup_call=sentinel, http=http, clock=clk)
    assert deps is not None
    assert deps.from_number == "+14155550100"
    assert deps.public_base_url == "https://voice.example"
    assert deps.ws_base_url == "wss://voice.example"
    assert deps.call_token_key == b"key"
    assert deps.twilio_auth_token == b"auth"
    assert isinstance(deps.originator, TwilioOriginator)     # the real REST client is wired
    assert deps.lookup_call is sentinel                      # the lookup seam is threaded through
    assert callable(deps.report_ended)                       # the CoreClient report seam is wired
    assert isinstance(deps.agent_for(), ScriptedAgent)       # placeholder when no agent key is set
    assert deps.clock is clk


def test_the_anthropic_key_swaps_in_the_claude_agent():
    # With the agent key present, agent_for builds the live ClaudeAgent over the injected model;
    # without it, it stays the ScriptedAgent placeholder (test_complete_config_builds_live_deps).
    http = httpx.AsyncClient()
    fake_model = object()
    deps = build_twilio_deps(Settings(voice_mode="live", twilio=CFG, anthropic_api_key=b"sk-x"),
                             lookup_call=lambda c: None, http=http, clock=lambda: None,
                             model_for=lambda key: fake_model)
    agent = deps.agent_for()
    assert isinstance(agent, ClaudeAgent)
    assert agent.model is fake_model
