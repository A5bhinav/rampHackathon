"""The speech guard (design, "How a call runs", step 5; R4).

Every line the model words is checked before it is spoken. A line that fails is replaced by a
fixed fallback line, so a fraudster on the call learns nothing:

- runs of three or more digits, written or spoken ("4821", "4 8 2 1", "four oh two one",
  "forty-eight twenty-one"), except a read-back of digits the callee just said, or the
  customer's AP callback number;
- the words "verified", "approved" and "match", in any form ("verification", "mismatch");
- amounts;
- bank names and routing words;
- the vendor's name, until the callee has named the vendor's company themselves (spec 0012, D2).

It errs toward blocking: a blocked line costs one fallback sentence, a leaked one can cost the
case. Fixed text from `script.py` is trusted and not guarded; the tests check it would pass.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .script import FALLBACK_LINE

DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19",
    "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90",
}
# Words that end a run of digits; any punctuation or space between digits keeps it going, except a
# colon, so a time like "3:30" is not a run. "and" between two digits keeps it going too ("48 and 21").
_TOKENS = re.compile(r"\d+|[a-z]+|:|[^\sa-z\d]")

_STATUS = re.compile(r"\b\w*(?:verif|approv|match)\w*\b")
_CURRENCY = re.compile(r"[$€£¥]")
_AMOUNT_WORDS = re.compile(r"\b(?:dollars?|cents?|usd|eur|euros?|pounds?|bucks|hundred|thousand|million|billion"
                           r"|grand)\b|\b\d+\s?[km]\b")
BANK_NAMES = (
    "chase", "jpmorgan", "jp morgan", "j p morgan", "bank of america", "bofa", "b of a", "wells fargo", "wells",
    "citibank", "citi", "us bank", "u s bank", "pnc", "truist", "capital one", "td bank", "bmo", "fifth third",
    "regions bank", "keybank", "key bank", "huntington", "m t bank", "citizens bank", "first republic",
    "silicon valley bank", "svb", "santander", "hsbc", "ally bank", "schwab", "goldman sachs", "navy federal",
    "usaa", "mercury", "chime", "routing", "iban", "swift", "aba number",
)
_COMPANY_SUFFIXES = frozenset({"llc", "inc", "incorporated", "co", "corp", "corporation", "ltd", "limited",
                               "company", "plc", "lp", "llp", "the"})
# A first word this common is not a name on its own ("Quick Ship Logistics" must not block "a quick question").
_COMMON_FIRST_WORDS = frozenset({
    "first", "american", "national", "united", "global", "general", "best", "new", "quick", "great", "good",
    "prime", "smart", "blue", "green", "red", "gold", "golden", "star", "city", "metro", "north", "south", "east",
    "west", "central", "pacific", "atlantic", "express", "rapid", "fast", "simple", "total", "true", "pure", "home",
    "office", "just", "peak", "summit", "core", "main", "open", "clear", "bright", "fresh", "big", "little", "royal",
    "premier", "superior", "standard", "modern", "classic", "direct", "allied", "advanced", "precision", "quality",
    "pro", "all", "one", "top", "key", "sure", "right", "able", "every", "better", "safe", "secure", "trust",
})


@dataclass(frozen=True)
class GuardContext:
    vendor_names: tuple[str, ...] = ()
    named_company: str = ""      # what the callee said when asked which company this is
    callee_last_turn: str = ""
    callback_number: str = ""
    customer_name: str = ""


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reasons: tuple[str, ...]


def _clean(text: str) -> str:
    """Lowercase, compatibility-normalized, with invisible format characters (zero-width spaces) removed."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf").lower()


def _words(text: str) -> str:
    """Lowercase words separated by single spaces, padded so phrases match on word boundaries."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", _clean(text))) + " "


words = _words


def digit_runs(text: str) -> list[str]:
    """Every run of digits in `text`, with spoken digits ("four", "forty") turned into digits."""
    tokens = _TOKENS.findall(_clean(text))
    runs, current = [], ""
    for i, token in enumerate(tokens):
        if token.isdigit():
            current += token
        elif token in DIGIT_WORDS:
            current += DIGIT_WORDS[token]
        elif token == "and" and current and i + 1 < len(tokens) and (
                tokens[i + 1].isdigit() or tokens[i + 1] in DIGIT_WORDS):
            continue
        elif token == ":" or token.isalpha():
            if current:
                runs.append(current)
            current = ""
        # any other punctuation between digits keeps the run going
    if current:
        runs.append(current)
    return runs


def _allowed_runs(ctx: GuardContext) -> set[str]:
    callee = digit_runs(ctx.callee_last_turn)
    allowed = set(callee) | {"".join(callee)}  # "4 8, um, 2 1" can be read back as 4821
    callback = "".join(ch for ch in ctx.callback_number if ch.isdigit())
    if callback:
        allowed.add(callback)
        if len(callback) == 11 and callback.startswith("1"):
            allowed.add(callback[1:])
    allowed.discard("")
    return allowed


def vendor_variants(names: tuple[str, ...]) -> set[str]:
    """The ways a vendor's name can be said: without its legal suffix, run together, its first two
    words, and its first word when that word is distinctive."""
    variants = set()
    for name in names:
        tokens = [t for t in re.findall(r"[a-z0-9]+", _clean(name)) if t not in _COMPANY_SUFFIXES]
        if not tokens:
            continue
        full = " ".join(tokens)
        if len(full) >= 2:
            variants.add(full)
        if len(tokens) > 1:
            variants.add("".join(tokens))
        if len(tokens) > 2:
            variants.add(" ".join(tokens[:2]))
        if len(tokens) > 1 and len(tokens[0]) >= 4 and tokens[0] not in _COMMON_FIRST_WORDS:
            variants.add(tokens[0])
    return variants


def _says_vendor(words: str, variants: set[str]) -> bool:
    compact = " " + words.replace(" ", "") + " "
    return any(f" {v} " in words or (" " not in v and len(v) >= 6 and v in compact) for v in variants)


def check(text: str, ctx: GuardContext) -> Verdict:
    reasons = []
    allowed = _allowed_runs(ctx)
    if any(len(run) >= 3 and run not in allowed for run in digit_runs(text)):
        reasons.append("digits")
    words = _words(text)
    customer = _words(ctx.customer_name).strip()
    if customer:  # the customer's own name ("Swift Freight") is always allowed
        words = words.replace(f" {customer} ", " ")
    if _STATUS.search(words):
        reasons.append("status_word")
    if _CURRENCY.search(_clean(text)) or _AMOUNT_WORDS.search(words):
        reasons.append("amount")
    if any(f" {bank} " in words for bank in BANK_NAMES):
        reasons.append("bank_name")
    variants = vendor_variants(ctx.vendor_names)
    callee_named_vendor = _says_vendor(_words(ctx.named_company), variants)
    if not callee_named_vendor and _says_vendor(words, variants):
        reasons.append("vendor_name")
    return Verdict(ok=not reasons, reasons=tuple(reasons))


def guard(text: str, ctx: GuardContext) -> tuple[str, Verdict]:
    """What to actually say: the line itself if it passes, otherwise the fixed fallback line."""
    verdict = check(text, ctx)
    return (text if verdict.ok else FALLBACK_LINE), verdict
