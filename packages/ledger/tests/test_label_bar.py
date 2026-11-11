"""Spec 0091: one email can't veto a number.

A label against a number is a visible sighting whose party isn't `vendor`, or whose role is `fax`
in any case. Support counts only the vendor's own sightings. The labels against a number veto it
only when established: their documents span `min_source_types` source types and the earliest is
`min_age_days` before the request, counting only documents that are honest by their own facts.
Weaker labels are ignored: they neither veto nor support.

Most of these tests run the rule alone, `_select_rule(label_bar=True)`, without spec 0079's guard
around it. The last sections run `select()`, the guard around it, under the two flags (rules 5 to 7).
"""
import datetime as dt

import pytest
from _world import REQUEST, T0, at, calls, corpus_world, flags, frozen, in_thread, provable, run, world
from dialback_ledger.select import (
    NO_REQUEST,
    RULE_VERSION,
    RULE_VERSION_GUARD,
    RULE_VERSION_V1,
    ChangeRequest,
    Settings,
    Sighting,
    _select_rule,
    active_rule_version,
    change_context_documents,
)
from hypothesis import example, given, settings
from test_demo_picks import STORIES, demo_world
from test_select_corpus import TRUTHS
from test_select_properties import any_rows

REAL, PLANTED = "+14155550142", "+16455550177"   # the vendor's line; a provable number someone planted
P = "+14155550143"                               # one digit off REAL
X = "+13035550101"                               # older than REAL, labeled someone else's
UNRELATED = "+18675550188"
VETOES = {"not_vendor_party", "fax_line"}
VERSION_FIELDS = ("rule_version", "number_selection_id", "inputs_sha256")
LABELS = pytest.mark.parametrize(("role", "party"), [("unknown", "bank"), ("fax", "vendor"), ("unknown", "unknown")],
                                 ids=["bank-line", "fax-line", "bare-mention"])


def inner(w: dict, **kw) -> dict:
    """The new rule alone, label bar on."""
    return _select_rule(**w, **kw, rule_version="t", label_bar=True)


def candidate(out: dict, e164: str) -> dict:
    return next(c for c in out["candidates"] if c["e164"] == e164)


def reasons(out: dict, e164: str) -> list[str]:
    return candidate(out, e164)["reasons"]


def label(e164: str, days: float, channel: str = "email", role: str = "unknown", party: str = "bank", **kw) -> dict:
    """A visible sighting that labels `e164` someone else's (or a fax), in a document of its own."""
    return {"e164": e164, "channel": channel, "days_before": days, "visibility": "visible", "role": role,
            "party": party} | kw


def vendors(e164: str, days: float, channel: str) -> dict:
    """A visible sighting labeled the vendor's own line, in a document of its own."""
    return {"e164": e164, "channel": channel, "days_before": days, "visibility": "visible", "role": "ar",
            "party": "vendor"}


def day_before(role: str, party: str) -> dict:
    """The label in a separate email from the vendor's domain the day before: outside the change context."""
    return label(REAL, 1, role=role, party=party, thread="t-sep", subject="One more thing")


# The table's weak labels (FINDINGS A1, A2) ------------------------------------------------------------

@LABELS
def test_a_weak_label_in_the_request_thread_is_ignored(role, party):
    w = world(provable(REAL, 200) + provable(PLANTED, 150) + [in_thread(REAL, role=role, party=party)])
    assert frozen(w)["e164"] == PLANTED, "FINDINGS A1: today the label moves the call"
    out = inner(w)
    assert out["e164"] == REAL
    assert reasons(out, REAL) == []


@LABELS
def test_a_weak_label_the_day_before_is_ignored(role, party):
    w = world(provable(REAL, 200) + provable(PLANTED, 150) + [day_before(role, party)])
    assert frozen(w)["e164"] == PLANTED
    out = inner(w)
    assert out["e164"] == REAL
    assert reasons(out, REAL) == []


