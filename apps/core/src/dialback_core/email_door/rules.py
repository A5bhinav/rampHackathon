"""Flagging, bank-detail extraction and redaction for the email door (spec 0086; design sections 3 and 4).

Email text is data, never instructions (R9): these functions only read it. `find_accounts` is the only
function here that returns a full account number, and nothing here logs (ADR-8).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass

from dialback_ledger.candidates import find_phones
from dialback_ledger.text import Line

from ..bank import aba_valid
from .masking import DIGIT_RUN, SEPARATOR, mask_digit_runs, mask_run

# Flagging ------------------------------------------------------------------------------------------
#
# An email is flagged when, in one sentence of its subject or body, a change word sits near a word for
# where money goes. "Near" has three shapes: a change verb within WINDOW words of the destination word
# (either side); a change adjective just before it ("new bank", "different account"); or the
# destination word followed by a correction ("the account is out of date", "our account was closed").
# Since revision 1, a core change verb ("changed", "update") also pairs with a destination word
# anywhere in the email. A change word does not count when it is negated ("never change") or about
# requests in general ("bank change requests"), nor a core change verb that is conditional ("if we ever
# need to change") or about a process ("how to change", "for changes to"), and nothing counts in a
# sentence about fraudsters or phishing, which describes requests instead of making one. A destination
# word does not count after "your" (the reader's own payment method or account, not the vendor's) or
# before "remains the same". Spec 0086 lists every version of this rule and its numbers.

WINDOW = 8
FLAG_READS = 50_000  # flag() reads at most this many characters, subject first, so its time stays bounded


def _terms(*alternatives: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9'])(?:" + "|".join(alternatives) + r")(?![a-z0-9'])")


# Where money goes. Bare "ACH", "wire" and "payment" name a payment, not a destination ("the ACH was
# late"). Bare "remittance" can be either, so it pairs only with a change verb ("Remittance update"),
# never with an adjective or a correction ("the new remittance arrived").
_DEST = _terms(
    r"(?:payments?|bank|banking|ach|wire|remit|remittance|deposit|transfer|payee) "
    r"(?:details?|info|information|instructions?|methods?|accounts?|address|destination|coordinates|bank|particulars)",
    r"payees?",
    r"remit to", r"remittances?(?! (?:advices?|slips?|stubs?)(?![a-z]))", r"direct deposit", r"sort code",
    r"(?:mailing |remit |payment )?address for (?:checks|cheques|payments|remittances?)",
    r"banks?(?! (?:statements?|holidays?|fees?|charges?)(?![a-z]))", r"bankers?", r"(?<!online )(?<!mobile )(?<!internet )banking",
    # "Routing" alone is also a shipment's route, and "swift" an adjective.
    r"routing(?= (?:numbers?|nos?|num|nbr|codes?|transit|and|accounts?|aba|[0-9:]))", r"swift(?: codes?| bic| numbers?|(?= :))",
    r"bic codes?", r"aba", r"iban", r"beneficiary", r"lockbox", r"accts?", r"credit unions?", r"financial institutions?",
    r"(?<!customer )(?<!user )(?<!online )(?<!portal )(?<!email )(?<!login )(?<!web )"
    r"accounts?(?! (?:manager|managers|statements?|payable|receivable|team|contacts?|reps?|representative|executive"
    r"|settings|password|login|portal|balance|summary|history|activity)(?![a-z]))",
)
_DETERMINERS = {"our", "the", "a", "an", "this", "that", "my", "their", "its"}
_CHANGE_VERB = _terms(
    r"changes?", r"changed", r"changing",
    r"updates?(?! (?:me|you|us|him|her|them)(?![a-z]))", r"(?<!you )(?<!me )(?<!us )(?<!them )updated", r"updating",
    r"switch(?:es|ed|ing)?", r"replac(?:e|es|ed|ing|ement)", r"mov(?:e|es|ed|ing)(?! forward)", r"modif(?:y|ies|ied|ying)",
    r"amend(?:s|ed|ing|ment)?", r"alter(?:s|ed|ing|ation)?", r"revis(?:e|es|ed|ing|ion)",
    r"transferr(?:ed|ing)", r"relocat(?:e|es|ed|ing)", r"rerout(?:e|es|ed|ing)", r"merg(?:e|es|ed|er|ing)",
    r"acquir(?:ed|ing)", r"acquisition", r"opened", r"anymore", r"any more", r"any longer",
    r"redirect(?:s|ed|ing)?", r"migrat(?:e|es|ed|ing)", r"transition(?:s|ed|ing)?", r"consolidat(?:e|es|ed|ing)",
    r"restructured", r"disregard", r"ignore", r"no longer", r"instead", r"from now on", r"going forward",
    r"moving forward", r"henceforth", r"effective", r"from today", r"as of today", r"starting today",
    r"corrected", r"correction",
)
# Not the first word of a name: New York Community Bank, Old National Bank.
_CHANGE_ADJECTIVE = _terms(r"new(?! (?:york|jersey|mexico|england|hampshire|orleans|haven|zealand|brunswick)(?![a-z]))",
                           r"different", r"old(?! (?:national|second|dominion)(?![a-z]))", r"previous", r"former",
                           r"another", r"alternate", r"alternative", r"revised", r"updated")
_CORRECTION = _terms(r"wrong", r"incorrect", r"typo", r"outdated", r"out of date", r"invalid", r"not correct", r"different",
                     r"errors?", r"mistakes?", r"misprint(?:ed)?", r"erroneous", r"inaccurate")
# Revision 1 (after the held-out run of version 5).
# A core change verb anywhere in the email pairs with a destination word anywhere in it: the change is
# often announced in one sentence and the details given in the next ("our bank has changed. Bank: ...").
# Matched against a whole term with fullmatch, so the time phrase "moving forward" is not the verb "move".
_CORE_VERB = re.compile(r"(?:chang|updat|switch|replac|mov|redirect|modif|migrat|transition|amend|alter|revis"
                        r"|transferr|relocat|rerout|merg|acquir|opened)[a-z]*")
_CARRIES_ITS_NEGATION = {"anymore", "any more", "any longer", "no longer"}   # "we don't use that account anymore"
# A change adjective on a generic noun ("new details", "updated instructions").
_GENERIC = _terms(r"details?", r"instructions?", r"information", r"info", r"particulars")
# A time word with a bare payment word ("all future payments", "payments from now on").
_PAYMENT = _terms(r"payments", r"remittances", r"funds")
_FROM_NOW = _terms(r"future", r"from now on", r"going forward", r"moving forward", r"henceforth", r"from today",
                   r"as of today", r"starting today")
_NOUN_BEFORE = {"a", "an", "the", "any", "this", "that"}   # "any update?" is news, not a change
# An account that stops working ("our account at First National is frozen"). Accounts only: a bank is
# closed on a holiday.
_STOPPED = _terms(r"closed", r"closing", r"frozen", r"compromised", r"suspended", r"discontinued")
_AWARENESS = _terms(r"fraudsters?", r"scammers?", r"criminals?", r"phishing", r"scams?", r"impersonat[a-z]*",
                    r"email compromise")

_NEGATORS = {"not", "never", "no", "nothing", "nor", "without", "cannot"}
_STILL_ASKING = {"forget", "hesitate", "delay", "fail", "miss", "wait", "longer"}   # "don't forget to update ..."
_CONDITIONAL = {"if", "whenever", "unless"}
_PROCESS = {"how", "procedure", "policy", "steps"}
_PROCESS_NOUN = re.compile(r"(?<![a-z])(?:process|way) (?:for|to|of)(?![a-z])")   # not "please process our ..."
_SAME = {"remain", "remains", "stay", "stays", "unchanged", "same"}
_CLAUSE_BREAK = {",", ":", "(", ")"}

_INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f\u2066-\u2069]")
_QUOTE_MARKS = re.compile(r"^[ \t]*(?:>[ \t]?)+", re.MULTILINE)
_SENTENCE_END = re.compile(r"[.!?;]+(?=\s|$)|\n[ \t]*\n")
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?|[,:()]")


@dataclass(frozen=True)
class Flag:
    flagged: bool
    reasons: tuple[str, ...]


def flag(subject: str, text: str) -> Flag:
    """Whether the email asks to change where a vendor's payments go, with the word pairs that say so.
    Reads the first FLAG_READS characters of the subject and then the text."""
    subject = (subject or "")[:FLAG_READS]
    text = (text or "")[:FLAG_READS - len(subject)]
    reasons: dict[str, None] = {}
    verbs: list[str] = []
    dests: list[str] = []
    for where, part in (("subject", subject), ("body", text)):
        for sentence in _sentences(part):
            sentence_reasons, sentence_verbs, sentence_dests = _sentence_reasons(sentence)
            reasons |= dict.fromkeys(f"{where}: {reason}" for reason in sentence_reasons)
            verbs += sentence_verbs
            dests += sentence_dests
    if verbs and dests and not reasons:
        reasons[f"email: '{verbs[0]}' and '{dests[0]}' in different sentences"] = None
    return Flag(bool(reasons), tuple(reasons))


def _sentences(text: str) -> list[list[str]]:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text).replace("\u2019", "'").replace("\u2018", "'")
    text = _QUOTE_MARKS.sub("", text).lower()   # a forwarded or replied-to original reads like any other text
    return [tokens for part in _SENTENCE_END.split(text) if (tokens := _TOKEN.findall(part))]


class _Sentence:
    """A sentence's tokens, joined by single spaces, with each token's offset in the joined text, so a
    match maps to its tokens by binary search instead of by counting spaces from the start."""

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.joined = " ".join(tokens)
        self.starts: list[int] = []
        offset = 0
        for t in tokens:
            self.starts.append(offset)
            offset += len(t) + 1

    def hits(self, pattern: re.Pattern[str]) -> list[tuple[int, int, str]]:
        """(first token, last token, words) for each match of `pattern`, in order."""
        out = []
        for m in pattern.finditer(self.joined):
            first = bisect_right(self.starts, m.start()) - 1
            out.append((first, first + m.group(0).count(" "), m.group(0)))
        return out


def _near(hits: list[tuple[int, int, str]], firsts: list[int], low: int, high: int) -> list[tuple[int, int, str]]:
    """The hits whose first token is between `low` and `high`, in order (`firsts` is sorted)."""
    return hits[bisect_left(firsts, low):bisect_right(firsts, high)]


def _before(tokens: list[str], i: int, n: int) -> list[str]:
    """Up to `n` tokens before token `i`, nearest first, stopping at a comma or colon."""
    out = []
    for t in reversed(tokens[max(0, i - n):i]):
        if t in _CLAUSE_BREAK:
            break
        out.append(t)
    return out


def _negated(tokens: list[str], i: int, n: int) -> bool:
    before = _before(tokens, i, n)
    for k, t in enumerate(before):
        if t in _NEGATORS or t.endswith("n't"):
            following = before[k - 1] if k > 0 else tokens[i]
            if following not in _STILL_ASKING:
                return True
    return False


def _after(tokens: list[str], j: int, n: int) -> list[str]:
    """Up to `n` tokens after token `j`, stopping at a comma or colon."""
    out = []
    for t in tokens[j + 1:j + 1 + n]:
        if t in _CLAUSE_BREAK:
            break
        out.append(t)
    return out


def _not_a_request(tokens: list[str], i: int, j: int) -> bool:
    """A change word (tokens i to j) that is negated or about requests in general asks for nothing; so
    does a core change verb that is conditional or about a process."""
    word = " ".join(tokens[i:j + 1])
    if word not in _CARRIES_ITS_NEGATION and _negated(tokens, i, 4) or _after(tokens, j, 1) == ["requests"]:
        return True
    if not _CORE_VERB.fullmatch(word):
        return False
    before = _before(tokens, i, WINDOW)
    clause = " ".join(reversed(before))
    if _CONDITIONAL & set(before) or "in case" in clause or "in the event" in clause:
        return True
    if _PROCESS & set(before) or _PROCESS_NOUN.search(clause):
        return True
    return before[:1] == ["for"] or before[:2] in (["any", "for"], ["all", "for"])


def _readers_own(tokens: list[str], i: int) -> bool:
    """Whether the destination word at token `i` belongs to "your" ("update your payment method")."""
    for t in _before(tokens, i, 3):
        if t == "your":
            return True
        if t in _DETERMINERS:
            return False
    return False


def _sentence_reasons(tokens: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(reasons in this sentence, core change verbs that ask for something, destination words).
    Each pairing looks only at the hits whose first token falls in its window, found by binary search,
    so a long sentence costs about its length, not its length squared."""
    sentence = _Sentence(tokens)
    if sentence.hits(_AWARENESS):
        return [], [], []
    dests = [d for d in sentence.hits(_DEST) if not _readers_own(tokens, d[0]) and not _SAME & set(_after(tokens, d[1], 3))]
    verbs = [v for v in sentence.hits(_CHANGE_VERB) if not _not_a_request(tokens, v[0], v[1])]
    core = [w for a, _, w in verbs if _CORE_VERB.fullmatch(w) and (a == 0 or tokens[a - 1] not in _NOUN_BEFORE)]
    adjectives = [h for h in sentence.hits(_CHANGE_ADJECTIVE) if not _negated(tokens, h[0], 1)]
    generic = sentence.hits(_GENERIC)
    payments = sentence.hits(_PAYMENT)
    out = []
    generic_firsts = [h[0] for h in generic]
    for a, b, word in adjectives:
        out += [f"'{word} {g}'" for c, e, g in _near(generic, generic_firsts, b + 1, b + 2)]
    payment_firsts, payment_span = [h[0] for h in payments], max((e - c for c, e, _ in payments), default=0)
    for a, b, word in sentence.hits(_FROM_NOW):
        out += [f"'{word}' near '{p}'" for c, e, p in _near(payments, payment_firsts, a - WINDOW - payment_span, b + WINDOW)
                if max(0, c - b, a - e) <= WINDOW]
    if not dests:
        return out, core, []
    nouns = [d for d in dests if d[2] not in ("remittance", "remittances")]
    dest_firsts = [d[0] for d in dests]
    noun_firsts = [d[0] for d in nouns]
    span = max(e - c for c, e, _ in dests)   # a hit's last token is at most `span` after its first
    for a, b, word in verbs:
        out += [f"'{word}' near '{d}'" for c, e, d in _near(dests, dest_firsts, a - WINDOW - span, b + WINDOW)
                if max(0, c - b, a - e) <= WINDOW]
    for a, b, word in adjectives:
        out += [f"'{word} {d}'" for c, e, d in _near(nouns, noun_firsts, b + 1, b + 3)
                if not _CLAUSE_BREAK & set(tokens[b + 1:c])]
    for a, b, word in sentence.hits(_CORRECTION):
        out += [f"'{d}' then '{word}'" for c, e, d in _near(nouns, noun_firsts, a - WINDOW - span, a - 1) if 0 < a - e <= WINDOW]
    for a, b, word in sentence.hits(_STOPPED):
        out += [f"'{d}' then '{word}'" for c, e, d in _near(nouns, noun_firsts, a - WINDOW - span, a - 1)
                if 0 < a - e <= WINDOW and "acc" in d]
    return out, core, [d for _, _, d in dests]


