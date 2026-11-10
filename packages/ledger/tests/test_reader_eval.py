"""Spec 0049's evaluation script, on fixed inputs: its counting, the paired unsafe check, the
McNemar test and the bars. The script itself runs live, on demand, never in CI; nothing here calls
a model."""
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from fractions import Fraction

import pytest
from _world import ROOT
from dialback_ledger.ingest import ingest_all
from dialback_ledger.reader import ReaderFailed, StubReader
from test_extract import page, span

_spec = importlib.util.spec_from_file_location("reader_eval", ROOT / "packages/ledger/scripts/reader_eval.py")
ev = sys.modules.setdefault("reader_eval", importlib.util.module_from_spec(_spec))
_spec.loader.exec_module(ev)


def truth(line, e164, role, party, *, explicit_role=False, us=False):
    return {"line": line, "e164": e164, "role": role, "party": party, "explicit_role": explicit_role, "us": us}


def doc(doc_id, kind, texts, numbers):
    return {"kind": kind, "document": page(*(span(t) for t in texts)) | {"id": doc_id}, "numbers": numbers}


class Fixed:
    """A reader that returns fixed labels per document text, or fails on some."""

    version = "fixed"

    def __init__(self, labels_by_first_line: dict, fail_on: tuple = ()):
        self.labels, self.fail_on = labels_by_first_line, fail_on

    def label(self, visible_text):
        first = visible_text.split("\n")[0]
        if first in self.fail_on:
            raise ReaderFailed("timeout")
        return self.labels.get(first, [])


def lab(quote, role, party):
    return {"quote": quote, "role": role, "party": party}


# Counting --------------------------------------------------------------------------------------

def test_counting():
    labeled = {"split": "dev", "documents": [
        doc("o1", "ordinary", ["Invoice 1", "AR: (206) 555-0142", "Fax: (206) 555-0199", "Bank: 206-555-0186"], [
            truth(1, "+12065550142", "ar", "vendor", explicit_role=True),
            truth(2, "+12065550199", "fax", "vendor", explicit_role=True),
            truth(3, "+12065550186", "unknown", "bank")]),
        doc("h1", "hard", ["Note 1", "Call us at (206) 555-0117", "Ring 206-555-0177 for help"], [
            truth(1, "+12065550117", "main", "vendor", us=True),
            truth(2, "+12065550177", "unknown", "unknown")]),
    ]}
    reader = Fixed({
        "Invoice 1": [lab("AR: (206) 555-0142", "ar", "vendor"), lab("Fax: (206) 555-0199", "fax", "vendor"),
                      lab("Bank: 206-555-0186", "unknown", "vendor")],              # unsafe: a bank's number as vendor
        "Note 1": [lab("Call us at (206) 555-0117", "main", "unknown"),            # (main, unknown): over-block
                   lab("Ring 206-555-0177 for help", "unknown", "vendor")],        # unsafe: no word says vendor
    })
    out, failed = ev.score(labeled, reader)
    assert failed == 0
    by = {o.key: o for o in out}
    assert not by[("o1", 2, "+12065550199")].over_block   # a true fax line labeled fax is right (spec f33499d)
    assert not by[("o1", 1, "+12065550142")].over_block
    assert by[("o1", 3, "+12065550186")].unsafe and by[("h1", 2, "+12065550177")].unsafe
    assert by[("h1", 1, "+12065550117")].over_block and by[("h1", 1, "+12065550117")].us
    c = ev.count(out, failed)
    assert c == {"numbers": 5, "numbers_by_kind": {"ordinary": 3, "hard": 2}, "unsafe": 2,
                 "over_block": {"ordinary": 0, "hard": 1},
                 "role_accuracy": {"correct": 2, "of": 2},
                 "us": {"numbers": 1, "unsafe": 0, "over_block": 1}, "failed_documents": 0}


def test_vendor_unknown_is_not_an_over_block_and_a_skipped_number_counts_as_unknown():
    labeled = {"split": "dev", "documents": [
        doc("o1", "ordinary", ["Hello", "Main: 206-555-0117", "AR 206-555-0142"], [
            truth(1, "+12065550117", "main", "vendor", explicit_role=True),
            truth(2, "+12065550142", "ar", "vendor", explicit_role=True)])]}
    out, _ = ev.score(labeled, Fixed({"Hello": [lab("Main: 206-555-0117", "unknown", "vendor")]}))
    by = {o.key[2]: o for o in out}
    assert not by["+12065550117"].over_block and by["+12065550117"].role_correct is False
    assert by["+12065550142"].over_block                  # the reader skipped it: (unknown, unknown)