@LABELS
def test_a_label_alone_cannot_block(role, party):
    w = world(provable(REAL, 200) + [day_before(role, party)])
    assert calls(frozen(w)) == [], "today one email blocks the real line"
    assert calls(inner(w)) == [REAL]


def test_a_labeled_sighting_is_not_support():
    # Rule 2: a vendor email plus a contract calling the number a bank's. Today the contract is a
    # second source type and its label vetoes; now it is neither.
    w = world([vendors(REAL, 200, "email"), label(REAL, 200, "contract")])
    today, out = frozen(w), inner(w)
    assert calls(today) == calls(out) == []
    assert reasons(today, REAL) == ["not_vendor_party"]
    assert reasons(out, REAL) == ["too_few_source_types"]


# Established labels still veto ------------------------------------------------------------------------

def x_labeled(listing_days: float = 120, listing_subject: str = "Hello") -> list[dict]:
    """X is older than REAL and provable, but a contract (party unknown) and a listing (a bank's) say it
    isn't the vendor's line."""
    return provable(REAL, 400) + provable(X, 500) + [
        label(X, 150, "contract", party="unknown", doc_id="x-label-contract"),
        label(X, listing_days, "listing", party="bank", subject=listing_subject, doc_id="x-label-listing")]


def test_two_old_sources_veto():
    out = inner(world(x_labeled()))
    assert "not_vendor_party" in reasons(out, X)
    assert out["e164"] == REAL


def test_attacker_cannot_unestablish():
    # An old lookalike email pulls the cutoff back to 160 days, so both labeling documents fall in the
    # change context. Whether a label counts is a fact of its own document, so they still establish.
    look = {"e164": UNRELATED, "channel": "email", "days_before": 160, "visibility": "visible", "role": "ar",
            "party": "vendor", "lookalike": True, "thread": "t-look"}
    w = world(x_labeled() + [look])
    assert {"x-label-contract", "x-label-listing"} <= {d.id for d in change_context_documents(**w)}
    out = inner(w)
    assert at(out["cutoff"]) == T0 - dt.timedelta(days=160)
    assert "not_vendor_party" in reasons(out, X)
    assert out["e164"] == REAL
    # Same subject: the listing, at 170 days under the request's subject, is older than the pulled
    # cutoff but within the subject window of it, so it is in the change context. Rule 3 measures the
    # window from the request (170 days, outside it), so the listing still counts and the labels establish.
    w = world(x_labeled(listing_days=170, listing_subject="Updated banking details") + [look])
    listing = next(d for d in w["documents"] if d.id == "x-label-listing")
    out = inner(w)
    assert listing.received_at < at(out["cutoff"]) == T0 - dt.timedelta(days=160)
    assert "x-label-listing" in {d.id for d in change_context_documents(**w)}
    assert "not_vendor_party" in reasons(out, X)
    assert out["e164"] == REAL


def test_a_label_under_the_request_subject_does_not_establish():
    # The listing is under the request's subject within the window: it doesn't count, and the contract
    # alone is one source type. X keeps its own support and is the older number (the accepted cost).
    out = inner(world(x_labeled(listing_days=20, listing_subject="Updated banking details")))
    assert reasons(out, X) == []
    # Controls: the same listing under another subject, or under the request's subject but outside
    # the window, does count, and the labels establish.
    for days, subject in ((20, "Hello"), (40, "Updated banking details")):
        assert "not_vendor_party" in reasons(inner(world(x_labeled(days, subject))), X), (days, subject)


def test_an_old_label_in_the_request_thread_does_not_count():
    # Rule 3: a labeling document in the request's thread never counts, however old (FINDINGS A3's
    # hijacked thread). Without it, the contract's label is alone and weak.
    rows = provable(REAL, 400) + [in_thread(REAL, days=300, role="unknown", party="bank"), label(REAL, 300, "contract")]
    out = inner(world(rows))
    assert reasons(out, REAL) == [] and out["e164"] == REAL
    other = provable(REAL, 400) + [label(REAL, 300, "email", thread="t-other"), label(REAL, 300, "contract")]
    assert "not_vendor_party" in reasons(inner(world(other)), REAL), "control: in another thread, it counts"