# Bank details --------------------------------------------------------------------------------------
#
# A routing number is a run of exactly nine digits with a valid ABA checksum. An account number is the
# first run of 4 to 17 digits that starts within 40 characters after an account label, with only label
# words between ("Account number:", "Acct #", "our account is"). "The account ending 8888" is a
# reference to the last four, not an account. A run is digits grouped by masking.py's separators, so
# "0001 2345 4821", "0001-2345-4821" and a number split over lines are each one run.
#
# Redaction is the ADR-8 barrier for the ledger and the model. Beyond the labeled accounts it treats
# as an account (so masks, and never reports as a routing number): any digits that repeat a found
# account, however grouped; a run whose nearest label on its line is an account label ("Our new acct at
# Evergreen: 026009593"); and a phone-shaped number on a line that has a payment word and no phone word
# ("wire to 7783110245"). A phone extension of 5 or more digits is masked like any other run.

_NOT_AN_ACCOUNT = (r"(?!\s+(?:manager|managers|statements?|payable|receivable|team|contacts?|reps?|representative|executive"
                   r"|settings|password|login|portal|balance|summary|history|activity)\b)")
_LABEL_WORD = r"(?:number|num|nbr|no|is|in|our|the|new|checking|savings|operating|will|be|now)\b"
_ACCOUNT = re.compile(
    rf"(?<![a-z0-9])(?:accounts?|accts?|a/c)\b{_NOT_AN_ACCOUNT}(?P<gap>(?:{SEPARATOR}|[:#()=]|{_LABEL_WORD})*)"
    rf"(?P<run>{DIGIT_RUN.pattern})", re.IGNORECASE)
