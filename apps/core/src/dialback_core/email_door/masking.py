"""Masking for text that may carry an account number (ADR-8)."""
from __future__ import annotations

import re

# A run of digits, possibly grouped by 1 to 3 separator characters: whitespace (a no-break space too),
# `.`, `-`, `/` or `_` ("0001 2345 4821", "0001.2345.4821", "0001  2345").
_RUN = re.compile(r"(?<!\d)\d(?:[\s.\-/_]{0,3}\d)*")


def strip_nul(text: str) -> str:
    """Postgres text cannot hold NUL; an email can carry one."""
    return text.replace("\x00", "")


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
