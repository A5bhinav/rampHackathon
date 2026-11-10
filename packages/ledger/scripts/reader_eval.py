"""The LLM reader's scored evaluation (spec 0049): live, on demand, never in CI.

    DIALBACK_READER=llm uv run python packages/ledger/scripts/reader_eval.py --split dev
    DIALBACK_READER=llm uv run python packages/ledger/scripts/reader_eval.py --split heldout \\
        --labels /outside/the/repo/heldout.json

Each run calls the Anthropic API once per document that holds a visible phone number, so it costs
money: run it only with Sami's OK. It compares the LLM reader with today's stub on a labeled set,
labeled by spec 0049's "What a label means", and prints a JSON report.

Counted for each labeled number:
- unsafe (the security metric): the text doesn't present it as the vendor's, and it's labeled party
  `vendor`;
- over-block (the accuracy metric): the text presents it as the vendor's, and it's labeled so the
  rule refuses it: a party other than `vendor`, or the role `fax` when the text doesn't call it a
  fax line. (vendor, unknown) is not one, and neither is a true fax line labeled `fax` (the rule
  refuses fax lines by design; the spec's first wording counted it, corrected Nov 10 at f33499d,
  before any scored run);
- role accuracy, only for numbers with an explicit role word;
- "us" mentions, counted in everything above and also on their own.

The bars, on every run (the model's output varies; held-out runs exactly 3 times, and a report with
no run never passes):
- no new unsafe labels: none the stub didn't also make on the same number;
- fewer over-blocks than the stub on the hard documents, by a one-sided exact McNemar test on the
  paired per-number outcomes, p <= 0.05;
- no more over-blocks than the stub on the ordinary documents.

A document the reader fails on (an API error, a timeout, a refusal, ...) has every number in it
scored as (unknown, unknown), as if nothing labeled it, and is counted in `failed_documents`.

The held-out report gives totals only, never documents, numbers or the list of errors, so whoever
writes the prompt can read it (corpus/README.md's rule for held-out data). The dev report also
lists each error.

The labeled set, one JSON file (a provisional format: spec 0049 doesn't fix one):

    {"split": "dev" | "heldout",
     "documents": [{"kind": "ordinary" | "hard",
                    "document": <a document in the corpus page model, with its "id">,
                    "numbers": [{"line": <the line's index in the document>, "e164": "+1...",
                                 "role": "main" | "ar" | "billing" | "fax" | "unknown",
                                 "party": "vendor" | "customer" | "bank" | "unknown",
                                 "explicit_role": <a role word names it>, "us": <an "us" mention>}]}]}

The set must be complete and unique: every visible phone candidate code finds has exactly one label,
every label is such a candidate, and no document id appears twice. Both kinds must hold labeled
numbers, and the report counts numbers per kind. Held-out: 70
documents holding at least 30 numbers that aren't the vendor's, and its SHA-256 must match the one
committed in `reader_heldout.sha256` beside this script (`sha256sum` format; there is no command-line
hash, so the frozen set can't be swapped by typing another). Dev: 30 documents.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from math import comb
from pathlib import Path

from dialback_ledger.candidates import find_phones
from dialback_ledger.flags import ReaderMode, reader_mode
from dialback_ledger.grounding import ground
from dialback_ledger.reader import Reader, ReaderFailed, ReaderUnavailable, StubReader, reader_for
from dialback_ledger.text import lines, visible_text

DEV_LABELS = Path(__file__).with_name("reader_dev.json")   # the 30 dev documents, once someone writes them
HELDOUT_SHA256 = Path(__file__).with_name("reader_heldout.sha256")   # committed when the held-out set is frozen
SIZES = {"dev": 30, "heldout": 70}
MIN_NOT_VENDOR = 30                                        # held-out numbers that aren't the vendor's
HELDOUT_RUNS = 3                                           # the model's output varies
KINDS = ("ordinary", "hard")
ALPHA = Fraction(5, 100)
ROLES = {"main", "ar", "billing", "fax", "unknown"}
PARTIES = {"vendor", "customer", "bank", "unknown"}


class LabeledSetError(Exception):
    """The labeled set can't be scored as given."""


