"""Tasks 6c and 6f: evaluate the email door's flagger on the labeled emails. Since Sami's decision 12 the model
alone decides (flag_email with a classifier; the word rule is not consulted).

A command-line script, not a test (pytest collects only test_*.py). It spends money and needs Anthropic
credentials, so a person runs it, one set at a time, as the spec's pre-registered procedure says:

    cd apps/core && uv run python tests/email_door/eval_flagger.py --model claude-haiku-5-5 --sets tuning dev

heldout2 is exploratory now (it needs `--confirm-heldout2` and a matching hash, and may be rerun). The sealed set
is heldout3: `--sets heldout3 --confirm-heldout3`, once per model, only after its hash checks out. Add
`--heldout2-revised` when any prompt revision was judged on heldout2: cr-04 and cr-10 then leave heldout3's bar
and are reported separately. Change requests whose note starts `ON-FILE-ONLY:` are reported separately on
heldout3 (the note's first characters are read to find them; no note text is ever printed or stored).
Only counts and file names are printed or written: never the key, an email's text, a subject, a note, a
vendor, or the model's quote.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from dialback_core.email_door import classifier as classifier_module
from dialback_core.email_door.classifier import (
    AnthropicClassifier,
    ChangeClassifier,
    ClassifierFailed,
    anthropic_client,
)
from dialback_core.email_door.masking import strip_nul
from dialback_core.email_door.rules import flag_email

FIXTURES = Path(__file__).parent / "fixtures"
OUT_DIR = Path(__file__).parent / "eval_results"
SETS = {"tuning": "tuning", "dev": "heldout", "heldout2": "heldout2", "heldout3": "heldout3", "heldout4": "heldout4"}   # set name -> fixture directory
DEFAULT_MODEL = "claude-haiku-5-5"
MODELS = ("claude-haiku-5-5", "claude-opus-5-5")
EXPECTED = {"tuning": (30, 62), "dev": (35, 65), "heldout2": (40, 70), "heldout3": (40, 70), "heldout4": (40, 70)}   # (change requests incl. cr-36, ordinary)
TELEMETRY_VAR = "ANTHROPIC_OPEN_TELEMETRY_TRACES_CONTENT_MODE"
WORKERS = 4
MAX_ORDINARY_FLAGGED = 3
CR_36 = "cr-36.json"   # reported separately on heldout2
ON_FILE_ONLY_HELDOUT3 = frozenset({"cr-26.json", "cr-27.json", "cr-31.json"})   # pinned: the set's ON-FILE-ONLY requests
ON_FILE_ONLY_HELDOUT4 = frozenset({"cr-38.json", "cr-39.json", "cr-40.json"})   # fixed in the pre-registration, before the set existed
CONFIRMED_SETS = ("heldout2", "heldout3", "heldout4")   # held back: each needs its own --confirm-<name> and a matching hash
REVISED_EXCLUDED = ("cr-04.json", "cr-10.json")   # leave heldout3's bar when heldout2 informed a prompt revision
ON_FILE_ONLY = "ON-FILE-ONLY:"   # a change request's note starts with this when only the on-file check can catch it
FLAGGER = "model"   # the flagger evaluated: the model alone decides (decision 12)
SEALED = ("heldout3", "heldout4")   # once per model, behind --confirm-<name>
PRICES_PER_MILLION = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.0, 20.0)}   # (input, output) USD


class EvalError(Exception):
    """A refusal to run; the message holds no email text."""


def read_set(directory: Path) -> tuple[dict[str, dict], str]:
    """Each fixture's subject, text and label, and whether its note starts with ON-FILE-ONLY: (a boolean; the
    note itself is never kept), with NUL bytes stripped as production does, plus the sha256 of the bytes read (the sealed-set check hashes
    exactly what is run)."""
    out, blob = {}, b""
    for path in sorted(directory.glob("*.json")):
        data = path.read_bytes()
        blob += data
        raw = json.loads(data)
        if raw["label"] not in ("change_request", "ordinary"):
            raise EvalError(f"{path.name} has a label that is neither change_request nor ordinary")
        out[path.name] = {"subject": strip_nul(raw["subject"]), "text": strip_nul(raw["text"]), "label": raw["label"],
                           "on_file_only": str(raw.get("note", "")).startswith(ON_FILE_ONLY)}
    return out, hashlib.sha256(blob).hexdigest()


def load_set(directory: Path) -> dict[str, dict]:
    return read_set(directory)[0]


def check_composition(name: str, emails: dict[str, dict], expected: tuple[int, int]) -> None:
    labels = [e["label"] for e in emails.values()]
    got = (labels.count("change_request"), labels.count("ordinary"))
    if got != expected or sum(got) != len(emails):
        raise EvalError(f"the {name} set holds {got[0]} change requests and {got[1]} ordinary emails, "
                        f"not the expected {expected[0]} and {expected[1]}")
    if name == "heldout2" and (CR_36 not in emails or emails[CR_36]["label"] != "change_request"):
        raise EvalError(f"{CR_36} is not among the heldout2 change requests")


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    prices = PRICES_PER_MILLION.get(model)
    if prices is None:
        return None
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


def percentile(values: list[float], pct: int) -> float | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ranked = sorted(values)
    rank = max(1, -(-pct * len(ranked) // 100))   # ceil(pct * n / 100)
    return ranked[rank - 1]


class TimedClassifier:
    """Wraps a classifier and records each call's latency (the rule-only emails make no call)."""

    def __init__(self, inner: ChangeClassifier) -> None:
        self.inner, self.latencies, self._lock = inner, [], threading.Lock()

    def classify(self, subject: str, text: str):
        start = time.perf_counter()
        try:
            return self.inner.classify(subject, text)
        finally:
            with self._lock:
                self.latencies.append(time.perf_counter() - start)


