"""Spec 0021: the six properties in the ledger README, over random vendor histories."""
import datetime as dt
import random

from _world import SCHEMA, T0, at, calls, frozen, run, world
from dialback_ledger.select import change_context_documents, near_miss
from hypothesis import find, given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

NUMBERS = ["+14155550142", "+14155550143", "+14155550124", "+14155550117", "+14155550169"]
row = st.fixed_dictionaries({
    "e164": st.sampled_from(NUMBERS),
    "channel": st.sampled_from(["email", "contract", "vendor_portal", "listing", "paper_mail", "vendor_master", "fax_server"]),
    "days_before": st.integers(-2, 1500),
    "visibility": st.sampled_from(["visible", "visible", "visible", "white", "tiny", "off_page", "unclear"]),
    "role": st.sampled_from(["main", "ar", "billing", "fax", "unknown", "Fax"]),
    "party": st.sampled_from(["vendor", "vendor", "vendor", "customer", "bank", "unknown", "Vendor"]),
    "thread": st.sampled_from([None, None, None, "req"]),
    "subject": st.sampled_from(["Hello", "Invoice", "Updated banking details", "Re: updated banking details"]),
    "lookalike": st.sampled_from([False, False, False, False, True]),
    "domain": st.sampled_from([None, None, None, "acme-ap.example"]),
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
    # Spec 0079, decision 4: a withheld selection keeps today's eligible candidates and calls none, so
    # "picked exactly when something is eligible" became three cases.
    if out["outcome"] == "no_provable_number":
        assert not eligible(out)
    else:
        assert eligible(out), out["outcome"]
        if out["outcome"] == "picked":
            assert out["e164"] == next(c["e164"] for c in out["candidates"] if c.get("rank") == 1)


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
    w = world(rs)
    docs, sights = list(w["documents"]), list(w["sightings"])
    rnd.shuffle(docs)
    rnd.shuffle(sights)
    assert run(w | {"documents": docs, "sightings": sights}) == run(w)  # the inputs hash too


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



# Review findings (spec 0021) --------------------------------------------------------------------

def test_an_uncounted_sighting_never_makes_a_number_old():
    young = [old(NUMBERS[4], "email", days_before=20), old(NUMBERS[4], "listing", days_before=10)]
    for channel in ("vendor_master", "fax_server"):
        out = run(world(young + [old(NUMBERS[4], channel, days_before=200)]))
        assert out["outcome"] == "no_provable_number", channel
        assert "younger_than_minimum_age" in out["candidates"][0]["reasons"], channel


def test_email_from_another_domain_never_counts():
    # Anyone can register acme-ap.example and email from it months ahead; that isn't the vendor's email.
    # A lookalike: refused, and its message also pulls the cutoff back to it.
    rows = [old(NUMBERS[4], "email", days_before=120, domain="acme-ap.example"), old(NUMBERS[4], "listing", days_before=100)]
    out = run(world(rows))
    assert out["outcome"] == "no_provable_number" and "email" not in out["candidates"][0]["source_types"]
    # An unrelated domain: not a lookalike, but still not the vendor's email.
    rows = [old(NUMBERS[4], "email", days_before=120, domain="mailbox.example"), old(NUMBERS[4], "listing", days_before=100)]
    out = run(world(rows))
    assert out["outcome"] == "no_provable_number" and out["candidates"][0]["source_types"] == ["listing"]


def test_no_vendor_domain_means_no_email_counts():
    rows = [old(NUMBERS[0], "email"), old(NUMBERS[0], "listing")]
    assert run(world(rows))["outcome"] == "picked"
    assert run(world(rows) | {"vendor_domains": ()})["outcome"] == "no_provable_number"


def test_numbers_first_seen_together_are_near_misses_of_each_other():
    rows = [old(NUMBERS[0], ch, days_before=200) for ch in ("email", "contract")]
    rows += [old(NUMBERS[1], ch, days_before=200) for ch in ("email", "contract")]
    out = run(world(rows))
    assert out["outcome"] == "no_provable_number"
    assert all("near_miss_of_older_number" in c["reasons"] for c in out["candidates"])
    # Both long-established: the sibling-line exemption applies, and both stay eligible.
    rows = [r | {"days_before": 900} for r in rows]
    assert {c["e164"] for c in run(world(rows))["candidates"] if c["eligible"]} == {NUMBERS[0], NUMBERS[1]}


def test_roles_in_the_change_context_never_reorder():
    rows = [old(NUMBERS[3], ch, role="main") for ch in ("email", "contract")]
    rows += [old(NUMBERS[4], ch, role="main") for ch in ("email", "contract")]
    plain = run(world(rows))["e164"]
    other = NUMBERS[4] if plain == NUMBERS[3] else NUMBERS[3]
    # The request calls the other number "AR"; that must not make it win the tie.
    assert run(world(rows + [old(other, "email", role="ar", thread="req", days_before=0)]))["e164"] == plain


def test_a_tagged_subject_is_still_the_same_subject():
    rows = [old(NUMBERS[4], "email", days_before=10, subject="[EXTERNAL] RE: Updated banking details")]
    assert "first_seen_in_change_context" in run(world(rows))["candidates"][0]["reasons"]


def test_conflicting_documents_with_one_id_count_against():
    rows = [old(NUMBERS[0], "email", doc_id="dup"), old(NUMBERS[0], "contract", doc_id="dup"), old(NUMBERS[0], "listing")]
    assert run(world(rows))["outcome"] == "no_provable_number"


def test_a_fax_label_in_any_case_is_a_fax():
    out = run(world([old(NUMBERS[0], "email"), old(NUMBERS[0], "contract", role="FAX")]))
    assert "fax_line" in out["candidates"][0]["reasons"]



# Spec 0079: the prefix property ------------------------------------------------------------------

def prefix_holds(rule, w: dict) -> bool:
    """What `rule` calls is a prefix of what the frozen copy of today's rule calls: the same numbers in
    the same order, possibly cut short or empty."""
    new, old = calls(rule(w)), calls(frozen(w))
    return new == old[:len(new)]


# The strategies above almost never give today's rule a pick (0 of 3000 derandomized draws, Nov 10),
# and the guard acts only on a pick. These worlds do: two or three provable numbers that are not near
# misses of each other, plus change-context rows on them. At 1000 derandomized draws, today's rule
# picks in 450 of them; the new rule withholds 41 and cuts the backups in 2.
PROVABLE = ["+14155550142", "+16455550177", "+13035550101"]
clean = st.fixed_dictionaries({
    "channel": st.sampled_from(["email", "contract", "vendor_portal", "listing", "paper_mail"]),
    "days_before": st.integers(60, 1500), "visibility": st.just("visible"),
    "role": st.sampled_from(["ar", "main", "billing"]), "party": st.just("vendor")})
provable_number = st.tuples(st.sampled_from(PROVABLE), st.lists(clean, min_size=2, max_size=3)).map(
    lambda t: [r | {"e164": t[0]} for r in t[1]])
on_a_provable = st.tuples(context_row, st.sampled_from(PROVABLE + PROVABLE + NUMBERS[1:2])).map(lambda t: t[0] | {"e164": t[1]})
guarded_rows = st.tuples(st.lists(provable_number, min_size=2, max_size=3), st.lists(on_a_provable, min_size=1, max_size=4),
                         st.lists(row, max_size=2)).map(lambda t: [r for rs in t[0] for r in rs] + t[1] + t[2])
any_rows = st.one_of(st.tuples(rows, st.lists(context_row, max_size=5)).map(lambda t: t[0] + t[1]), guarded_rows)


@settings(max_examples=400, deadline=None)
@given(any_rows)
def test_new_rule_is_a_prefix_of_frozen_v1(rs):
    w = world(rs)
    assert prefix_holds(run, w)
    assert list(Draft202012Validator(SCHEMA).iter_errors(run(w))) == []


def skips_the_comparison(w: dict) -> dict:
    """A rule that skips spec 0079's comparison and calls what the run without the change context
    calls: the context-blind fix FINDINGS A6 ruled out."""
    today = frozen(w)
    context = {d.id for d in change_context_documents(**w)}
    return frozen(w | {"documents": [d for d in w["documents"] if d.id not in context],
                       "sightings": [s for s in w["sightings"] if s.document_id not in context],
                       "requested_cutoff": at(today["cutoff"])})


def test_the_prefix_check_fails_a_rule_that_skips_the_comparison():
    # The check can fail: on A1's world (a bank label in the request's thread on the real line, a
    # planted provable number), the rule that skips the comparison calls the real line, which today's
    # rule wouldn't call first. And the property's own strategy finds such a world by itself.
    a1 = [old(NUMBERS[0], ch, days_before=200) for ch in ("contract", "listing")]
    a1 += [old(NUMBERS[3], ch, days_before=150) for ch in ("contract", "listing")]
    a1 += [old(NUMBERS[0], "email", days_before=0, thread="req", party="bank", role="unknown")]
    assert not prefix_holds(skips_the_comparison, world(a1))
    assert prefix_holds(run, world(a1))
    found = find(guarded_rows, lambda rs: not prefix_holds(skips_the_comparison, world(rs)),
                 settings=settings(max_examples=1000, deadline=None, database=None, derandomize=True))
    assert prefix_holds(run, world(found))