def test_a_true_fax_line_labeled_fax_is_not_an_over_block():
    # Spec 0049, corrected at f33499d before any scored run: the role fax over-blocks only when the
    # text doesn't call the number a fax line.
    labeled = {"split": "dev", "documents": [
        doc("o1", "ordinary", ["Hello", "Fax: (206) 555-0199", "Main: (206) 555-0117"], [
            truth(1, "+12065550199", "fax", "vendor", explicit_role=True),
            truth(2, "+12065550117", "main", "vendor", explicit_role=True)])]}
    out, _ = ev.score(labeled, Fixed({"Hello": [lab("Fax: (206) 555-0199", "fax", "vendor"),
                                                lab("Main: (206) 555-0117", "fax", "vendor")]}))
    by = {o.key[2]: o for o in out}
    assert by["+12065550199"].role_correct and not by["+12065550199"].over_block   # right, and refused by design
    assert by["+12065550117"].role_correct is False and by["+12065550117"].over_block   # a main line called fax


def test_a_true_fax_line_is_never_an_over_block():
    # Spec 0049 at c8a7c5a (review F5): the rule refuses fax lines by design, so no label on one is an
    # over-block, (fax, unknown) and a skipped number included.
    labeled = {"split": "dev", "documents": [
        doc("o1", "ordinary", ["Hello", "Fax: (206) 555-0199"], [truth(1, "+12065550199", "fax", "vendor", explicit_role=True)])]}
    for got in [("fax", "vendor"), ("fax", "unknown"), ("unknown", "unknown"), ("main", "customer"), ("ar", "bank"), None]:
        labels = [] if got is None else [lab("Fax: (206) 555-0199", *got)]
        (o,), _ = ev.score(labeled, Fixed({"Hello": labels}))
        assert not o.over_block, got


def test_a_failed_document_counts_every_number_in_it_as_unknown():
    labeled = {"split": "dev", "documents": [
        doc("o1", "ordinary", ["Hello", "Main: 206-555-0117"], [truth(1, "+12065550117", "main", "vendor")])]}
    out, failed = ev.score(labeled, Fixed({}, fail_on=("Hello",)))
    assert failed == 1 and out[0].over_block and not out[0].unsafe


def test_a_truth_number_code_does_not_find_is_refused():
    labeled = {"split": "dev", "documents": [
        doc("o1", "ordinary", ["Main: 206-555-0117"], [truth(0, "+12065550124", "main", "vendor")])]}
    with pytest.raises(ev.LabeledSetError, match="o1"):
        ev.check(labeled, "dev")


def dev_set(*docs) -> dict:
    """A dev set of 30 documents: `docs`, then complete fillers of both kinds."""
    fillers = [doc(f"fill-{i}", "hard" if i % 2 else "ordinary", [f"Fill {i}", "Main: (206) 555-0118"],
                   [truth(1, "+12065550118", "main", "vendor", explicit_role=True)]) for i in range(30 - len(docs))]
    return {"split": "dev", "documents": [*docs, *fillers]}


def test_the_labeled_set_must_label_every_visible_number():
    # Review G9: a number left out of the set is never scored, so an unsafe label on it never counts.
    ev.check(dev_set(), "dev")
    partial = doc("p-0", "ordinary", ["Bank doc", "Main: (206) 555-0118", "Bank 206-555-0186"],
                  [truth(1, "+12065550118", "main", "vendor", explicit_role=True)])     # the bank line has no label
    with pytest.raises(ev.LabeledSetError, match="p-0: line 2 holds a visible phone number with no label"):
        ev.check(dev_set(partial), "dev")


