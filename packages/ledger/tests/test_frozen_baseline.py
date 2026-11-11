"""Spec 0079's baseline stays today's rule: the frozen copy is main's select.py at 907b0077, byte for
byte apart from one import line, and it uses a frozen copy of the lookalike classifier, so a later
change to the live classifier can't move both sides of the prefix property at once (the review's
item 7). Checked by git blob id, so it needs no git history (CI checks out shallow)."""
import hashlib
from pathlib import Path

import dialback_ledger.lookalike as live_lookalike
from _world import FROZEN

FIXTURES = Path(__file__).parent / "fixtures"
# `git rev-parse 907b0077:packages/ledger/src/dialback_ledger/<file>`
SELECT_BLOB = "030892666d5466bb58f451524c2373bdc5689558"
LOOKALIKE_BLOB = "9c984514c294f1105dababeb189c27013a008270"
FROZEN_IMPORT = "from lookalike_v1_frozen import is_lookalike, sender_domain\n"
ORIGINAL_IMPORT = "from .lookalike import is_lookalike, sender_domain\n"


def blob_id(text: str) -> str:
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def body(path: Path) -> str:
    """The file without its leading comment header: from its module docstring on."""
    text = path.read_text(encoding="utf-8")
    return text[text.index('"""'):]


def test_the_frozen_rule_is_mains_select_but_for_one_import():
    text = body(FIXTURES / "select_v1_frozen.py")
    assert text.count(FROZEN_IMPORT) == 1
    assert blob_id(text.replace(FROZEN_IMPORT, ORIGINAL_IMPORT)) == SELECT_BLOB


def test_the_frozen_classifier_is_mains_lookalike():
    assert blob_id(body(FIXTURES / "lookalike_v1_frozen.py")) == LOOKALIKE_BLOB


def test_the_frozen_rule_uses_the_frozen_classifier():
    assert FROZEN.is_lookalike is not live_lookalike.is_lookalike
    assert FROZEN.is_lookalike.__module__ == "lookalike_v1_frozen"
    assert FROZEN.sender_domain.__module__ == "lookalike_v1_frozen"
