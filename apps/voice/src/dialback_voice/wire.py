"""Assemble the live TwilioDeps from validated settings (spec 0014, decision 5).

`build_twilio_deps` returns the deps only when settings carries a validated `TwilioConfig`;
otherwise it returns None, so `create_app(twilio=None)` mounts no Twilio routes and the service runs
the text line (R7). It wires the real `TwilioOriginator` and the `CoreClient` report seam.
`lookup_call` is threaded in: the entrypoint passes the Postgres one (`pg_store.make_lookup`,
spec 0029). `agent_for` is the live `ClaudeAgent` when `ANTHROPIC_API_KEY` is set (spec 0015), else the
`ScriptedAgent` placeholder; the key gates the agent, not the Twilio routes."""
from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import UUID

import httpx

from .agent import Agent, ScriptedAgent
from .app import CallDial, TwilioDeps
from .claude_agent import AnthropicVoiceModel, ClaudeAgent, VoiceModel
from .report import CoreClient
from .settings import Settings
from .transport import Clock
from .twilio.origination import TwilioOriginator

log = logging.getLogger(__name__)


def _default_model(key: bytes) -> VoiceModel:
    """A sync httpx client for the model turns, with a short deadline so a slow model fails closed."""
    return AnthropicVoiceModel(httpx.Client(timeout=8.0), key)


def build_agent_for(settings: Settings, *,
                    model_for: Callable[[bytes], VoiceModel] = _default_model) -> Callable[[], Agent]:
    """The live `ClaudeAgent` factory when the agent key is set, else the `ScriptedAgent` placeholder
    (spec 0015, decision 5). The key gates the agent, not the Twilio routes: a live deployment with a
    validated Twilio config but no key still dials, running the scripted brain, and logs a warning."""
    key = settings.anthropic_api_key
    if key is None:
        return ScriptedAgent
    model = model_for(key)
    return lambda: ClaudeAgent(model=model)


def build_twilio_deps(settings: Settings, *, lookup_call: Callable[[UUID], CallDial | None],
                      http: httpx.AsyncClient, clock: Clock,
                      model_for: Callable[[bytes], VoiceModel] = _default_model) -> TwilioDeps | None:
    """The live deps, or None when there is no validated live config (text line, no Twilio routes)."""
    cfg = settings.twilio
    if cfg is None:
        return None
    agent_for = build_agent_for(settings, model_for=model_for)
    if agent_for is ScriptedAgent:  # loud: a live call would run the scripted brain without the agent key
        log.warning("DIALBACK_VOICE=live without ANTHROPIC_API_KEY: wired with the ScriptedAgent "
                    "placeholder. Set the key for the live Claude agent before dialing (spec 0015).")
    report = CoreClient(cfg.core_url, cfg.internal_token, http).report_call_ended
    originator = TwilioOriginator(http, account_sid=cfg.account_sid, auth_token=cfg.auth_token)
    return TwilioDeps(call_token_key=cfg.call_token_key, twilio_auth_token=cfg.auth_token,
                      ws_base_url=cfg.ws_base_url, public_base_url=cfg.public_base_url,
                      from_number=cfg.from_number, originator=originator,
                      lookup_call=lookup_call, report_ended=report, agent_for=agent_for, clock=clock)