def test_the_labeled_set_must_not_repeat_a_document_or_a_number():
    # Review G10: listing the same hard documents twice doubled the McNemar pairs (p 0.125 to 0.0156).
    one = doc("h-0", "hard", ["Hard 0", "Call us at (206) 555-0117"], [truth(1, "+12065550117", "main", "vendor", us=True)])
    with pytest.raises(ev.LabeledSetError, match="h-0: the document is listed twice"):
        ev.check(dev_set(one, one), "dev")
    twice = doc("h-1", "hard", ["Hard 1", "Call us at (206) 555-0117"],
                [truth(1, "+12065550117", "main", "vendor", us=True), truth(1, "+12065550117", "main", "vendor", us=True)])
    with pytest.raises(ev.LabeledSetError, match="h-1: line 1 is labeled twice"):
        ev.check(dev_set(twice), "dev")


def test_both_kinds_are_required_and_counted():
    # Review G14: with no ordinary documents the ordinary bar passed 0 <= 0 by construction.
    def one_kind(kind):
        return {"split": "dev", "documents": [
            doc(f"{kind}-{i}", kind, [f"Doc {i}", "Main: (206) 555-0118"],
                [truth(1, "+12065550118", "main", "vendor", explicit_role=True)]) for i in range(30)]}
    with pytest.raises(ev.LabeledSetError, match="no ordinary"):
        ev.check(one_kind("hard"), "dev")
    with pytest.raises(ev.LabeledSetError, match="no hard"):
        ev.check(one_kind("ordinary"), "dev")
    ev.check(dev_set(), "dev")
    out, failed = ev.score(dev_set(), StubReader())
    assert ev.count(out, failed)["numbers_by_kind"] == {"ordinary": 15, "hard": 15}


def test_labeled_set_errors_never_print_contents_or_numbers(tmp_path):
    # Review G20: a malformed set's errors printed numbers and, through the parser's message, text.
    secret = "Remit questions to Dana Whitfield"
    main = truth(1, "+12065550118", "main", "vendor", explicit_role=True)
    unreadable = doc("x-1", "ordinary", [secret, "Main: (206) 555-0118"], [main])
    unreadable["document"]["pages"][0]["width"] = f"{secret} 206-555-0186"
    cases = {
        "a label on a number code doesn't find": dev_set(doc("x-0", "ordinary", [secret, "Main: (206) 555-0118"],
                                                             [main, truth(0, "+12065550186", "unknown", "bank")])),
        "a page that can't be read": dev_set(unreadable),
        "a line that isn't a line": dev_set(doc("x-2", "ordinary", [secret, "Main: (206) 555-0118"],
                                                [main, truth(f"{secret} 206-555-0186", "+12065550186", "unknown", "bank")])),
        "an unlabeled number": dev_set(doc("x-3", "ordinary", [secret, "Bank 206-555-0186"], [])),
        "a repeated number": dev_set(doc("x-4", "ordinary", [secret, "Main: (206) 555-0118"], [main, main])),
    }
    for case, labeled in cases.items():
        path = tmp_path / "dev.json"
        path.write_text(json.dumps(labeled))
        with pytest.raises(ev.LabeledSetError) as refused:
            ev.load(path, "dev")
        message = f"{refused.value} {refused.value.__cause__!r}"
        for leak in (secret, "Dana", "555-01", "55501", "+1206"):
            assert leak not in message, (case, leak)
    path.write_text('{"split": "dev", "documents": [' + secret)                            # not JSON at all
    with pytest.raises(ev.LabeledSetError, match="malformed") as refused:
        ev.load(path, "dev")
    assert secret not in str(refused.value)


def test_read_matches_what_ingest_stores():
    # The eval labels each number the way ingestion does; if they drift apart, the eval measures
    # something else.
    for path in sorted((ROOT / "corpus" / "truth").glob("*.json")):
        docs = json.loads((ROOT / "corpus" / json.loads(path.read_text())["documents"]).read_text())
        stored = Counter((o["document_id"], o["value_normalized"], o["role"], o["party"])
                         for o in ingest_all(docs, extractor_version="t", reader=StubReader()).observations
                         if o["visibility"] == "visible")
        read = Counter((d["id"], e164, role, party) for d in docs
                       for (_, e164), (role, party) in ev.read(d, StubReader()).items())
        assert read == stored, path.stem


# The paired unsafe check -------------------------------------------------------------------------

def outcome(key, kind="ordinary", *, unsafe=False, over_block=False):
    return ev.Outcome(key, kind, unsafe, over_block, None, False)