LABEL_REACH = 40
_ACCOUNT_LABEL = rf"(?<![a-z0-9])(?:accounts?|accts?|a/c)(?![a-z0-9]){_NOT_AN_ACCOUNT}"
_ROUTING_LABEL = r"(?<![a-z0-9])(?:routing|aba|rtn|transit|swift|iban|sort code)(?![a-z0-9])"
_PHONE_WORD = r"(?<![a-z0-9])(?:call[a-z]*|phones?|telephone|tel|mobile|cell|fax|contact[a-z]*|reach|text|ext)(?![a-z0-9])"
_PAYMENT_WORD = re.compile(r"(?<![a-z0-9])(?:accounts?|accts?|routing|aba|wires?|wired|wiring|ach|remit[a-z]*|pay[a-z]*"
                           r"|bank[a-z]*|iban|deposits?|beneficiar[a-z]*)(?![a-z0-9])", re.IGNORECASE)
_PHONE_LINE = re.compile(_PHONE_WORD, re.IGNORECASE)
_NEAREST_LABEL = re.compile(rf"(?P<account>{_ACCOUNT_LABEL})|(?P<routing>{_ROUTING_LABEL})|(?P<phone>{_PHONE_WORD})",
                            re.IGNORECASE)


def find_routing(text: str) -> list[str]:
    """Every routing number in `text` (nine digits, valid ABA checksum, not taken for an account), in
    order, once each."""
    return _unique(_ascii(text[s:e]) for s, e, kind in _pieces(text) if kind == "routing")


