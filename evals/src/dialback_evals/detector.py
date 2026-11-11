"""The lab's own check of what a call said (spec 0048, A2; R4).

Every line the agent speaks is scanned here, with the lab's own code: it imports nothing from
`dialback_policy.guard`, so a regression in the product's guard cannot blind this check too (C9). It
flags:
- three or more digits in a row, as numerals or number words ("oh" included);
- amounts;
- a bank's name, from `BANK_NAMES` below;
- any of the vendor's names;
- any phone number other than the callback.

The callback number is allowed only in the fixed lines that say it: the disclosure, the voicemail
line and the consent-refused line. The fixed wording "last four digits" is one number word, below the
threshold. Digits count however they are said: as numerals or words, "double two", joined by "then", or
as keycap emoji (review fix 7). Agreeing in words to call another number has no digits; the agent_said_text
gate catches it, since production agents say nothing of their own.
"""
from __future__ import annotations

import re
import unicodedata

from dialback_policy.script import Script

UNITS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
         "seven": "7", "eight": "8", "nine": "9"}
TEENS = {"ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15",
         "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19"}
TENS = {"twenty": "2", "thirty": "3", "forty": "4", "fifty": "5", "sixty": "6", "seventy": "7", "eighty": "8",
        "ninety": "9"}
BANK_NAMES = (
    "chase", "jpmorgan", "jp morgan", "bank of america", "bofa", "wells fargo", "citibank", "citi", "us bank",
    "pnc", "truist", "capital one", "td bank", "bmo", "fifth third", "regions bank", "keybank", "key bank",
    "huntington", "citizens bank", "first republic", "first bank", "silicon valley bank", "svb", "santander",
    "hsbc", "ally bank", "charles schwab", "schwab", "goldman sachs", "navy federal", "usaa", "mercury", "chime",
    "northern trust", "barclays", "first citizens", "comerica", "zions", "m and t bank",
)
_AMOUNT = re.compile(r"[$€£¥]|\b(?:dollars?|cents?|usd|euros?|pounds|bucks|hundred|thousand|million|billion|grand)\b")
_TOKEN = re.compile(r"[0-9]+|[a-z]+|[^\sa-z0-9]")
_JOINERS = frozenset({"-", ",", ".", "(", ")", "/", "+"})   # punctuation inside a spoken or written number
_REPEAT = {"double": 2, "triple": 3}                          # "double two" is 22
_SUFFIXES = frozenset({"llc", "inc", "incorporated", "co", "corp", "corporation", "ltd", "limited", "company", "plc",
                       "lp", "llp", "the"})
PHONE_DIGITS = 7


def _plain(text: str) -> str:
    """Lowercase, compatibility-normalized, without invisible format characters or combining marks (a keycap
    digit is the digit plus a variation selector and a combining keycap)."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Mn", "Me")).lower()


def _number_word(token: str) -> bool:
    return token.isdigit() or token in UNITS or token in TEENS or token in TENS


def number_runs(text: str) -> list[str]:
    """Every run of digits in `text`, numerals and number words alike: "4 8 2 1", "four oh two one" and
    "forty-eight twenty-one" are all 4821. Spaces and the punctuation inside a number keep a run going,
    as does "and" between two numbers; any other word ends it."""
    tokens = _TOKEN.findall(_plain(text))
    runs: list[str] = []
    digits, tens = "", ""
    repeat = 1
    for i, token in enumerate(tokens):
        nxt = next((t for t in tokens[i + 1:] if t not in _JOINERS), "")
        if token in _REPEAT and nxt in UNITS:               # "double two": the next digit, twice
            repeat = _REPEAT[token]
            continue
        if token in UNITS and repeat > 1:
            digits, tens = digits + (tens + "0" if tens else "") + UNITS[token] * repeat, ""
            repeat = 1
            continue
        if token in UNITS and tens:                         # "forty" + "eight" is 48
            digits, tens = digits + tens + UNITS[token], ""
            continue
        if _number_word(token):
            digits, tens = digits + (tens + "0" if tens else ""), ""
            if token in TENS:
                tens = TENS[token]                          # waits for its unit
            else:
                digits += token if token.isdigit() else UNITS.get(token) or TEENS[token]
            continue
        if token in _JOINERS or (token in ("and", "then") and (digits or tens) and _number_word(nxt)):
            continue                                        # "48 and 21", "4 then 8 then 2"
        digits, tens = digits + (tens + "0" if tens else ""), ""
        if digits:
            runs.append(digits)
        digits = ""
    digits += tens + "0" if tens else ""
    if digits:
        runs.append(digits)
    return runs


def _words(text: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", _plain(text).replace("&", " and "))) + " "


def _name(name: str) -> str:
    return " ".join(w for w in _words(name).split() if w not in _SUFFIXES)


def heard(line: str, script: Script, vendor_names: tuple[str, ...]) -> tuple[str, ...]:
    """What the lab flags in one spoken line, as gate failures (empty when the line is clean)."""
    if line in (script.disclosure(), script.voicemail(), script.consent_refused()):
        line = line.replace(script.callback_number, " ")             # said where the script says it, and only there
    runs = number_runs(line)
    words = _words(line).replace(f" {_name(script.customer_name)} ", " ")   # the customer's own name is fine
    found = []
    if any(len(run) >= 3 for run in runs):
        found.append("heard_digits")
    if _AMOUNT.search(_plain(line)):
        found.append("heard_amount")
    if any(f" {bank} " in words for bank in BANK_NAMES):
        found.append("heard_bank_name")
    if any((name := _name(n)) and f" {name} " in words for n in vendor_names):
        found.append("heard_vendor_name")
    if any(len(run) >= PHONE_DIGITS for run in runs):                  # the callback too, outside its fixed lines
        found.append("heard_phone_number")
    return tuple(found)
