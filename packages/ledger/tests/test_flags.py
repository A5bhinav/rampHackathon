"""Spec 0079, decision 7: DIALBACK_CONTEXT_GUARD, read in flags.py. On by default; any unknown value
means on; only off runs today's rule alone, for rollback. What select() does with it is in
test_select_guard.py.

Spec 0091, rule 6: DIALBACK_LABEL_BAR, the same way: on by default and for any unknown value; only
off turns the label bar off. What select() does with the two is in test_label_bar.py."""
from dialback_ledger.flags import context_guard, label_bar


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


def test_label_bar_defaults_on():
    assert label_bar({}) is True
    for value in ("on", "ON", " on ", "", "yes", "true", "1", "0", "false", "no", "of", "offf", "disabled"):
        assert label_bar({"DIALBACK_LABEL_BAR": value}) is True, value
    for value in ("off", "OFF", " Off "):
        assert label_bar({"DIALBACK_LABEL_BAR": value}) is False, value
    assert label_bar({"DIALBACK_CONTEXT_GUARD": "off"}) is True, "its own variable, not the guard's"
    assert context_guard({"DIALBACK_LABEL_BAR": "off"}) is True, "and the guard doesn't read it"


def test_label_bar_reads_the_environment(monkeypatch):
    monkeypatch.setenv("DIALBACK_LABEL_BAR", "off")
    assert label_bar() is False
    monkeypatch.delenv("DIALBACK_LABEL_BAR")
    assert label_bar() is True
