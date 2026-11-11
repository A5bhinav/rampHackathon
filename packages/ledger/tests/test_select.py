"""select() on spec 0092's new visibility: a number seen only in text that leaves no ink is hidden text."""
from _world import run, world


def rows(visibility: str) -> list[dict]:
    return [{"e164": "+14155550142", "channel": channel, "days_before": days, "visibility": visibility, "role": "ar",
             "party": "vendor"} for channel, days in (("contract", 400), ("listing", 300))]


def test_no_ink_only_is_hidden_text_only():
    (c,) = run(world(rows("no_ink")))["candidates"]
    assert "hidden_text_only" in c["reasons"] and not c["eligible"]


def test_the_same_number_seen_visibly_is_eligible():
    # The control: only the visibility differs, so it is what refuses the number above.
    (c,) = run(world(rows("visible")))["candidates"]
    assert c["eligible"] and c["reasons"] == []
