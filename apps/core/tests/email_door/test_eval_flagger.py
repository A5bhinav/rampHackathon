"""Task 6c's evaluation script, on fakes and a tiny synthetic fixture directory in tmp_path. It never reads
the real fixture directories and never reaches the network."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import eval_flagger
import pytest
from dialback_core.email_door.classifier import ClassifierFailed, Verdict

ASKS = "Hi team, we have changed our bank account. Please update your records."   # the rule flags this
PLAIN = "Hi team, attached is invoice for October. Thanks!"                        # and not this
SOFT = "Kindly route the remaining balance to Evergreen Mutual from now on. TEXTMARK"   # the model flags this
BOOM = "Hi team, a note that makes the fake model fail. TEXTMARK"
QUOTE = "QUOTEMARK from the model"


class FakeModel:
    """TEST DOUBLE for a ChangeClassifier: yes on 'Evergreen', ClassifierFailed on 'fail', else no."""

    input_tokens, output_tokens, calls = 0, 0, 0

    def classify(self, subject: str, text: str) -> Verdict:
        if "fail" in text:
            raise ClassifierFailed("timeout")
        return Verdict(True, QUOTE) if "Evergreen" in text else Verdict(False, "")


def email(label: str, text: str) -> dict:
    return {"subject": "SUBJMARK", "text": text, "label": label, "vendor": "VENDORMARK", "note": "NOTEMARK", "sender": "a@b"}


def write(dir_: Path, emails: dict[str, dict]) -> None:
    dir_.mkdir(parents=True)
    for name, e in emails.items():
        (dir_ / name).write_text(json.dumps(e))


@pytest.fixture
def fixtures(tmp_path):
    root = tmp_path / "fixtures"
    write(root / "tuning", {
        "cr-01.json": email("change_request", ASKS),      # flagged by the rule
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


def evaluate(fixtures_dir, name="tuning", model_factory=FakeModel):
    return eval_flagger.evaluate_set(name, eval_flagger.load_set(fixtures_dir / eval_flagger.SETS[name]),
                                     model_factory(), model="claude-haiku-5-5", workers=4)


def test_counting_by_label_and_by_who_flagged(fixtures):
    r = evaluate(fixtures)
    assert (r["change_requests_flagged"], r["change_requests_total"]) == (2, 3)
    assert (r["ordinary_flagged"], r["ordinary_total"]) == (1, 3)
    assert (r["change_requests_by_rule"], r["change_requests_by_model"]) == (1, 1)
    assert (r["ordinary_by_rule"], r["ordinary_by_model"]) == (0, 1)
    assert r["missed"] == ["cr-03.json"] and r["false_flags"] == ["ord-02.json"]


def test_a_failure_counts_as_not_flagged_and_is_tallied_by_reason(fixtures):
    r = evaluate(fixtures)
    assert r["failures"] == {"timeout": 1}
    assert "ord-03.json" not in r["false_flags"]
    # a failure on a change request is a miss
    write(fixtures / "x", {"cr-01.json": email("change_request", BOOM)})
    r = eval_flagger.evaluate_set("tuning", eval_flagger.load_set(fixtures / "x"), FakeModel(), model="m", workers=2)
    assert r["missed"] == ["cr-01.json"] and r["failures"] == {"timeout": 1}


def test_only_subject_text_and_label_are_loaded(fixtures):
    loaded = eval_flagger.load_set(fixtures / "tuning")
    assert set(loaded["cr-01.json"]) == {"subject", "text", "label"}


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
    assert r["bar_without_cr_36"] == ("PASS" if without else "FAIL")
    assert r["bar_with_cr_36"] == ("PASS" if with_ else "FAIL")


def run_main(fixtures, tmp_path, *args):
    return eval_flagger.main(list(args), fixtures=fixtures, out_dir=tmp_path / "out", make_classifier=lambda m: FakeModel(),
                             today="2026-11-10")


def test_heldout2_needs_the_confirm_flag_and_is_never_read_without_it(tmp_path, capsys):
    heldout2(tmp_path, cr36_flagged=True, ordinary_flagged=0)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout2") == 2
    assert "--confirm-heldout2" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_a_hash_mismatch_refuses_heldout2(tmp_path, capsys):
    heldout2(tmp_path, cr36_flagged=True, ordinary_flagged=0)
    seal(tmp_path, good=False)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout2", "--confirm-heldout2") == 2
    assert "hash" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_heldout2_runs_when_confirmed_and_sealed(tmp_path, capsys):
    heldout2(tmp_path, cr36_flagged=False, ordinary_flagged=1)
    assert run_main(tmp_path, tmp_path, "--sets", "heldout2", "--confirm-heldout2") == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "cr-36.json" in out
    assert (tmp_path / "out" / "2026-11-10-claude-haiku-5-5-heldout2.json").exists()


def test_the_results_file_and_the_table_hold_counts_and_file_names_only(fixtures, tmp_path, capsys):
    assert run_main(fixtures, tmp_path, "--sets", "tuning", "dev") == 0
    out = capsys.readouterr()
    written = sorted((tmp_path / "out").glob("*.json"))
    assert [p.name for p in written] == ["2026-11-10-claude-haiku-5-5-dev.json", "2026-11-10-claude-haiku-5-5-tuning.json"]
    data = json.loads(written[1].read_text())
    assert data["missed"] == ["cr-03.json"] and data["model"] == "claude-haiku-5-5"
    blob = out.out + out.err + "".join(p.read_text() for p in written)
    for secret in ("TEXTMARK", "SUBJMARK", "QUOTEMARK", "NOTEMARK", "VENDORMARK", "Evergreen"):
        assert secret not in blob
    assert "cr-03.json" in out.out


def test_a_missing_key_stops_before_any_call(monkeypatch, fixtures, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert eval_flagger.main(["--sets", "tuning"], fixtures=fixtures, out_dir=tmp_path / "out") == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
