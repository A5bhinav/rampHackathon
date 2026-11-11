"""Tasks 6c and 6f's evaluation script (the model-decides flagger), on fakes and a tiny synthetic fixture directory in tmp_path. It never reads
the real fixture directories and never reaches the network."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

import eval_flagger
import pytest
from dialback_core.email_door import classifier as classifier_module
from dialback_core.email_door.classifier import ClassifierFailed, Verdict

ASKS = "Hi team, we have changed our bank account. Please update your records."   # the rule flags this
PLAIN = "Hi team, attached is invoice for October. Thanks!"                        # and not this
SOFT = "Kindly route the remaining balance to Evergreen Mutual from now on. TEXTMARK"   # the model flags this
BOOM = "Hi team, a note that makes the fake model fail. TEXTMARK"
QUOTE = "QUOTEMARK from the model"


class FakeModel:
    """TEST DOUBLE for a ChangeClassifier: yes on 'Evergreen' or 'changed our bank', ClassifierFailed on 'fail', else no."""

    input_tokens, output_tokens, calls = 0, 0, 0

    def classify(self, subject: str, text: str) -> Verdict:
        if "fail" in text:
            raise ClassifierFailed("timeout")
        return Verdict(True, QUOTE) if "Evergreen" in text or "changed our bank" in text else Verdict(False, "")


def email(label: str, text: str, note: str = "NOTEMARK") -> dict:
    return {"subject": "SUBJMARK", "text": text, "label": label, "vendor": "VENDORMARK", "note": note, "sender": "a@b"}


def write(dir_: Path, emails: dict[str, dict]) -> None:
    dir_.mkdir(parents=True)
    for name, e in emails.items():
        (dir_ / name).write_text(json.dumps(e))


@pytest.fixture
def fixtures(tmp_path):
    root = tmp_path / "fixtures"
    write(root / "tuning", {
        "cr-01.json": email("change_request", ASKS),      # flagged by the model
        "cr-02.json": email("change_request", SOFT),      # flagged by the model
        "cr-03.json": email("change_request", PLAIN),     # missed
        "ord-01.json": email("ordinary", PLAIN),
        "ord-02.json": email("ordinary", SOFT),           # falsely flagged by the model
        "ord-03.json": email("ordinary", BOOM),           # the model fails: counts as not flagged
    })
    write(root / "heldout", {"cr-01.json": email("change_request", ASKS), "ord-01.json": email("ordinary", PLAIN)})
    return root


def seal(root: Path, good: bool = True) -> None:
    files = sorted((root / "heldout2").glob("*.json"))
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()
    (root / "heldout2.sha256").write_text(("0" * 64 if not good else digest) + "  -\n")


def heldout2(root: Path, *, cr36_flagged: bool, ordinary_flagged: int, missed_other: bool = False) -> None:
    emails = {f"cr-{i:02d}.json": email("change_request", ASKS) for i in (1, 2)}
    emails["cr-36.json"] = email("change_request", ASKS if cr36_flagged else PLAIN)
    if missed_other:
        emails["cr-02.json"] = email("change_request", PLAIN)
    for i in range(5):
        emails[f"ord-{i:02d}.json"] = email("ordinary", SOFT if i < ordinary_flagged else PLAIN)
    write(root / "heldout2", emails)
    seal(root)


def seal3(root: Path) -> None:
    files = sorted((root / "heldout3").glob("*.json"))
    (root / "heldout3.sha256").write_text(hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest() + "  -\n")


def heldout3(root: Path, *, on_file_only_flagged: bool = False, cr04_flagged: bool = True, ordinary_flagged: int = 0,
             missed_other: bool = False) -> None:
    ofo = "ON-FILE-ONLY: SECRETNOTE the bank details come with no ask"
    emails = {"cr-01.json": email("change_request", ASKS), "cr-04.json": email("change_request", ASKS if cr04_flagged else PLAIN),
              "cr-10.json": email("change_request", ASKS),
              "cr-20.json": email("change_request", ASKS if on_file_only_flagged else PLAIN, ofo),
              "cr-21.json": email("change_request", ASKS, "An ordinary note ON-FILE-ONLY: later")}   # not at the start
    if missed_other:
        emails["cr-01.json"] = email("change_request", PLAIN)
    for i in range(5):
        emails[f"ord-{i:02d}.json"] = email("ordinary", SOFT if i < ordinary_flagged else PLAIN)
    write(root / "heldout3", emails)
    seal3(root)


@pytest.fixture(autouse=True)
def synthetic_pin(monkeypatch):
    """The synthetic heldout3 tree's ON-FILE-ONLY request is cr-20; the real pin (the three real names) is tested
    on its own below."""
    monkeypatch.setattr(eval_flagger, "ON_FILE_ONLY_HELDOUT3", frozenset({"cr-20.json"}))


def evaluate(fixtures_dir, name="tuning", model_factory=FakeModel):
    return eval_flagger.evaluate_set(name, eval_flagger.load_set(fixtures_dir / eval_flagger.SETS[name]),
                                     model_factory(), model="claude-haiku-5-5", workers=4)


def test_counting_by_label_and_by_who_flagged(fixtures):
    r = evaluate(fixtures)
    assert (r["change_requests_flagged"], r["change_requests_total"]) == (2, 3)
    assert (r["ordinary_flagged"], r["ordinary_total"]) == (1, 3)
    assert (r["change_requests_by_model"], r["ordinary_by_model"]) == (2, 1)
    assert "change_requests_by_rule" not in r and r["flagger"] == "model"
    assert r["missed"] == ["cr-03.json"] and r["false_flags"] == ["ord-02.json"]


def test_a_failure_counts_as_not_flagged_and_is_tallied_by_reason(fixtures):
    r = evaluate(fixtures)
    assert r["failures"] == {"timeout": 1} and r["failed"] == {"ord-03.json": "timeout"}
    assert "ord-03.json" not in r["false_flags"]
    # a failure on a change request is a miss
    write(fixtures / "x", {"cr-01.json": email("change_request", BOOM)})
    r = eval_flagger.evaluate_set("tuning", eval_flagger.load_set(fixtures / "x"), FakeModel(), model="m", workers=2)
    assert r["missed"] == ["cr-01.json"] and r["failures"] == {"timeout": 1}
    assert r["failed"] == {"cr-01.json": "timeout"}   # told apart from an ordinary email that failed


def test_only_subject_text_label_and_the_on_file_only_flag_are_loaded(fixtures):
    loaded = eval_flagger.load_set(fixtures / "tuning")
    assert set(loaded["cr-01.json"]) == {"subject", "text", "label", "on_file_only"}
    assert loaded["cr-01.json"]["on_file_only"] is False


def test_cost_and_latency_and_usage(fixtures):
    class Counting(FakeModel):
        input_tokens, output_tokens = 1_000_000, 2_000_000

    r = eval_flagger.evaluate_set("tuning", eval_flagger.load_set(fixtures / "tuning"), Counting(), model="claude-haiku-5-5", workers=4)
    assert (r["input_tokens"], r["output_tokens"]) == (1_000_000, 2_000_000)
    assert r["cost_usd"] == pytest.approx(0.10 + 2 * 0.50)
    assert eval_flagger.cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(24.0)
    assert eval_flagger.cost_usd("claude-other", 1, 1) is None
    assert r["latency_p50_s"] is not None and r["latency_p95_s"] >= r["latency_p50_s"]
    assert eval_flagger.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert eval_flagger.percentile([1.0, 2.0, 3.0, 4.0], 95) == 4.0
    assert eval_flagger.percentile([], 50) is None


@pytest.mark.parametrize("cr36_flagged, ordinary_flagged, missed_other, without, with_", [
    (False, 3, False, True, False),    # cr-36 missed: passes without it, fails with it
    (True, 3, False, True, True),
    (True, 4, False, False, False),    # too many ordinary
    (True, 0, True, False, False),     # another change request missed
])
def test_the_heldout2_bar_with_and_without_cr_36(tmp_path, cr36_flagged, ordinary_flagged, missed_other, without, with_):
    heldout2(tmp_path, cr36_flagged=cr36_flagged, ordinary_flagged=ordinary_flagged, missed_other=missed_other)
    r = evaluate(tmp_path, "heldout2")
    assert r["cr_36"] == {"flagged": cr36_flagged}
    assert "cr-36.json" not in r["missed"]
    assert (r["change_requests_total"], r["ordinary_total"]) == (2, 5)   # cr-36 is not counted among them
    assert r["bar_without_cr_36"] == ("PASS" if without else "FAIL")
    assert r["bar_with_cr_36"] == ("PASS" if with_ else "FAIL")


EXPECTED = {"tuning": (3, 3), "dev": (1, 1), "heldout2": (3, 5), "heldout3": (5, 5)}
T0 = datetime(2026, 11, 10, 12, 30, 5, tzinfo=UTC)


def run_main(fixtures, tmp_path, *args, now=T0, model_factory=FakeModel):
    return eval_flagger.main(list(args), fixtures=fixtures, out_dir=tmp_path / "out",
                             make_classifier=lambda m: model_factory(), now=now, expected=EXPECTED)


def spy_on_read_set(monkeypatch) -> list:
    """Records each directory eval_flagger.read_set is asked to read (and still reads it)."""
    seen, real = [], eval_flagger.read_set

    def spy(directory):
        seen.append(directory.name)
        return real(directory)
    monkeypatch.setattr(eval_flagger, "read_set", spy)
    return seen


def test_heldout2_needs_the_confirm_flag_and_is_never_read_without_it(tmp_path, capsys, monkeypatch):
    heldout2(tmp_path, cr36_flagged=True, ordinary_flagged=0)
    read = spy_on_read_set(monkeypatch)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout2") == 2
    assert "--confirm-heldout2" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()
    assert read == [], "heldout2 was never read"


def test_a_hash_mismatch_refuses_heldout2(tmp_path, capsys):
    heldout2(tmp_path, cr36_flagged=True, ordinary_flagged=0)
    seal(tmp_path, good=False)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout2", "--confirm-heldout2") == 2
    assert "hash" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_heldout2_runs_when_confirmed_and_hash_checked(tmp_path, capsys):
    heldout2(tmp_path, cr36_flagged=False, ordinary_flagged=1)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout2", "--confirm-heldout2") == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "cr-36.json" in out
    assert (tmp_path / "out" / "2026-11-10T123005Z-claude-haiku-5-5-heldout2.json").exists()


def test_the_results_file_and_the_table_hold_counts_and_file_names_only(fixtures, tmp_path, capsys):
    assert run_main(fixtures, tmp_path, "--sets", "tuning", "dev") == 0
    out = capsys.readouterr()
    written = sorted((tmp_path / "out").glob("*.json"))
    assert [p.name for p in written] == ["2026-11-10T123005Z-claude-haiku-5-5-dev.json", "2026-11-10T123005Z-claude-haiku-5-5-tuning.json"]
    data = json.loads(written[1].read_text())
    assert data["missed"] == ["cr-03.json"] and data["model"] == "claude-haiku-5-5"
    blob = out.out + out.err + "".join(p.read_text() for p in written)
    for secret in ("TEXTMARK", "SUBJMARK", "QUOTEMARK", "NOTEMARK", "VENDORMARK", "Evergreen"):
        assert secret not in blob
    assert "cr-03.json" in out.out


def test_a_missing_key_stops_before_any_call(monkeypatch, fixtures, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert eval_flagger.main(["--sets", "tuning"], fixtures=fixtures, out_dir=tmp_path / "out", expected=EXPECTED) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


# Fix round 1 -------------------------------------------------------------------------------------------

def test_an_unexpected_error_is_tallied_by_type_and_counts_as_not_flagged(fixtures):
    class Broken(FakeModel):
        def classify(self, subject, text):
            raise RuntimeError("EMAILTEXT leaked into an error")

    r = evaluate(fixtures, model_factory=Broken)
    assert r["failures"] == {"unexpected:RuntimeError": 6}   # the model decides every email, so every email fails
    assert set(r["failed"].values()) == {"unexpected:RuntimeError"}
    assert r["change_requests_flagged"] == 0 and r["ordinary_flagged"] == 0
    assert "EMAILTEXT" not in json.dumps(r)


def test_every_email_is_counted_exactly_once(tmp_path):
    heldout2(tmp_path, cr36_flagged=True, ordinary_flagged=1)
    r = evaluate(tmp_path, "heldout2")
    assert r["change_requests_total"] + r["ordinary_total"] + 1 == r["emails"] == 8


def test_quiet_sdk_logs_survives_loggers_created_meanwhile():
    from dialback_core.email_door.classifier import quiet_sdk_logs

    stop, errors = threading.Event(), []

    def create():
        i = 0
        while not stop.is_set() and i < 1_000:
            logging.getLogger(f"anthropic.stress{i}")
            i += 1

    maker = threading.Thread(target=create)
    maker.start()
    try:
        for _ in range(20):
            try:
                quiet_sdk_logs()
            except RuntimeError as exc:
                errors.append(exc)
    finally:
        stop.set()
        maker.join()
    assert not errors


def test_a_result_file_is_never_overwritten(fixtures, tmp_path, capsys):
    assert run_main(fixtures, tmp_path, "--sets", "tuning") == 0
    (first,) = (tmp_path / "out").glob("*.json")
    before = first.read_text()
    assert run_main(fixtures, tmp_path, "--sets", "tuning") == 2   # same second, same name
    assert "exists" in capsys.readouterr().err and first.read_text() == before
    later = datetime(2026, 11, 10, 12, 30, 6, tzinfo=UTC)
    assert run_main(fixtures, tmp_path, "--sets", "tuning", now=later) == 0
    assert len(list((tmp_path / "out").glob("*.json"))) == 2


def test_sets_are_deduplicated(fixtures, tmp_path):
    assert run_main(fixtures, tmp_path, "--sets", "tuning", "tuning", "dev", "tuning") == 0
    assert len(list((tmp_path / "out").glob("*.json"))) == 2


def test_heldout2_is_exploratory_and_can_be_rerun_but_keeps_its_hash_check(tmp_path, capsys):
    heldout2(tmp_path, cr36_flagged=True, ordinary_flagged=0)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout2", "--confirm-heldout2") == 0
    later = datetime(2026, 11, 11, tzinfo=UTC)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout2", "--confirm-heldout2", now=later) == 0
    assert len(list((tmp_path / "out").glob("*-heldout2.json"))) == 2
    seal(tmp_path, good=False)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout2", "--confirm-heldout2", now=datetime(2026, 11, 12, tzinfo=UTC)) == 2
    assert "hash" in capsys.readouterr().err


def test_every_result_file_records_the_flagger_mode(fixtures, tmp_path):
    assert run_main(fixtures, tmp_path, "--sets", "tuning", "dev") == 0
    for path in (tmp_path / "out").glob("*.json"):
        assert json.loads(path.read_text())["flagger"] == "model"


def test_heldout3_needs_its_own_confirm_flag_and_is_never_read_without_it(tmp_path, capsys, monkeypatch):
    heldout3(tmp_path)
    read = spy_on_read_set(monkeypatch)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout3") == 2
    assert "--confirm-heldout3" in capsys.readouterr().err and not (tmp_path / "out").exists()
    heldout2(tmp_path, cr36_flagged=True, ordinary_flagged=0)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout3", "--confirm-heldout2") == 2   # the other flag does not do
    assert read == [], "heldout3 was never read"


def test_a_hash_mismatch_refuses_heldout3(tmp_path, capsys):
    heldout3(tmp_path)
    (tmp_path / "heldout3.sha256").write_text("0" * 64 + "  -\n")
    assert run_main(tmp_path, tmp_path, "--sets", "heldout3", "--confirm-heldout3") == 2
    assert "hash" in capsys.readouterr().err and not (tmp_path / "out").exists()


def test_heldout3_runs_once_per_model(tmp_path, capsys):
    heldout3(tmp_path)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout3", "--confirm-heldout3") == 0
    later = datetime(2026, 11, 11, tzinfo=UTC)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout3", "--confirm-heldout3", now=later) == 2
    assert "already" in capsys.readouterr().err
    assert run_main(tmp_path, tmp_path, "--sets", "heldout3", "--confirm-heldout3", "--model", "claude-opus-5-5", now=later) == 0


@pytest.mark.parametrize("ofo_flagged, ordinary_flagged, missed_other, without, with_", [
    (False, 3, False, True, False),    # the ON-FILE-ONLY one missed: passes without it, fails with it
    (True, 3, False, True, True),
    (True, 4, False, False, False),    # too many ordinary
    (True, 0, True, False, False),     # another change request missed
])
def test_the_heldout3_bar_without_and_with_the_on_file_only_requests(tmp_path, ofo_flagged, ordinary_flagged, missed_other, without, with_):
    heldout3(tmp_path, on_file_only_flagged=ofo_flagged, ordinary_flagged=ordinary_flagged, missed_other=missed_other)
    r = evaluate(tmp_path, "heldout3")
    assert r["on_file_only"] == {"cr-20.json": ofo_flagged}   # reported separately, by file name only
    assert "cr-20.json" not in r["missed"]
    assert (r["change_requests_total"], r["ordinary_total"]) == (4, 5)   # cr-20 is not counted among them
    assert r["bar_without_on_file_only"] == ("PASS" if without else "FAIL")
    assert r["bar_with_on_file_only"] == ("PASS" if with_ else "FAIL")
    assert r["heldout2_revised"] is False and r["excluded_after_heldout2_revision"] == {}


def test_cr_04_and_cr_10_leave_the_heldout3_bar_only_after_a_heldout2_revision(tmp_path):
    heldout3(tmp_path, on_file_only_flagged=True, cr04_flagged=False)
    plain = evaluate(tmp_path, "heldout3")
    assert plain["missed"] == ["cr-04.json"] and plain["bar_without_on_file_only"] == "FAIL"
    emails = eval_flagger.load_set(tmp_path / "heldout3")
    r = eval_flagger.evaluate_set("heldout3", emails, FakeModel(), model="claude-haiku-5-5", workers=2, heldout2_revised=True)
    assert r["missed"] == [] and r["change_requests_total"] == 2
    assert r["excluded_after_heldout2_revision"] == {"cr-04.json": False, "cr-10.json": True}
    assert r["heldout2_revised"] is True and r["bar_without_on_file_only"] == "PASS"


def test_heldout3_reports_on_file_only_without_any_note_text(tmp_path, capsys):
    heldout3(tmp_path, on_file_only_flagged=True)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout3", "--confirm-heldout3", "--heldout2-revised") == 0
    out = capsys.readouterr()
    (path,) = (tmp_path / "out").glob("*-heldout3.json")
    blob = out.out + out.err + path.read_text()
    assert "ON-FILE-ONLY (separate)" in out.out and "cr-20.json" in blob
    for secret in ("SECRETNOTE", "NOTEMARK", "TEXTMARK", "SUBJMARK", "no ask", "later"):
        assert secret not in blob
    assert json.loads(path.read_text())["flagger"] == "model"


def test_only_a_note_that_starts_with_the_marker_counts(tmp_path):
    write(tmp_path / "s", {"a.json": email("change_request", PLAIN, "ON-FILE-ONLY: x"),
                           "b.json": email("change_request", PLAIN, "see ON-FILE-ONLY: x"),
                           "c.json": email("change_request", PLAIN, "on-file-only: x")})
    loaded = eval_flagger.load_set(tmp_path / "s")
    assert [loaded[f]["on_file_only"] for f in ("a.json", "b.json", "c.json")] == [True, False, False]


def test_the_composition_is_checked_before_any_call(fixtures, tmp_path, capsys):
    calls = []

    class Spy(FakeModel):
        def classify(self, subject, text):
            calls.append(1)
            return super().classify(subject, text)

    assert eval_flagger.main(["--sets", "tuning"], fixtures=fixtures, out_dir=tmp_path / "out", now=T0,
                             make_classifier=lambda m: Spy(), expected={"tuning": (30, 62)}) == 2
    assert "tuning" in capsys.readouterr().err and calls == []


def test_an_unknown_label_is_an_error(tmp_path):
    write(tmp_path / "s", {"a.json": email("maybe", PLAIN)})
    with pytest.raises(eval_flagger.EvalError):
        eval_flagger.load_set(tmp_path / "s")


def test_heldout2_composition_is_forty_change_requests_with_cr_36_and_seventy_ordinary():
    assert eval_flagger.EXPECTED == {"tuning": (30, 62), "dev": (35, 65), "heldout2": (40, 70), "heldout3": (40, 70)}


def test_nul_bytes_are_stripped_as_production_does(tmp_path):
    write(tmp_path / "s", {"a.json": email("ordinary", "Hi\u0000 there")})
    assert eval_flagger.load_set(tmp_path / "s")["a.json"]["text"] == "Hi there"


def test_the_hash_is_of_the_bytes_that_are_run(tmp_path):
    heldout2(tmp_path, cr36_flagged=True, ordinary_flagged=0)
    emails, digest = eval_flagger.read_set(tmp_path / "heldout2")
    files = sorted((tmp_path / "heldout2").glob("*.json"))
    assert digest == hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest() and len(emails) == 8


def test_only_the_two_named_models_are_allowed(fixtures, tmp_path):
    with pytest.raises(SystemExit):
        run_main(fixtures, tmp_path, "--model", "claude-other")


@pytest.mark.parametrize("mode, ok", [("", True), ("metadata", True), ("full", False), ("content", False)])
def test_span_content_capture_refuses_the_run(monkeypatch, fixtures, tmp_path, capsys, mode, ok):
    monkeypatch.setenv("ANTHROPIC_OPEN_TELEMETRY_TRACES_CONTENT_MODE", mode)
    assert run_main(fixtures, tmp_path, "--sets", "tuning") == (0 if ok else 2)
    if not ok:
        assert "TELEMETRY" in capsys.readouterr().err


def test_the_table_shows_failures_by_file_and_model_call_latency(fixtures, tmp_path, capsys):
    run_main(fixtures, tmp_path, "--sets", "tuning")
    out = capsys.readouterr().out
    assert "ord-03.json" in out and "timeout" in out and "model-call latency" in out


# Task 6g -------------------------------------------------------------------------------------------------

def test_every_result_records_its_prompt_and_the_header_shows_the_version(fixtures, tmp_path, capsys):
    assert run_main(fixtures, tmp_path, "--sets", "tuning", "dev") == 0
    out = capsys.readouterr().out
    expected = hashlib.sha256(classifier_module.SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    for path in (tmp_path / "out").glob("*.json"):
        data = json.loads(path.read_text())
        assert data["prompt_version"] == classifier_module.PROMPT_VERSION == "v2"
        assert data["prompt_sha256"] == expected
    assert out.count("prompt v2") == 2
    assert "== tuning (claude-haiku-5-5, flagger: model, prompt v2) ==" in out


def test_the_recorded_prompt_is_read_from_the_classifier_module_at_run_time(fixtures, tmp_path, monkeypatch):
    monkeypatch.setattr(classifier_module, "SYSTEM_PROMPT", "another prompt")
    monkeypatch.setattr(classifier_module, "PROMPT_VERSION", "v9")
    assert run_main(fixtures, tmp_path, "--sets", "dev") == 0
    (path,) = (tmp_path / "out").glob("*.json")
    data = json.loads(path.read_text())
    assert data["prompt_version"] == "v9"
    assert data["prompt_sha256"] == hashlib.sha256(b"another prompt").hexdigest()


def test_the_pinned_on_file_only_set_is_the_three_named_requests(monkeypatch):
    monkeypatch.undo()   # the autouse fixture's stand-in for the synthetic tree
    assert eval_flagger.ON_FILE_ONLY_HELDOUT3 == frozenset({"cr-26.json", "cr-27.json", "cr-31.json"})


def heldout3_with_on_file_only(root: Path, names: set[str]) -> None:
    """A heldout3 tree whose ON-FILE-ONLY change requests are exactly `names`."""
    ofo = "ON-FILE-ONLY: SECRETNOTE"
    emails = {name: email("change_request", ASKS, ofo) for name in names}
    emails["cr-01.json"] = email("change_request", ASKS)
    for i in range(5):
        emails[f"ord-{i:02d}.json"] = email("ordinary", PLAIN)
    write(root / "heldout3", emails)
    seal3(root)


@pytest.mark.parametrize("names", [{"cr-20.json", "cr-21.json"}, set()], ids=["one extra", "one missing"])
def test_a_heldout3_whose_on_file_only_set_differs_from_the_pin_is_refused(tmp_path, capsys, names):
    heldout3_with_on_file_only(tmp_path, names)
    calls = []

    class Spy(FakeModel):
        def classify(self, subject, text):
            calls.append(1)
            return super().classify(subject, text)

    expected = {**EXPECTED, "heldout3": (len(names) + 1, 5)}
    made = []
    code = eval_flagger.main(["--sets", "heldout3", "--confirm-heldout3"], fixtures=tmp_path, out_dir=tmp_path / "out",
                             make_classifier=lambda m: made.append(m) or Spy(), now=T0, expected=expected)
    err = capsys.readouterr().err
    assert code == 2 and "heldout3" in err and "ON-FILE-ONLY" in err
    assert calls == [] and made == [] and not (tmp_path / "out").exists()
    assert "SECRETNOTE" not in err


def test_a_heldout3_whose_on_file_only_set_matches_the_pin_runs(tmp_path):
    heldout3_with_on_file_only(tmp_path, {"cr-20.json"})
    expected = {**EXPECTED, "heldout3": (2, 5)}
    code = eval_flagger.main(["--sets", "heldout3", "--confirm-heldout3"], fixtures=tmp_path, out_dir=tmp_path / "out",
                             make_classifier=lambda m: FakeModel(), now=T0, expected=expected)
    assert code == 0 and len(list((tmp_path / "out").glob("*-heldout3.json"))) == 1