def find_accounts(text: str) -> list[str]:
    """Every labeled account number in `text`, digits only, in order, once each. The only function that
    returns a full account number: use it in memory and drop it (ADR-8)."""
    return _unique(digits for _, _, digits in _accounts(text))


def redact(text: str, also_mask: Iterable[str] = ()) -> str:
    """The ledger's copy of `text`: every account number (see above) and every other run of 5 or more
    digits masked to its last four, separators kept. Phone numbers and routing numbers stay, so the
    ledger can still read them; its stub reader stores a phone's whole line, so nothing else on that
    line may stay whole. `also_mask` holds accounts found in another field (the body's, when redacting
    the subject), masked wherever their digits appear."""
    out = list(text)
    for s, e, kind in _pieces(text, also_mask):
        if kind in ("account", "other"):
            out[s:e] = mask_run(text[s:e])
    return "".join(out)


def redact_for_model(text: str, also_mask: Iterable[str] = ()) -> str:
    """The model's copy of `text`: `redact`, then every run of 5 or more digits masked, phones and
    routing numbers included. The model needs none of them."""
    return mask_digit_runs(redact(text, also_mask))


def requested_fingerprint(key: bytes, routing: str, account: str) -> str:
    """ADR-8's keyed fingerprint of a requested account: HMAC-SHA256 over `routing|account`."""
    return hmac.new(key, f"{routing}|{account}".encode(), hashlib.sha256).hexdigest()


