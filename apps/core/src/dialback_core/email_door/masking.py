"""Masking for text that may carry an account number (ADR-8)."""
from __future__ import annotations

import re

# A run of digits, possibly grouped by up to 5 separator characters: whitespace (a no-break space too),
# `,` `.` `-` `/` `_`, the Unicode dashes U+2010 to U+2015 and U+2212, and the zero-width characters
# U+200B, U+200C, U+200D, U+2060 and U+FEFF, and the invisible formatting characters U+00AD, U+200E, U+200F and U+2066 to U+2069. A separator never matches a digit, so the pattern is linear.
_RUN = re.compile(r"(?<!\d)\d(?:[\s,.\-/_\u2010-\u2015\u2212\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f\u2066-\u2069]{0,5}\d)*")


def strip_nul(text: str) -> str:
    """Text Postgres can store: no NUL, and a lone surrogate (which json will happily read) becomes `?`."""
    return text.replace("\x00", "").encode("utf-8", errors="replace").decode("utf-8")


def _mask(match: re.Match[str]) -> str:
    run = match.group(0)
    if sum(c.isdigit() for c in run) < 5:
        return run
    keep_from = len(run)
    seen = 0
    for i in range(len(run) - 1, -1, -1):
        if run[i].isdigit():
            seen += 1
            if seen == 4:
                keep_from = i
                break
    return "".join(c if (not c.isdigit() or i >= keep_from) else "*" for i, c in enumerate(run))


def mask_digit_runs(text: str) -> str:
    """Replace each digit of every run of 5 or more digits with `*`, except the last four. Separators stay."""
    return _RUN.sub(_mask, text)
