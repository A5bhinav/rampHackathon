"""The speech guard (design, "How a call runs", step 5; R4).

Every line the model words is checked before it is spoken. A line that fails is replaced by a
fixed fallback line, so a fraudster on the call learns nothing:

- runs of three or more digits, written or spoken ("4821", "4 8 2 1", "four eight two one"),
  except a read-back of digits the callee just said, or the customer's AP callback number;
- the words "verified", "approved" and "match", in any form ("verification", "mismatch");
- amounts;
- bank names and routing words;
- the vendor's name, until the callee has named the company themselves (spec 0012, D2).

Fixed text from `script.py` is trusted and not guarded; the tests check it would pass anyway.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .script import FALLBACK_LINE

DIGIT_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
               "seven": "7", "eight": "8", "nine": "9"}
# Characters that keep a run of digits going ("415-555", "4.8.2", "1,2,3"). Anything else ends it.
_JOINERS = frozenset("-.,/")
_TOKENS = re.compile(r"\d+|[a-z]+|\S")

_STATUS = re.compile(r"\b\w*(?:verif|approv|match)\w*\b")
_AMOUNT = re.compile(r"[$€£]|\b(?:dollars?|cents?|usd|bucks|thousand|million|grand)\b")
BANK_NAMES = (
    "chase", "jpmorgan", "jp morgan", "bank of america", "wells fargo", "citibank", "citi", "us bank", "u s bank",
    "pnc", "truist", "capital one", "td bank", "bmo", "fifth third", "regions bank", "keybank", "key bank",
    "huntington", "m t bank", "citizens bank", "first republic", "silicon valley bank", "svb", "santander", "hsbc",
    "ally bank", "schwab", "goldman sachs", "navy federal", "usaa", "mercury", "chime", "routing", "iban", "swift",
    "aba number",
)
_COMPANY_SUFFIXES = frozenset({"llc", "inc", "incorporated", "co", "corp", "corporation", "ltd", "limited",
                               "company", "plc", "lp", "llp", "the"})
_GENERIC_FIRST_WORDS = frozenset({"first", "american", "national", "united", "global", "general", "best", "new"})


@dataclass(frozen=True)
class GuardContext:
    vendor_names: tuple[str, ...] = ()
    company_named: bool = False
    callee_last_turn: str = ""
    callback_number: str = ""


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reasons: tuple[str, ...]


def _words(text: str) -> str:
    """Lowercase words separated by single spaces, padded so phrases match on word boundaries."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "


def digit_runs(text: str) -> list[str]:
    """Every run of digits in `text`, with spoken digits ("four") turned into digits."""
    runs, current = [], ""
    for token in _TOKENS.findall(text.lower()):
        if token.isdigit():
            current += token
        elif token in DIGIT_WORDS:
            current += DIGIT_WORDS[token]
        elif token in _JOINERS:
            continue
        else:
            if current:
                runs.append(current)
            current = ""
    if current:
        runs.append(current)
    return runs


def _allowed_runs(ctx: GuardContext) -> set[str]:
    allowed = set(digit_runs(ctx.callee_last_turn))
    callback = "".join(ch for ch in ctx.callback_number if ch.isdigit())
    if callback:
        allowed.add(callback)
        if len(callback) == 11 and callback.startswith("1"):
            allowed.add(callback[1:])
    return allowed


def vendor_variants(names: tuple[str, ...]) -> set[str]:
    """The vendor's name without its legal suffix, and its first word when it is distinctive."""
    variants = set()
    for name in names:
        tokens = [t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in _COMPANY_SUFFIXES]
        if not tokens:
            continue
        full = " ".join(tokens)
        if len(full) >= 3:
            variants.add(full)
        if len(tokens) > 1 and len(tokens[0]) >= 4 and tokens[0] not in _GENERIC_FIRST_WORDS:
            variants.add(tokens[0])
    return variants


def check(text: str, ctx: GuardContext) -> Verdict:
    reasons = []
    allowed = _allowed_runs(ctx)
    if any(len(run) >= 3 and run not in allowed for run in digit_runs(text)):
        reasons.append("digits")
    lowered = text.lower()
    if _STATUS.search(lowered):
        reasons.append("status_word")
    if _AMOUNT.search(lowered):
        reasons.append("amount")
    words = _words(text)
    if any(f" {bank} " in words for bank in BANK_NAMES):
        reasons.append("bank_name")
    if not ctx.company_named and any(f" {v} " in words for v in vendor_variants(ctx.vendor_names)):
        reasons.append("vendor_name")
    return Verdict(ok=not reasons, reasons=tuple(reasons))


def guard(text: str, ctx: GuardContext) -> tuple[str, Verdict]:
    """What to actually say: the line itself if it passes, otherwise the fixed fallback line."""
    verdict = check(text, ctx)
    return (text if verdict.ok else FALLBACK_LINE), verdict