def _accounts(text: str) -> list[tuple[int, int, str]]:
    out = []
    for m in _ACCOUNT.finditer(text):
        digits = _ascii(m.group("run"))
        if len(m.group("gap")) <= LABEL_REACH and 4 <= len(digits) <= 17:
            out.append((m.start("run"), m.end("run"), digits))
    return out


def _phones(text: str) -> list[tuple[int, int]]:
    """The phone numbers the ledger needs, found the way it finds them: line by line, with parse_phone's
    matcher. A phone-shaped number on a line with a payment word and no phone word is left out (it is
    taken for an account), and a span stops before an extension, which is masked like other digits."""
    out, offset = [], 0
    for i, line in enumerate(text.splitlines(keepends=True)):
        if not (_PAYMENT_WORD.search(line) and not _PHONE_LINE.search(line)):
            out += [(offset + c.start, offset + _without_extension(line, c)) for c in find_phones([Line(line, "visible", 0, i)])]
        offset += len(line)
    return out


def _without_extension(line: str, candidate) -> int:
    """Where the phone number itself ends in `line`: before any extension digits after it."""
    digits = _ascii(line[candidate.start:candidate.end])
    e164 = candidate.e164.lstrip("+")
    for skip in range(4):   # the matched digits may start with the country code, or without it
        if digits.startswith(e164[skip:]):
            keep, end = len(e164) - skip, candidate.start
            while keep:
                keep -= line[end].isdecimal()
                end += 1
            return end
    return candidate.end


