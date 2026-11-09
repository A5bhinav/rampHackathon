"""Could a person see this text? White (on the white page), under 4 points, or off the page is hidden.

Hidden values are kept, marked, and never eligible (design §4). Anything this can't read, such
as an unparseable colour, counts as hidden: fail closed (R7).
"""
from __future__ import annotations

import math
import re

TINY_POINTS = 4
NEAR_WHITE = 0xF0


def _is_white(color: object) -> bool:
    m = re.fullmatch(r"#?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})", str(color or "").strip())
    if not m:
        return True
    return all(int(g, 16) >= NEAR_WHITE for g in m.groups())


def classify(span: dict, width: float, height: float) -> str:
    try:
        x, y, size = float(span["x"]), float(span["y"]), float(span["size"])
    except (KeyError, TypeError, ValueError):
        return "off_page"
    if not all(math.isfinite(v) for v in (x, y, size)):
        return "off_page"  # NaN or infinity: nowhere a person could read it
    if not (0 <= x < width and 0 <= y < height):
        return "off_page"
    if size < TINY_POINTS:
        return "tiny"
    if _is_white(span.get("color")):
        return "white"
    return "visible"
