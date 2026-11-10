"""The flagging bar on the labeled emails (spec 0086). The rule alone did not meet the held-out bar, and
Sami's decision 11 made the email door's flagger the rule OR a quarantined model, judged on a fresh
held-out set. Here: the rule still meets the bar on the tuning half it was built on, and keeps a floor
on the old held-out half, which is now development data (its hash is still checked)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dialback_core.email_door.rules import flag

FIXTURES = Path(__file__).parent / "fixtures"
MAX_ORDINARY_FLAGGED = 3  # an absolute count, set before any tuning; never loosen it
DEVELOPMENT_FLOOR = 30    # change requests the rule alone flagged in the development half when it stopped


def load(half: str) -> dict[str, dict]:
    return {p.name: json.loads(p.read_text()) for p in sorted((FIXTURES / half).glob("*.json"))}


def score(emails: dict[str, dict], change_requests: int, ordinary: int) -> tuple[list[str], list[str], str]:
    labels = [e["label"] for e in emails.values()]
    assert (labels.count("change_request"), labels.count("ordinary"), len(labels)) == (change_requests, ordinary, change_requests + ordinary)
    flagged = {name: flag(e["subject"], e["text"]).flagged for name, e in emails.items()}
    missed = sorted(n for n, e in emails.items() if e["label"] == "change_request" and not flagged[n])
    false_flags = sorted(n for n, e in emails.items() if e["label"] == "ordinary" and flagged[n])
    summary = (f"change requests flagged {change_requests - len(missed)}/{change_requests} (missed: {missed}); "
               f"ordinary flagged {len(false_flags)}/{ordinary} (flagged: {false_flags})")
    return missed, false_flags, summary


def test_tuning_half_meets_the_bar():
    missed, false_flags, summary = score(load("tuning"), change_requests=30, ordinary=62)
    assert not missed and len(false_flags) <= MAX_ORDINARY_FLAGGED, summary


def test_rule_alone_keeps_its_floor_on_the_development_half():
    files = sorted((FIXTURES / "heldout").glob("*.json"))
    sealed = (FIXTURES / "heldout.sha256").read_text().split()[0]
    assert hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest() == sealed
    missed, false_flags, summary = score(load("heldout"), change_requests=35, ordinary=65)
    assert 35 - len(missed) >= DEVELOPMENT_FLOOR and len(false_flags) <= MAX_ORDINARY_FLAGGED, summary
