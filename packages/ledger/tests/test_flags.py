"""Spec 0079, decision 7: DIALBACK_CONTEXT_GUARD, read in flags.py. On by default; any unknown value
means on; only off runs today's rule alone, for rollback. What select() does with it is in
test_select_guard.py."""
from dialback_ledger.flags import context_guard


def test_context_guard_defaults_on():
    assert context_guard({}) is True
    for value in ("on", "ON", " on ", "", "yes", "true", "1", "0", "false", "no", "of", "offf", "disabled"):
        assert context_guard({"DIALBACK_CONTEXT_GUARD": value}) is True, value
    for value in ("off", "OFF", " Off "):
        assert context_guard({"DIALBACK_CONTEXT_GUARD": value}) is False, value


def test_context_guard_reads_the_environment(monkeypatch):
    monkeypatch.setenv("DIALBACK_CONTEXT_GUARD", "off")
    assert context_guard() is False
    monkeypatch.delenv("DIALBACK_CONTEXT_GUARD")
    assert context_guard() is True