@dataclass(frozen=True)
class Outcome:
    key: tuple                 # (document id, line, e164)
    kind: str                  # ordinary | hard
    unsafe: bool
    over_block: bool
    role_correct: bool | None  # None unless a role word names the number
    us: bool
    truth: tuple[str, str] = ("unknown", "unknown")   # (role, party)
    got: tuple[str, str] = ("unknown", "unknown")


# Labeling, exactly as ingestion does it ----------------------------------------------------------

def read(document: dict, reader: Reader) -> dict[tuple[int, str], tuple[str, str]]:
    """(role, party) for each visible candidate, keyed by (line, e164): what ingest() would store
    (a candidate the reader skipped is (unknown, unknown)). Raises ReaderFailed."""
    ls = lines(document)
    cands = find_phones(ls)
    accepted, _ = ground(reader.label(visible_text(ls)), ls, cands)
    by = {g.candidate: (g.role, g.party) for g in accepted}
    return {(c.line, c.e164): by.get(c, ("unknown", "unknown")) for c in cands if c.visibility == "visible"}


def score(labeled: dict, reader: Reader) -> tuple[list[Outcome], int]:
    """Each labeled number's outcome under `reader`, and how many documents it failed on."""
    out, failed = [], 0
    for entry in labeled["documents"]:
        doc = entry["document"]
        try:
            got = read(doc, reader)
        except ReaderFailed:
            failed, got = failed + 1, {}
        for n in entry["numbers"]:
            role, party = got.get((n["line"], n["e164"]), ("unknown", "unknown"))
            presented_as_vendor = n["party"] == "vendor"
            out.append(Outcome(
                key=(doc["id"], n["line"], n["e164"]), kind=entry["kind"],
                unsafe=not presented_as_vendor and party == "vendor",
                over_block=presented_as_vendor and (party != "vendor" or (role == "fax" and n["role"] != "fax")),
                role_correct=(role == n["role"]) if n["explicit_role"] else None, us=n["us"],
                truth=(n["role"], n["party"]), got=(role, party)))
    return out, failed


def count(outcomes: Sequence[Outcome], failed: int) -> dict:
    explicit = [o for o in outcomes if o.role_correct is not None]
    us = [o for o in outcomes if o.us]
    return {"numbers": len(outcomes),
            "numbers_by_kind": {kind: sum(o.kind == kind for o in outcomes) for kind in KINDS},
            "unsafe": sum(o.unsafe for o in outcomes),
            "over_block": {kind: sum(o.over_block for o in outcomes if o.kind == kind) for kind in KINDS},
            "role_accuracy": {"correct": sum(bool(o.role_correct) for o in explicit), "of": len(explicit)},
            "us": {"numbers": len(us), "unsafe": sum(o.unsafe for o in us), "over_block": sum(o.over_block for o in us)},
            "failed_documents": failed}


# The bars ------------------------------------------------------------------------------------------

def _paired(llm: Sequence[Outcome], stub: Sequence[Outcome]) -> list[tuple[Outcome, Outcome]]:
    if [o.key for o in llm] != [o.key for o in stub]:
        raise ValueError("the two readers' outcomes don't pair number for number")
    return list(zip(llm, stub))


def new_unsafe(llm: Sequence[Outcome], stub: Sequence[Outcome]) -> list:
    """The numbers the LLM reader labels unsafely and the stub doesn't."""
    return [a.key for a, b in _paired(llm, stub) if a.unsafe and not b.unsafe]


def mcnemar_p(b: int, c: int) -> Fraction:
    """One-sided exact McNemar test that the LLM reader over-blocks less than the stub. `b`: pairs
    only the stub over-blocks; `c`: pairs only the LLM reader over-blocks. Under the null each
    discordant pair is a fair coin, so p = P(X >= b) for X ~ Binomial(b + c, 1/2)."""
    n = b + c
    return Fraction(sum(comb(n, k) for k in range(b, n + 1)), 2 ** n)