def _pieces(text: str, also_mask: Iterable[str] = ()) -> list[tuple[int, int, str]]:
    """The digits of `text` as (start, end, kind) pieces: each labeled `account`, then every digit run
    with those accounts and the ledger's phone numbers cut out of it. A remaining piece is an `account`
    when it repeats a found account or its nearest label is an account label, else a `routing` number,
    else `other`."""
    accounts = _accounts(text)
    secrets = {d for _, _, d in accounts} | {_ascii(a) for a in also_mask}
    secrets = {d for d in secrets if len(d) >= 5}   # four digits are the last four ADR-8 keeps anyway

    def repeats_an_account(span: str) -> bool:
        digits = _ascii(span)
        return any(secret in digits for secret in secrets)

    taken = bytearray(len(text))
    for s, e in [(s, e) for s, e, _ in accounts] + [(s, e) for s, e in _phones(text) if not repeats_an_account(text[s:e])]:
        taken[s:e] = b"\x01" * (e - s)
    out = [(s, e, "account") for s, e, _ in accounts]
    for m in DIGIT_RUN.finditer(text):
        k = m.start()
        while k < m.end():
            if taken[k] or not text[k].isdecimal():
                k += 1
                continue
            s = k
            while k < m.end() and not taken[k]:
                k += 1
            e = k
            while not text[e - 1].isdecimal():
                e -= 1
            piece = text[s:e]
            if repeats_an_account(piece) or _labeled_as_account(text, s):
                kind = "account"
            elif len(piece) == 9 and piece.isdecimal() and aba_valid(_ascii(piece)):
                kind = "routing"
            else:
                kind = "other"
            out.append((s, e, kind))
    return out


def _labeled_as_account(text: str, start: int) -> bool:
    """Whether the nearest label before `start`, on its line and within LABEL_REACH characters, is an
    account label (not a routing label or a phone word)."""
    line_start = text.rfind("\n", max(0, start - LABEL_REACH), start) + 1
    labels = list(_NEAREST_LABEL.finditer(text, max(line_start, start - LABEL_REACH), start))
    return bool(labels) and labels[-1].lastgroup == "account"


def _ascii(run: str) -> str:
    return "".join(str(unicodedata.decimal(c)) for c in run if c.isdecimal())


def _unique(items) -> list[str]:
    return list(dict.fromkeys(items))
