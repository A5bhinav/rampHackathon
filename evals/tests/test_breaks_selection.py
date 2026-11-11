"""Spec 0048, C2 (FINDINGS B5): the generator reaches the boundaries B5 found it missing.

Each test loads a private copy of production's `select.py` with one deliberate change (select.py itself
is never edited) and draws 2000 worlds at seed 0 with pinned draws. The change is caught when, on some
world, the changed rule's output differs from production's on the same world: spec 0048's
differential meaning of "caught" (the meaning spec 0046 uses for a break being found). The first
seven are B5's changes; the last three tie the new knobs (an upper-case "FAX" role, a late sighting on
an on-time document, ages exactly at `min_age_days`) to a change each one exists to catch.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from functools import lru_cache
from pathlib import Path

import pytest
from dialback_evals.draws import sample
from dialback_evals.worldgen import World, worlds

EXAMPLES, SEED = 2000, 0
production = importlib.import_module("dialback_ledger.select")   # the module (the package re-exports the function)

# (name, the exact text in select.py, what the changed copy says instead)
B5 = [
    ("context-ignores-request-id",
     "d.thread_id == request.thread_id) or d.id == request.document_id\n",
     "d.thread_id == request.thread_id)\n"),
    ("cutoff-ignores-requested-cutoff",
     "starts = [requested_cutoff, *(docs[i].received_at for i in look_ids)] + ([request.received_at] if request else [])",
     "starts = [*(docs[i].received_at for i in look_ids)] + ([request.received_at] if request else [requested_cutoff])"),
    ("ranking-reads-context-roles",
     'ranking_roles = {s.role for s, _ in clean}',
     'ranking_roles = {s.role for s, _, _ in visible}'),
    ("r2-tie-goes-outside",
     "first_ctx <= min(outside)",
     "first_ctx < min(outside)"),
    ("same-time-near-misses-do-not-flag",
     'fb["seen"] > mine',
     'fb["seen"] >= mine'),
    ("established-line-strict",
     'cutoff - fa["seen"] >= established and cutoff - fb["seen"] >= established',
     'cutoff - fa["seen"] > established and cutoff - fb["seen"] > established'),
    ("subject-window-strict",
     "cutoff - d.received_at <= window",
     "cutoff - d.received_at < window"),
]
KNOBS = [
    ("fax-case-sensitive",                       # spec 0046's D03: never found, the generator drew no "FAX"
     'str(s.role).strip().lower() == "fax"',
     'str(s.role).strip() == "fax"'),
    ("reads-late-sightings-on-on-time-documents",
     "s for s in sightings if s.recorded_at < case_opened_at and s.document_id in docs",
     "s for s in sightings if s.document_id in docs"),
    ("min-age-strict",
     "cutoff - first_clean < dt.timedelta(days=settings.min_age_days)",
     "cutoff - first_clean <= dt.timedelta(days=settings.min_age_days)"),
]


def changed_rule(name: str, old: str, new: str):
    """Production's select.py with one change, loaded as a private module inside dialback_ledger (so its
    relative imports resolve). Refuses a change whose text no longer appears exactly once."""
    source = Path(production.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1, f"{name}: the text to change appears {source.count(old)} times in select.py"
    module_name = f"dialback_ledger._lab_changed_{name.replace('-', '_')}"
    spec = importlib.util.spec_from_loader(module_name, loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "dialback_ledger"
    sys.modules[module_name] = module
    exec(compile(source.replace(old, new), f"<select.py: {name}>", "exec"), module.__dict__)   # noqa: S102
    return module


@lru_cache(maxsize=1)
def pinned_sample() -> tuple[World, ...]:
    return tuple(sample(worlds(), examples=EXAMPLES, seed=SEED))


def first_difference(rule) -> int | None:
    """The index of the first sampled world on which `rule.select` differs from production, or None."""
    for i, world in enumerate(pinned_sample()):
        if rule.select(**world.kwargs) != production.select(**world.kwargs):
            return i
    return None


def test_an_unchanged_copy_is_never_caught():
    # The differential check must not fire on a copy with no change, or every test below passes vacuously.
    same = changed_rule("unchanged", "RULE_VERSION = ", "RULE_VERSION = ")
    assert first_difference(same) is None


@pytest.mark.parametrize(("name", "old", "new"), B5 + KNOBS, ids=[c[0] for c in B5 + KNOBS])
def test_each_change_is_caught(name, old, new):
    assert first_difference(changed_rule(name, old, new)) is not None, \
        f"{name}: no world among {EXAMPLES} at seed {SEED} tells the changed rule from production"