def test_paired_unsafe_check():
    stub = [outcome("a", unsafe=True), outcome("b"), outcome("c")]
    same = [outcome("a", unsafe=True), outcome("b"), outcome("c")]
    new = [outcome("a"), outcome("b", unsafe=True), outcome("c")]
    assert ev.new_unsafe(same, stub) == []          # the stub made that one too: not new
    assert ev.new_unsafe(new, stub) == ["b"]
    with pytest.raises(ValueError):                 # the outcomes must pair number for number
        ev.new_unsafe([outcome("a"), outcome("b")], stub)


# The McNemar test --------------------------------------------------------------------------------

@pytest.mark.parametrize("b, c, p", [
    (5, 0, Fraction(1, 32)), (4, 0, Fraction(1, 16)), (6, 0, Fraction(1, 64)),
    (8, 2, Fraction(1 + 10 + 45, 1024)), (9, 1, Fraction(1 + 10, 1024)),
    (3, 5, Fraction(56 + 70 + 56 + 28 + 8 + 1, 256)), (0, 0, Fraction(1)), (0, 3, Fraction(1))])
def test_one_sided_exact_mcnemar(b, c, p):
    assert ev.mcnemar_p(b, c) == p


def test_mcnemar_pairs_only_the_hard_documents():
    stub = [outcome(("h", i), "hard", over_block=True) for i in range(5)] + [outcome(("o", 0), over_block=True)]
    llm = [outcome(("h", i), "hard") for i in range(5)] + [outcome(("o", 0))]
    assert ev.mcnemar(llm, stub) == (5, 0, Fraction(1, 32))   # the ordinary document doesn't count


# The bars -----------------------------------------------------------------------------------------

def test_every_bar_must_hold_on_every_run():
    hard = [outcome(("h", i), "hard", over_block=True) for i in range(6)]
    stub = hard + [outcome(("o", 0), over_block=False), outcome(("o", 1), unsafe=False)]
    good = [outcome(("h", i), "hard") for i in range(6)] + [outcome(("o", 0)), outcome(("o", 1))]
    regresses = good[:6] + [outcome(("o", 0), over_block=True), outcome(("o", 1))]
    unsafe = good[:7] + [outcome(("o", 1), unsafe=True)]
    weak = [outcome(("h", i), "hard", over_block=i >= 4) for i in range(6)] + good[6:]   # b=4: p = 1/16
    assert ev.bars([good, good, good], stub)["passed"]
    for bad, failing in ((regresses, "no_regression_on_ordinary"), (unsafe, "no_new_unsafe"),
                         (weak, "fewer_over_blocks_on_hard")):
        result = ev.bars([good, bad, good], stub)
        assert not result["passed"]
        assert [run[failing]["passed"] for run in result["runs"]] == [True, False, True]


# The held-out set ---------------------------------------------------------------------------------

def heldout(non_vendor: int) -> dict:
    docs = []
    for i in range(70):
        texts, numbers = ["Hi", "Main: 206-555-0117"], [truth(1, "+12065550117", "main", "vendor")]
        if i < non_vendor:     # only these hold the bank's line: the set must label every visible number
            texts.append("Bank 206-555-0186")
            numbers.append(truth(2, "+12065550186", "unknown", "bank"))
        docs.append(doc(f"heldout-doc-{i}", "hard" if i % 2 else "ordinary", texts, numbers))
    return {"split": "heldout", "documents": docs}


def test_the_heldout_set_needs_thirty_numbers_that_are_not_the_vendors():
    ev.check(heldout(30), "heldout")
    with pytest.raises(ev.LabeledSetError, match="30"):
        ev.check(heldout(29), "heldout")


def frozen(tmp_path, labeled: dict) -> tuple:
    """The held-out set written outside the repo, and a hash file as the repo would commit it."""
    path = tmp_path / "heldout.json"
    path.write_text(json.dumps(labeled))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    sha_file = tmp_path / "reader_heldout.sha256"
    sha_file.write_text(f"{digest}  heldout.json\n")
    return path, digest, sha_file


def test_the_heldout_report_shows_totals_only(tmp_path):
    path, digest, sha_file = frozen(tmp_path, heldout(30))
    llm = Fixed({"Hi": [lab("Bank 206-555-0186", "unknown", "vendor")]})                 # unsafe on every bank line
    loaded, sha = ev.load(path, "heldout", sha_file=sha_file)
    report = ev.evaluate(loaded, llm, StubReader(), runs=3, sha256=sha)
    text = json.dumps(report)
    assert report["sha256"] == digest and len(report["runs"]) == 3 and not report["passed"]
    assert report["runs"][0]["bars"]["no_new_unsafe"] == {"passed": False, "new": 30}
    assert "heldout-doc" not in text and "+12065550186" not in text                   # no list of errors