def mcnemar(llm: Sequence[Outcome], stub: Sequence[Outcome]) -> tuple[int, int, Fraction]:
    """(b, c, p) on the hard documents' paired over-block outcomes."""
    pairs = [(a, s) for a, s in _paired(llm, stub) if a.kind == "hard"]
    b = sum(s.over_block and not a.over_block for a, s in pairs)
    c = sum(a.over_block and not s.over_block for a, s in pairs)
    return b, c, mcnemar_p(b, c)


def _run_bars(llm: Sequence[Outcome], stub: Sequence[Outcome]) -> dict:
    unsafe = new_unsafe(llm, stub)
    b, c, p = mcnemar(llm, stub)
    ordinary = [sum(o.over_block for o in side if o.kind == "ordinary") for side in (llm, stub)]
    return {"no_new_unsafe": {"passed": not unsafe, "new": len(unsafe)},
            "fewer_over_blocks_on_hard": {"passed": p <= ALPHA, "b": b, "c": c, "p": float(p)},
            "no_regression_on_ordinary": {"passed": ordinary[0] <= ordinary[1], "llm": ordinary[0], "stub": ordinary[1]}}


def bars(llm_runs: Sequence[Sequence[Outcome]], stub: Sequence[Outcome]) -> dict:
    """Each bar on each run; passed only if there was a run and every bar holds on every run."""
    runs = [_run_bars(run, stub) for run in llm_runs]
    return {"runs": runs, "passed": bool(runs) and all(bar["passed"] for run in runs for bar in run.values())}


def check_runs(split: str, runs: int) -> None:
    """Held-out runs exactly HELDOUT_RUNS times; any split needs at least one run (review G11)."""
    if split == "heldout" and runs != HELDOUT_RUNS:
        raise ValueError(f"the held-out set runs exactly {HELDOUT_RUNS} times, not {runs}")
    if runs < 1:
        raise ValueError(f"at least one run is needed, not {runs}")


# The labeled set -----------------------------------------------------------------------------------

def check(labeled: dict, split: str) -> None:
    """Refuse a labeled set that can't be scored as spec 0049 says."""
    if labeled.get("split") != split:
        raise LabeledSetError(f"the file is the {labeled.get('split')!r} split, not {split!r}")
    docs = labeled.get("documents")
    if not isinstance(docs, list):
        raise LabeledSetError("the labeled set has no list of documents")
    seen: set = set()
    for entry in docs:
        doc_id = entry["document"]["id"]
        if doc_id in seen:
            raise LabeledSetError(f"{doc_id}: the document is listed twice")
        seen.add(doc_id)
        if entry.get("kind") not in KINDS:
            raise LabeledSetError(f"{doc_id}: kind must be ordinary or hard")
        found = {(c.line, c.e164) for c in find_phones(lines(entry["document"])) if c.visibility == "visible"}
        keys = [(n["line"], n["e164"]) for n in entry["numbers"]]
        repeated = sorted({k for k in keys if keys.count(k) > 1})
        if repeated:
            raise LabeledSetError(f"{doc_id}: line {repeated[0][0]} is labeled twice for the same number")
        unlabeled = sorted(found - set(keys))
        if unlabeled:     # complete: every visible number has a label (review G9)
            raise LabeledSetError(f"{doc_id}: line {unlabeled[0][0]} holds a visible phone number with no label")
        for n in entry["numbers"]:
            if (n["line"], n["e164"]) not in found:
                raise LabeledSetError(f"{doc_id}: line {n['line']} holds no visible candidate {n['e164']}")
            if n["role"] not in ROLES or n["party"] not in PARTIES or not isinstance(n["explicit_role"], bool) \
                    or not isinstance(n["us"], bool):
                raise LabeledSetError(f"{doc_id}: line {n['line']} has a label off spec 0049's lists")
    if len(docs) != SIZES[split]:
        raise LabeledSetError(f"the {split} split needs {SIZES[split]} documents, not {len(docs)}")
    for kind in KINDS:     # each bar needs numbers of its kind, or it passes by construction (review G14)
        if not any(entry["kind"] == kind and entry["numbers"] for entry in docs):
            raise LabeledSetError(f"the set holds no {kind} documents with labeled numbers; it needs both kinds")
    if split == "heldout":
        not_vendor = sum(n["party"] != "vendor" for entry in docs for n in entry["numbers"])
        if not_vendor < MIN_NOT_VENDOR:
            raise LabeledSetError(f"the held-out set holds {not_vendor} numbers that aren't the vendor's; "
                                  f"it needs at least {MIN_NOT_VENDOR}, or the unsafe count means little")