def test_the_request_itself_does_not_count():
    # Rule 3: the request is never a labeling document, even with no thread or subject to place it by.
    w = world(provable(REAL, 400) + [label(REAL, 300, "contract")])
    bare = ChangeRequest(REQUEST.document_id, REQUEST.received_at, None, None, REQUEST.sender)
    w |= {"request": bare,
          "sightings": [*w["sightings"], Sighting(REAL, REQUEST.document_id, "email", T0, "visible", "unknown", "bank", T0)]}
    out = inner(w)
    assert reasons(out, REAL) == [] and out["e164"] == REAL
    other = provable(REAL, 400) + [label(REAL, 300, "contract"), label(REAL, 0, "email", thread="t-other")]
    assert "not_vendor_party" in reasons(inner(world(other)), REAL), "control: another email that moment counts"


def test_a_lookalike_label_does_not_count():
    # Rule 3: a labeling document from a lookalike sender never counts, whatever its channel. (It also
    # pulls the cutoff back to 300 days; REAL, at 400, is still old enough.)
    rows = provable(REAL, 400) + [label(REAL, 300, "vendor_portal", lookalike=True), label(REAL, 300, "contract")]
    out = inner(world(rows))
    assert reasons(out, REAL) == [] and out["e164"] == REAL
    own = provable(REAL, 400) + [label(REAL, 300, "vendor_portal"), label(REAL, 300, "contract")]
    assert "not_vendor_party" in reasons(inner(world(own)), REAL), "control: from the vendor's domain, it counts"


def test_the_label_bar_reads_its_settings():
    w = world(x_labeled())
    assert "not_vendor_party" in reasons(inner(w), X)
    assert not VETOES & set(reasons(inner(w, settings=Settings(min_age_days=200)), X)), "earliest label is 150 days"
    assert not VETOES & set(reasons(inner(w, settings=Settings(min_source_types=3)), X)), "labels span two types"
    subject = world(x_labeled(listing_days=20, listing_subject="Updated banking details"))
    assert "not_vendor_party" in reasons(inner(subject, settings=Settings(same_subject_window_days=10)), X)


# The near-miss anchor (Q153) --------------------------------------------------------------------------

@pytest.mark.parametrize("real_rows", [
    [label(REAL, 400, "contract", party="unknown"), label(REAL, 400, "listing", party="unknown")],
    [label(REAL, 400, "contract", party="unknown"), vendors(REAL, 100, "listing")],
    [label(REAL, 400, "contract", role="fax", party="vendor"), vendors(REAL, 100, "listing")],
], ids=["1", "1b", "1c"])
def test_labeled_real_line_still_anchors_its_near_miss(real_rows):
    # The builder check's worlds: REAL's oldest sightings carry a label (the stub reader's "Tel:" line,
    # or the vendor's own "Fax:"); P is one digit off, provable at 200 days.
    w = world(real_rows + provable(P, 200))
    today, out = frozen(w), inner(w)
    assert calls(today) == calls(out) == []
    assert "near_miss_of_older_number" in reasons(today, P)
    assert "near_miss_of_older_number" in reasons(out, P)
    assert candidate(out, REAL)["first_seen_at"] == candidate(today, REAL)["first_seen_at"], "today's first sighting"


# Review Focus 1 to 5 ----------------------------------------------------------------------------------

@pytest.mark.parametrize(("role", "party", "veto"), [("ar", "Vendor", "not_vendor_party"), ("FAX", "vendor", "fax_line")],
                         ids=["party-Vendor", "role-FAX"])
def test_a_differently_cased_label_is_a_label_against(role, party, veto):
    # Review Focus 1. Weak, it neither vetoes nor supports: the contract alone is one source type.
    weak = world([vendors(REAL, 200, "contract"), label(REAL, 200, "listing", role=role, party=party)])
    assert reasons(inner(weak), REAL) == ["too_few_source_types"]
    # Established (two source types, 200 days back), it vetoes, as spec 0021 says.
    established = world([vendors(REAL, 200, "contract"), label(REAL, 200, "listing", role=role, party=party),
                         label(REAL, 200, "email", role=role, party=party)])
    assert veto in reasons(inner(established), REAL)