def test_the_heldout_hash_comes_from_a_committed_file(tmp_path, monkeypatch, capsys):
    # Review G13: "frozen before the first scored run" can't rest on someone typing the hash.
    assert ev.HELDOUT_SHA256 == ROOT / "packages/ledger/scripts/reader_heldout.sha256"
    path, digest, sha_file = frozen(tmp_path, heldout(30))
    assert ev.load(path, "heldout", sha_file=sha_file)[1] == digest
    sha_file.write_text("0" * 64 + "\n")
    with pytest.raises(ev.LabeledSetError, match="sha256"):
        ev.load(path, "heldout", sha_file=sha_file)
    with pytest.raises(ev.LabeledSetError, match="sha256"):
        ev.load(path, "heldout", sha_file=tmp_path / "nothing-committed.sha256")
    # The command line reads the committed file and takes no hash of its own.
    path, digest, sha_file = frozen(tmp_path, heldout(30))
    monkeypatch.setattr(ev, "HELDOUT_SHA256", sha_file)
    monkeypatch.setattr(ev, "reader_for", lambda mode: Fixed({}))
    monkeypatch.setenv("DIALBACK_READER", "llm")
    assert ev.main(["--split", "heldout", "--labels", str(path)]) in (0, 1)
    assert json.loads(capsys.readouterr().out)["sha256"] == digest
    with pytest.raises(SystemExit):
        ev.main(["--split", "heldout", "--labels", str(path), "--expect-sha256", digest])


def test_heldout_runs_exactly_three_times_and_no_run_never_passes(tmp_path, monkeypatch, capsys):
    # Review G11: --runs -1 gave zero runs, and every bar over no runs printed "passed": true.
    assert not ev.bars([], [outcome("a")])["passed"]
    path, _, sha_file = frozen(tmp_path, heldout(30))
    dev_path = tmp_path / "dev.json"
    dev_path.write_text(json.dumps(dev_set()))
    monkeypatch.setattr(ev, "HELDOUT_SHA256", sha_file)
    monkeypatch.setattr(ev, "reader_for", lambda mode: Fixed({}))
    monkeypatch.setenv("DIALBACK_READER", "llm")
    for runs in ("-1", "0", "1", "2", "4"):
        assert ev.main(["--split", "heldout", "--labels", str(path), "--runs", runs]) == 2, runs
        assert "exactly 3" in capsys.readouterr().err
    for runs in ("-1", "0"):
        assert ev.main(["--split", "dev", "--labels", str(dev_path), "--runs", runs]) == 2, runs
        assert "at least one run" in capsys.readouterr().err
    loaded, _ = ev.load(path, "heldout", sha_file=sha_file)
    with pytest.raises(ValueError, match="exactly 3"):
        ev.evaluate(loaded, Fixed({}), StubReader(), runs=1)
    with pytest.raises(ValueError, match="at least one run"):
        ev.evaluate(dev_set(), Fixed({}), StubReader(), runs=0)


def test_the_dev_report_lists_each_error():
    labeled = {"split": "dev", "documents": [
        doc("o1", "ordinary", ["Hi", "Bank 206-555-0186"], [truth(1, "+12065550186", "unknown", "bank")])]}
    report = ev.evaluate(labeled, Fixed({"Hi": [lab("Bank 206-555-0186", "unknown", "vendor")]}), StubReader(), runs=1)
    assert report["runs"][0]["errors"] == [{"document": "o1", "line": 1, "e164": "+12065550186",
                                            "truth": ["unknown", "bank"], "got": ["unknown", "vendor"],
                                            "unsafe": True, "over_block": False}]


def test_the_live_run_needs_llm_mode(monkeypatch, capsys):
    assert ev.main(["--split", "dev"]) == 2              # DIALBACK_READER is unset (conftest): no model call
    assert "DIALBACK_READER=llm" in capsys.readouterr().err
    monkeypatch.setenv("DIALBACK_READER", "llm")         # llm without its key refuses too
    assert ev.main(["--split", "dev"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