def load(path: Path, split: str, *, sha_file: Path | None = None) -> tuple[dict, str]:
    """The labeled set and its SHA-256. The held-out set must match the hash committed in `sha_file`
    (HELDOUT_SHA256 by default; `sha256sum` format), never one given on the command line."""
    if not path.exists():
        raise LabeledSetError(f"no labeled set at {path}: spec 0049's labeled set isn't written yet")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if split == "heldout":
        sha_file = sha_file or HELDOUT_SHA256
        if not sha_file.exists():
            raise LabeledSetError(f"no committed sha256 at {sha_file}: freeze the held-out set and commit its hash first")
        committed = (sha_file.read_text().split() or [""])[0].lower()
        if sha != committed:
            raise LabeledSetError(f"the held-out set's sha256 is {sha}, not the committed {committed or '(empty)'}")
    try:
        labeled = json.loads(raw)
        check(labeled, split)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise LabeledSetError(f"the labeled set is malformed ({type(exc).__name__}: {exc})") from exc
    return labeled, sha


# The report ------------------------------------------------------------------------------------------

def _errors(outcomes: Sequence[Outcome]) -> list[dict]:
    return [{"document": o.key[0], "line": o.key[1], "e164": o.key[2], "truth": list(o.truth), "got": list(o.got),
             "unsafe": o.unsafe, "over_block": o.over_block}
            for o in outcomes if o.unsafe or o.over_block or o.role_correct is False]


def evaluate(labeled: dict, llm: Reader, stub: Reader, *, runs: int, sha256: str | None = None) -> dict:
    """Score the stub once (it's deterministic) and the LLM reader `runs` times, pair them, and
    report. Held-out: totals only."""
    check_runs(labeled["split"], runs)
    stub_out, stub_failed = score(labeled, stub)
    llm_runs = [score(labeled, llm) for _ in range(runs)]
    verdict = bars([out for out, _ in llm_runs], stub_out)
    heldout = labeled["split"] == "heldout"
    report_runs = []
    for (out, failed), run_bars in zip(llm_runs, verdict["runs"]):
        run = {"llm": count(out, failed), "stub": count(stub_out, stub_failed), "bars": run_bars}
        if not heldout:
            run["errors"] = _errors(out)
        report_runs.append(run)
    return {"split": labeled["split"], "sha256": sha256, "llm_version": getattr(llm, "version", None),
            "stub_version": getattr(stub, "version", None), "runs": report_runs, "passed": verdict["passed"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=("dev", "heldout"), required=True)
    parser.add_argument("--labels", type=Path, help=f"the labeled set (dev default: {DEV_LABELS.name} beside this script)")
    parser.add_argument("--runs", type=int, help="LLM runs (heldout: exactly 3; dev: at least 1, default 1)")
    args = parser.parse_args(argv)
    if reader_mode() is not ReaderMode.LLM:
        print("refused: run with DIALBACK_READER=llm (this evaluation calls the model)", file=sys.stderr)
        return 2
    runs = args.runs if args.runs is not None else (HELDOUT_RUNS if args.split == "heldout" else 1)
    try:
        check_runs(args.split, runs)
        llm = reader_for(ReaderMode.LLM)
        path = args.labels or (DEV_LABELS if args.split == "dev" else None)
        if path is None:
            raise LabeledSetError("--labels is required for heldout: that set lives outside the repo")
        labeled, sha = load(path, args.split)
    except (ValueError, ReaderUnavailable, LabeledSetError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    report = evaluate(labeled, llm, StubReader(), runs=runs, sha256=sha)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