def test_a_hidden_label_neither_supports_nor_opposes():
    # Review Focus 2. Visible, these two labels would be established; hidden, they don't oppose, and
    # they add no source type to the contract.
    rows = [vendors(REAL, 200, "contract"), label(REAL, 300, "listing", visibility="white"),
            label(REAL, 300, "email", visibility="white")]
    assert reasons(inner(world(rows)), REAL) == ["too_few_source_types"]
    visible = [r | {"visibility": "visible"} for r in rows]
    assert "not_vendor_party" in reasons(inner(world(visible)), REAL), "control: visible, they establish"
    # Nor do they make a weak visible label established.
    weak = provable(REAL, 200) + [day_before("unknown", "bank")]
    assert reasons(inner(world(weak + rows[1:])), REAL) == []
    assert "not_vendor_party" in reasons(inner(world(weak + visible[1:])), REAL), "control: visible, they do"


def test_labels_from_another_domain_never_establish():
    # Review Focus 3. A bank's own email is not a source type that counts, so the contract's label is
    # alone and weak.
    rows = provable(REAL, 200) + [label(REAL, 300, "contract"), label(REAL, 300, "email", domain="bank.example")]
    out = inner(world(rows))
    assert reasons(out, REAL) == [] and out["e164"] == REAL
    own = provable(REAL, 200) + [label(REAL, 300, "contract"), label(REAL, 300, "email")]
    assert "not_vendor_party" in reasons(inner(world(own)), REAL), "control: from the vendor's domain, it counts"


def test_a_bank_label_and_a_fax_label_establish_together():
    # Review Focus 4. X is older than REAL and provable; a bank label in a contract and the vendor's own
    # fax label in a listing span two source types together.
    labels = [label(X, 200, "contract", party="bank"), label(X, 150, "listing", role="fax", party="vendor")]
    out = inner(world(provable(REAL, 300) + provable(X, 400) + labels))
    assert {"not_vendor_party", "fax_line"} <= set(reasons(out, X))
    assert out["e164"] == REAL
    for one in labels:                       # either alone is one source type: weak, and X is picked
        alone = inner(world(provable(REAL, 300) + provable(X, 400) + [one]))
        assert not VETOES & set(reasons(alone, X)) and alone["e164"] == X, one


def test_a_label_received_after_the_request_does_not_count():
    # Review Focus 5. The listing arrives about 30 minutes after the request: before the requested
    # cutoff and recorded before the case opened, but after "the request". The contract alone is weak.
    rows = provable(REAL, 200) + [label(REAL, 300, "contract"), label(REAL, -0.02, "listing")]
    w = world(rows)
    late = w["documents"][-1]
    assert REQUEST.received_at < late.received_at < w["requested_cutoff"] and late.recorded_at < w["case_opened_at"]
    out = inner(w)
    assert reasons(out, REAL) == [] and out["e164"] == REAL
    early = provable(REAL, 200) + [label(REAL, 300, "contract"), label(REAL, 1, "listing")]
    assert "not_vendor_party" in reasons(inner(world(early)), REAL), "control: a day before, it counts"


# The label bar off is today's rule --------------------------------------------------------------------

@settings(max_examples=300, deadline=None)
@given(any_rows)
def test_label_bar_off_is_todays_rule(rs):
    w = world(rs)
    assert _select_rule(**w, rule_version=RULE_VERSION_V1, label_bar=False) == frozen(w)


# select(): the flags (rules 6 and 7) ------------------------------------------------------------------

def without(out: dict, fields) -> dict:
    return {k: v for k, v in out.items() if k not in fields}


def first(out: dict) -> str | None:
    """Who is called first, or None for nobody."""
    return next(iter(calls(out)), None)