def evaluate_set(name: str, emails: dict[str, dict], classifier: ChangeClassifier, *, model: str,
                 workers: int = WORKERS, heldout2_revised: bool = False) -> dict:
    timed = TimedClassifier(classifier)

    def one(item: tuple[str, dict]) -> tuple[str, bool, str]:
        file, e = item
        try:
            decision = flag_email(e["subject"], e["text"], "", timed)
        except ClassifierFailed as exc:   # counted as not flagged
            return file, False, exc.reason
        except Exception as exc:   # noqa: BLE001  one bad email must not lose the set; the type name only
            return file, False, f"unexpected:{type(exc).__name__}"
        return file, decision.flagged, decision.by

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = {file: (flagged, by) for file, flagged, by in pool.map(one, sorted(emails.items()))}
    failed = {f: by for f, (flagged, by) in outcomes.items() if not flagged and by not in ("none", "rule", "model")}
    failures: dict[str, int] = {}
    for reason in failed.values():
        failures[reason] = failures.get(reason, 0) + 1

    def is_cr(f: str) -> bool:
        return emails[f]["label"] == "change_request"

    # On heldout2, cr-36 is reported on its own and left out of the change-request counts. On heldout3, the
    # ON-FILE-ONLY change requests are, and so are cr-04 and cr-10 when heldout2 informed a prompt revision.
    separate = {CR_36} if name == "heldout2" and CR_36 in emails else set()
    on_file_only = {f for f in emails if name in SEALED and is_cr(f) and emails[f]["on_file_only"]}
    revised = {f for f in REVISED_EXCLUDED if name == "heldout3" and heldout2_revised and f in emails and is_cr(f)}
    separate |= on_file_only | revised
    crs = [f for f in sorted(emails) if is_cr(f) and f not in separate]
    ords = [f for f in sorted(emails) if not is_cr(f)]
    assert len(crs) + len(ords) + len(separate) == len(emails)
    flagged = lambda f: outcomes[f][0] 
    by = lambda f, who: outcomes[f] == (True, who) 
    in_tokens, out_tokens = getattr(classifier, "input_tokens", 0), getattr(classifier, "output_tokens", 0)
    result = {
        "set": name, "model": model, "flagger": FLAGGER,
        # read from the classifier module now, not copied, so a result names the prompt that produced it
        "prompt_version": classifier_module.PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(classifier_module.SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "emails": len(emails),
        "change_requests_total": len(crs), "change_requests_flagged": sum(map(flagged, crs)),
        "change_requests_by_model": sum(by(f, "model") for f in crs),
        "ordinary_total": len(ords), "ordinary_flagged": sum(map(flagged, ords)),
        "ordinary_by_model": sum(by(f, "model") for f in ords),
        "failures": dict(sorted(failures.items())), "failed": dict(sorted(failed.items())),
        "missed": [f for f in crs if not flagged(f)], "false_flags": [f for f in ords if flagged(f)],
        "calls": getattr(classifier, "calls", 0), "input_tokens": in_tokens, "output_tokens": out_tokens,
        "cost_usd": cost_usd(model, in_tokens, out_tokens),
        "latency_p50_s": percentile(timed.latencies, 50), "latency_p95_s": percentile(timed.latencies, 95),
    }
    if name == "heldout2":
        cr36_flagged = bool(separate) and flagged(CR_36)
        ok_ordinary = result["ordinary_flagged"] <= MAX_ORDINARY_FLAGGED
        without = not result["missed"] and ok_ordinary
        result["cr_36"] = {"flagged": cr36_flagged}
        result["bar_without_cr_36"] = "PASS" if without else "FAIL"          # the pre-registered bar
        result["bar_with_cr_36"] = "PASS" if without and cr36_flagged else "FAIL"
    if name in SEALED:
        ok_ordinary = result["ordinary_flagged"] <= MAX_ORDINARY_FLAGGED
        without = not result["missed"] and ok_ordinary
        result["on_file_only"] = {f: flagged(f) for f in sorted(on_file_only)}
        result["heldout2_revised"] = heldout2_revised
        result["excluded_after_heldout2_revision"] = {f: flagged(f) for f in sorted(revised)}
        result["bar_without_on_file_only"] = "PASS" if without else "FAIL"   # the pre-registered bar
        result["bar_with_on_file_only"] = "PASS" if without and all(flagged(f) for f in on_file_only) else "FAIL"
    return result


def table(r: dict) -> str:
    cost = "unknown" if r["cost_usd"] is None else f"${r['cost_usd']:.4f}"
    lat = lambda v: "n/a" if v is None else f"{v:.2f}s" 
    lines = [
        f"== {r['set']} ({r['model']}, flagger: {r['flagger']}, prompt {r['prompt_version']}) ==",
        f"change requests flagged {r['change_requests_flagged']}/{r['change_requests_total']}",
        f"ordinary flagged        {r['ordinary_flagged']}/{r['ordinary_total']}",
        f"failures (counted as not flagged): {r['failures'] or 'none'}",
        f"failed by file: {r['failed'] or 'none'}",
        f"missed: {r['missed'] or 'none'}",
        f"falsely flagged: {r['false_flags'] or 'none'}",
        (f"tokens in/out {r['input_tokens']}/{r['output_tokens']} over {r['calls']} calls, cost {cost}, "
         f"model-call latency p50 {lat(r['latency_p50_s'])} p95 {lat(r['latency_p95_s'])}"),
    ]
    if "cr_36" in r:
        lines += [f"{CR_36} (separate): {'flagged' if r['cr_36']['flagged'] else 'NOT flagged'}",
                  f"bar without {CR_36} (the pre-registered bar): {r['bar_without_cr_36']}",
                  f"bar with {CR_36}: {r['bar_with_cr_36']}"]
    if "on_file_only" in r:
        separate = {**r["on_file_only"], **r["excluded_after_heldout2_revision"]}
        lines += [f"ON-FILE-ONLY (separate): flagged {sum(r['on_file_only'].values())}/{len(r['on_file_only'])}: {r['on_file_only'] or 'none'}"]
        if r["set"] == "heldout3":
            lines += [f"cr-04 and cr-10 excluded after a heldout2 revision: {r['excluded_after_heldout2_revision'] or 'none'}"]
        lines += [f"all change requests: {r['change_requests_flagged'] + sum(separate.values())}/{r['change_requests_total'] + len(separate)}",
                  f"bar without the ON-FILE-ONLY ones (the pre-registered bar): {r['bar_without_on_file_only']}",
                  f"bar with them: {r['bar_with_on_file_only']}"]
    return "\n".join(lines)


def build_classifier(model: str) -> AnthropicClassifier:
    """The app's client (pinned base URL and timeout), with the key from ANTHROPIC_API_KEY."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise EvalError("ANTHROPIC_API_KEY is not set")
    return AnthropicClassifier(anthropic_client(key), model=model)


def main(argv: list[str] | None = None, *, fixtures: Path = FIXTURES, out_dir: Path = OUT_DIR,
         make_classifier=build_classifier, now: datetime | None = None, expected: dict | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL)
    parser.add_argument("--sets", nargs="+", choices=sorted(SETS), default=["tuning", "dev"])
    parser.add_argument("--confirm-heldout2", action="store_true")
    parser.add_argument("--confirm-heldout3", action="store_true")
    parser.add_argument("--confirm-heldout4", action="store_true")
    parser.add_argument("--heldout2-revised", action="store_true",
                        help="a prompt revision was judged on heldout2: cr-04 and cr-10 leave heldout3's bar")
    args = parser.parse_args(argv)
    sets = list(dict.fromkeys(args.sets))
    expected = expected or EXPECTED
    now = now or datetime.now(UTC)
    safe_model = "".join(c if c.isalnum() or c in ".-" else "_" for c in args.model)
    try:
        if os.environ.get(TELEMETRY_VAR, "") not in ("", "metadata"):
            raise EvalError(f"{TELEMETRY_VAR} would put email text in trace spans; unset it (or use metadata)")
        loaded = {}
        for name in sets:
            if name in CONFIRMED_SETS and not getattr(args, f"confirm_{name}"):
                raise EvalError(f"{name} is held back: pass --confirm-{name} to run it")
            if name in SEALED and list(out_dir.glob(f"*-{safe_model}-{name}.json")):
                raise EvalError(f"a {name} result for {args.model} already exists in {out_dir.name}; it runs once")
            emails, digest = read_set(fixtures / SETS[name])
            if name in CONFIRMED_SETS and digest != (fixtures / f"{name}.sha256").read_text().split()[0]:
                raise EvalError(f"the {name} hash does not match {name}.sha256; refusing to run")
            check_composition(name, emails, expected[name])
            if name in SEALED:
                pinned = {"heldout3": ON_FILE_ONLY_HELDOUT3, "heldout4": ON_FILE_ONLY_HELDOUT4}[name]
                derived = frozenset(f for f, e in emails.items() if e["label"] == "change_request" and e["on_file_only"])
                if derived != pinned:
                    raise EvalError(f"the {name} ON-FILE-ONLY change requests are not the pinned three "
                                    f"({', '.join(sorted(pinned))}); refusing to run")
            loaded[name] = emails
        classifier = make_classifier(args.model)
    except EvalError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in sets:
        # Totals are per classifier instance, so each set's usage is the difference.
        before = (getattr(classifier, "input_tokens", 0), getattr(classifier, "output_tokens", 0), getattr(classifier, "calls", 0))
        result = evaluate_set(name, loaded[name], classifier, model=args.model, heldout2_revised=args.heldout2_revised)
        result["input_tokens"] -= before[0]
        result["output_tokens"] -= before[1]
        result["calls"] -= before[2]
        result["cost_usd"] = cost_usd(args.model, result["input_tokens"], result["output_tokens"])
        print(table(result))   # shown first, so a paid result is never lost to a file error
        try:
            with open(out_dir / f"{stamp}-{safe_model}-{name}.json", "x") as f:   # never overwrite
                f.write(json.dumps(result, indent=2) + "\n")
        except FileExistsError:
            print(f"the result file for {name} exists; not overwriting it", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
