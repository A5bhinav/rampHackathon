"""Assemble the live TwilioDeps from validated settings (spec 0014, decision 5).

`build_twilio_deps` returns the deps only when settings carries a validated `TwilioConfig`;
otherwise it returns None, so `create_app(twilio=None)` mounts no Twilio routes and the service runs
the text line (R7). It wires the real `TwilioOriginator` and the `CoreClient` report seam.
`lookup_call` is threaded in: its psycopg query joins `voice.calls`, `app.cases` and the ledger
pick, whose shapes are not final (Q40/Q50), so the entrypoint passes the real one once the ledger
lands. `agent_for` defaults to the `ScriptedAgent` placeholder; the post-lab agent spec swaps in the
live model (Q41)."""
from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import UUID

import httpx

from .agent import Agent, ScriptedAgent
from .app import CallDial, TwilioDeps
from .report import CoreClient
from .settings import Settings
from .transport import Clock
from .twilio.origination import TwilioOriginator

log = logging.getLogger(__name__)


def build_twilio_deps(settings: Settings, *, lookup_call: Callable[[UUID], CallDial | None],
                      http: httpx.AsyncClient, clock: Clock,
                      agent_for: Callable[[], Agent] = ScriptedAgent) -> TwilioDeps | None:
    """The live deps, or None when there is no validated live config (text line, no Twilio routes)."""
    cfg = settings.twilio
    if cfg is None:
        return None
    if agent_for is ScriptedAgent:  # loud: a live call would run the scripted brain until the agent spec
        log.warning("DIALBACK_VOICE=live is wired with the ScriptedAgent placeholder; swap in the "
                    "live model before dialing a real number (agent spec, Q41).")
    report = CoreClient(cfg.core_url, cfg.internal_token, http).report_call_ended
    originator = TwilioOriginator(http, account_sid=cfg.account_sid, auth_token=cfg.auth_token)
    return TwilioDeps(call_token_key=cfg.call_token_key, twilio_auth_token=cfg.auth_token,
                      ws_base_url=cfg.ws_base_url, public_base_url=cfg.public_base_url,
                      from_number=cfg.from_number, originator=originator,
                      lookup_call=lookup_call, report_ended=report, agent_for=agent_for, clock=clock)