def in_the_request(rows: list[dict], role: str, party: str) -> dict:
    """A world whose change request email itself labels REAL (FINDINGS A1, A2)."""
    w = world(rows)
    return w | {"sightings": [*w["sightings"], Sighting(REAL, REQUEST.document_id, "email", T0, "visible", role, party, T0)]}


def a1() -> dict:
    """FINDINGS A1: the change email calls REAL a bank's line, and PLANTED is provable. The three rules
    differ here: today's calls PLANTED, spec 0079 calls nobody, and this spec calls REAL."""
    return in_the_request(provable(REAL, 200) + provable(PLANTED, 150), "unknown", "bank")


@pytest.mark.parametrize(("guard", "bar", "version"), [
    ("off", "off", RULE_VERSION_V1), ("off", None, RULE_VERSION_V1),
    (None, "off", RULE_VERSION_GUARD), (None, None, RULE_VERSION)],
    ids=["guard-off-bar-off", "guard-off-bar-on", "guard-on-bar-off", "both-on"])
def test_three_flag_states_name_their_rule(guard, bar, version):
    # Rule 6 (None: the flag unset, its default). Each state runs its own rule and names it.
    w = a1()
    with flags(guard, bar):
        assert active_rule_version() == version
        out = run(w)
    assert out["rule_version"] == version
    if version == RULE_VERSION_V1:
        assert out == frozen(w), "the guard off: today's rule exactly, whatever the label bar says"
    elif version == RULE_VERSION_GUARD:
        assert out["outcome"] == "withheld", "the label bar off: spec 0079"
    else:
        assert out["outcome"] == "picked" and out["e164"] == REAL, "both on: this spec"


@pytest.mark.parametrize(("guard", "bar"), [("on", "on"), (None, "ON"), (None, ""), (None, "0"), ("sideways", "sideways")])
def test_unknown_flag_values_run_this_rule(guard, bar):
    w = a1()
    with flags(guard, bar):
        assert active_rule_version() == RULE_VERSION
        out = run(w)
    assert out["rule_version"] == RULE_VERSION and out["e164"] == REAL


def test_no_request_still_fails_closed():
    # The acceptance list's failure path, through select() with both flags on. R7: a case whose change
    # request isn't in the ledger calls nobody, even where every label is weak and the real line is
    # provable; the rule alone too, since no labeling document counts without a request.
    for w in (world(provable(REAL, 200)), a1()):
        w = w | {"request": None}
        with flags():
            out = run(w)
        assert out["outcome"] == "no_provable_number" and out["reason"] == NO_REQUEST
        assert out["rule_version"] == RULE_VERSION
        assert (inner(w)["outcome"], inner(w)["reason"]) == ("no_provable_number", NO_REQUEST)
    # And established labels still refuse the number with today's reason.
    with flags():
        out = run(world(x_labeled()))
    assert "not_vendor_party" in reasons(out, X) and out["e164"] == REAL


# Spec 0091's table, through select() ------------------------------------------------------------------

WEAK = [("unknown", "bank", "bank-line"), ("fax", "vendor", "fax-line"), ("unknown", "unknown", "bare-mention")]
HONEST = label(PLANTED, 20, party="unknown", thread="t-honest", subject="Not our number")  # the vendor: "0177 isn't ours"

TABLE = [  # the world; who is called first today, under spec 0079, and under this spec (None: nobody)
    *(pytest.param(in_the_request(provable(REAL, 200) + provable(PLANTED, 150), role, party), PLANTED, None, REAL,
                   id=f"a-label-in-the-change-email-{name}") for role, party, name in WEAK),
    pytest.param(world(provable(REAL, 200) + provable(PLANTED, 150) + [day_before("unknown", "bank")]),
                 PLANTED, PLANTED, REAL, id="the-same-label-the-day-before"),
    pytest.param(world(provable(REAL, 200) + [day_before("unknown", "bank")]), None, None, REAL,
                 id="that-label-with-no-plant"),
    pytest.param(in_the_request(provable(REAL, 200), "unknown", "unknown"), None, None, REAL,
                 id="an-honest-request-that-mentions-the-number"),
    pytest.param(world([vendors(REAL, 200, "email"), label(REAL, 200, "contract")]), None, None, None,
                 id="vendor-email-plus-a-contract-calling-it-a-banks"),
    pytest.param(world(provable(REAL, 200) + provable(PLANTED, 400) + [HONEST]), REAL, REAL, PLANTED,
                 id="the-accepted-cost"),
]


