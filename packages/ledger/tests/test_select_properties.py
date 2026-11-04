"""Spec 0015: the six properties in the ledger README, over random vendor histories."""
import datetime as dt
import random

from _world import SCHEMA, T0, at, run, world
from dialback_ledger.select import near_miss
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

NUMBERS = ["+14155550142", "+14155550143", "+14155550124", "+14155550117", "+14155550169"]
row = st.fixed_dictionaries({
    "e164": st.sampled_from(NUMBERS),
    "channel": st.sampled_from(["email", "contract", "vendor_portal", "listing", "paper_mail", "vendor_master"]),
    "days_before": st.integers(-2, 1500),
    "visibility": st.sampled_from(["visible", "visible", "visible", "white", "tiny", "off_page"]),
    "role": st.sampled_from(["main", "ar", "billing", "fax", "unknown"]),
    "party": st.sampled_from(["vendor", "vendor", "vendor", "customer", "bank", "unknown"]),
    "thread": st.sampled_from([None, None, None, "req"]),
    "subject": st.sampled_from(["Hello", "Invoice", "Updated banking details", "Re: updated banking details"]),
    "lookalike": st.sampled_from([False, False, False, False, True]),
})
rows = st.lists(row, max_size=25)
context_row = row.flatmap(lambda r: st.sampled_from([r | {"thread": "req"}, r | {"lookalike": True},
                                                     r | {"days_before": -1}]))
many = settings(max_examples=150, deadline=None)


def eligible(out: dict) -> set[str]:
    return {c["e164"] for c in out["candidates"] if c["eligible"]}


@many
@given(rows)
def test_the_result_is_always_a_valid_selection(rs):
    out = run(world(rs))
    assert list(Draft202012Validator(SCHEMA).iter_errors(out)) == []
    assert (out["outcome"] == "picked") == bool(eligible(out))


@many
@given(rows, st.lists(context_row, min_size=1, max_size=5))
def test_1_change_context_never_makes_a_number_eligible(rs, extra):
    assert eligible(run(world(rs + extra))) <= eligible(run(world(rs)))


@many
@given(rows, st.lists(row, min_size=1, max_size=5))
def test_2_only_rows_recorded_before_the_case_opened_count(rs, late):
    assert run(world(rs + [r | {"recorded_late": True} for r in late])) == run(world(rs))


@many
@given(rows, st.randoms(use_true_random=False))
def test_3_order_does_not_matter(rs, rnd: random.Random):
    shuffled = rs[:]
    rnd.shuffle(shuffled)
    a, b = run(world(rs)), run(world(shuffled))
    # Document ids follow the row order, so compare what the rule decided.
    strip = lambda o: {k: v for k, v in o.items() if k not in ("inputs_sha256", "number_selection_id")}
    assert strip(a) == strip(b)


def in_context(r: dict, cutoff: dt.datetime) -> bool:
    received = T0 - dt.timedelta(days=r["days_before"])
    same_subject = r.get("subject", "").lower().removeprefix("re: ") == "updated banking details"
    return (r.get("thread") == "req" or r.get("lookalike") or received > cutoff
            or (same_subject and cutoff - received <= dt.timedelta(days=30)))


@many
@given(rows)
def test_4_a_number_first_seen_in_the_change_context_is_never_picked(rs):
    out = run(world(rs))
    if out["outcome"] == "picked":
        visible = [r for r in rs if r["e164"] == out["e164"] and r["visibility"] == "visible"]
        first = min(visible, key=lambda r: (-r["days_before"], in_context(r, at(out["cutoff"])) is False))
        assert not in_context(first, at(out["cutoff"]))


@many
@given(rows)
def test_5_labels_only_remove_eligibility(rs):
    neutral = [r | {"role": "main", "party": "vendor"} for r in rs]
    labeled, plain = run(world(rs)), run(world(neutral))
    assert eligible(labeled) <= eligible(plain)
    for c in labeled["candidates"]:
        if c["eligible"]:
            assert len(c["source_types"]) >= 2


@many
@given(rows)
def test_6_the_pick_is_never_a_near_miss_of_an_older_number(rs):
    out = run(world(rs))
    if out["outcome"] != "picked":
        return
    cutoff = at(out["cutoff"])
    established = lambda c: cutoff - at(c["first_seen_at"]) >= dt.timedelta(days=365)
    pick = next(c for c in out["candidates"] if c["e164"] == out["e164"])
    for c in out["candidates"]:
        older_vendor_number = not {"hidden_text_only", "first_seen_in_change_context"} & set(c["reasons"])
        if older_vendor_number and at(c["first_seen_at"]) < at(pick["first_seen_at"]) and near_miss(c["e164"], pick["e164"]):
            assert established(c) and established(pick), (pick, c)


def test_a_missing_change_request_fails_closed():
    w = world([{"e164": NUMBERS[0], "channel": ch, "days_before": 900, "visibility": "visible", "role": "ar",
                "party": "vendor"} for ch in ("email", "contract")])
    assert run(w)["outcome"] == "picked"
    out = run(w | {"request": None})
    assert out["outcome"] == "no_provable_number" and "change request" in out["reason"]


def old(e164: str, channel: str, **kw) -> dict:
    return {"e164": e164, "channel": channel, "days_before": 900, "visibility": "visible", "role": "ar",
            "party": "vendor"} | kw


def test_a_source_inside_the_change_context_never_counts():
    # Seen once by email four months ago, then "confirmed" by a portal entry after the request.
    rows = [old(NUMBERS[4], "email", days_before=120), old(NUMBERS[4], "vendor_portal", days_before=-1)]
    out = run(world(rows))
    assert out["outcome"] == "no_provable_number"
    assert out["candidates"][0]["source_types"] == ["email"]
    assert "too_few_source_types" in out["candidates"][0]["reasons"]
    # Nor a second source in the request's own thread, sent long ago (a hijacked thread).
    rows = [old(NUMBERS[4], "email", days_before=120), old(NUMBERS[4], "contract", days_before=110, thread="req")]
    assert run(world(rows))["outcome"] == "no_provable_number"


def test_a_banks_or_customers_number_is_refused():
    for party in ("bank", "customer", "unknown"):
        rows = [old(NUMBERS[0], "email"), old(NUMBERS[0], "contract", party=party)]
        out = run(world(rows))
        assert out["outcome"] == "no_provable_number", party
        assert "not_vendor_party" in out["candidates"][0]["reasons"]


def test_a_fax_line_is_refused():
    out = run(world([old(NUMBERS[0], "email"), old(NUMBERS[0], "contract", role="fax")]))
    assert out["outcome"] == "no_provable_number"
    assert "fax_line" in out["candidates"][0]["reasons"]


def test_ties_go_to_more_sources_then_ar_over_main():
    rows = [old(NUMBERS[3], ch, role="main") for ch in ("email", "contract", "listing")]
    rows += [old(NUMBERS[4], ch, role="ar") for ch in ("email", "contract")]
    assert run(world(rows))["e164"] == NUMBERS[3]          # three sources beat two
    rows = [old(NUMBERS[3], ch, role="main") for ch in ("email", "contract")]
    rows += [old(NUMBERS[4], ch, role="ar") for ch in ("email", "contract")]
    out = run(world(rows))
    assert out["e164"] == NUMBERS[4] and [a["e164"] for a in out["alternates"]] == [NUMBERS[3]]