@pytest.mark.parametrize(("w", "today", "guarded", "now"), TABLE)
def test_the_table(w, today, guarded, now):
    assert first(frozen(w)) == today, "today's column"
    with flags(bar="off"):
        assert first(run(w)) == guarded, "spec 0079's column"
    with flags():
        out = run(w)
    assert out["rule_version"] == RULE_VERSION
    # The last row is the accepted cost (Q151): PLANTED is older than REAL and only one honest email
    # says it isn't the vendor's, so this spec calls PLANTED. Today's rule would call it too without
    # that email; the vendor's records were already poisoned (K01).
    assert first(out) == now, "this spec's column"


# The label bar on a label-free world, and on today's eligible numbers ---------------------------------

def label_free(rs: list[dict]) -> list[dict]:
    """The same rows with every label against taken off: party `vendor`, and a role that isn't a fax."""
    return [r | {"party": "vendor", "role": "main" if r["role"].lower() == "fax" else r["role"]} for r in rs]


@settings(max_examples=300, deadline=None)
@given(any_rows)
def test_no_labels_means_todays_rule(rs):
    w = world(label_free(rs))
    assert _select_rule(**w, rule_version=RULE_VERSION_V1, label_bar=True) == frozen(w), "field for field"
    with flags():
        on = run(w)
    with flags(bar="off"):
        off = run(w)
    assert (on["rule_version"], off["rule_version"]) == (RULE_VERSION, RULE_VERSION_GUARD)
    assert without(on, VERSION_FIELDS) == without(off, VERSION_FIELDS)


def ranked(out: dict) -> list[str]:
    """The eligible numbers, in rank order."""
    return [c["e164"] for c in sorted((c for c in out["candidates"] if c["eligible"]), key=lambda c: c["rank"])]


@settings(max_examples=300, deadline=None)
@given(any_rows)
# Few random draws have two or more numbers today's rule finds eligible, so three worlds always run:
# a number the bar makes eligible between two of today's; a change-context copy of a number one digit
# off an eligible one (the near-miss anchor reads only what counts); and a vendor sighting with role
# `unknown` as a number's oldest support (rule 1: not a label against).
@example(provable(X, 900) + provable(PLANTED, 600) + provable(REAL, 300) + [in_thread(PLANTED, role="unknown", party="unknown")])
@example(provable(REAL, 200) + [in_thread(P, days=300)])
@example([vendors(X, 900, "listing") | {"role": "unknown"}] + provable(X, 300) + provable(REAL, 600))
def test_todays_eligible_stay_eligible_in_order(rs):
    w = world(rs)
    today, new = ranked(frozen(w)), ranked(inner(w))
    assert set(today) <= set(new)
    assert [e for e in new if e in today] == today


# The label-free worlds on file: the frozen corpus and the demo ----------------------------------------

FILED = [*(pytest.param(lambda path=path: corpus_world(path)[1], id=path.stem) for path in TRUTHS),
         *(pytest.param(lambda ref=ref: demo_world(ref), id=f"demo-{ref}") for ref in sorted(STORIES))]


@pytest.mark.parametrize("build", FILED)
def test_corpus_and_demo_worlds_are_todays(build):
    # The acceptance list: on every label-free world, the result is today's, field for field but the
    # three that name the rule, against 0079's frozen copy.
    w = build()
    against = [s for s in w["sightings"]
               if s.visibility == "visible" and (s.party != "vendor" or s.role.strip().lower() == "fax")]
    assert against == [], "the premise: no visible label against any number"
    assert without(inner(w), VERSION_FIELDS) == without(frozen(w), VERSION_FIELDS)
