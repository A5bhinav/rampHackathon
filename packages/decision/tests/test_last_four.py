import pytest
from dialback_decision import LAST_FOUR_RESULTS, compare_last_four
from hypothesis import given
from hypothesis import strategies as st


@pytest.mark.parametrize(("spoken", "new", "old", "expected"), [
    (["4821"], "4821", "1234", "match"),
    (["1234"], "4821", "1234", "matches_old_account"),
    (["4822"], "4821", "1234", "near_miss"),          # one digit off
    (["8421"], "4821", "1234", "near_miss"),          # two neighbors swapped
    (["2841"], "4821", "1234", "mismatch"),           # two digits off, not neighbors
    (["9999"], "4821", None, "mismatch"),
    (["4821", "4821"], "4821", "1234", "match"),      # said twice, the same
    (["4821", "4822"], "4821", "1234", "inconsistent"),
    (["482"], "4821", "1234", "not_provided"),
    (["refused"], "4821", "1234", "not_provided"),
    (["dont_know"], "4821", "1234", "not_provided"),
    ([], "4821", "1234", "not_provided"),
    (["refused", "4821"], "4821", "1234", "match"),   # refused at first, then gave them
    (["4821"], "4821", "4821", "ambiguous"),          # old and new end the same: proves nothing
    (["4821", "1111"], "4821", "4821", "inconsistent"),  # inconsistent is tested first
])
def test_compare_in_the_designed_order(spoken, new, old, expected):
    assert compare_last_four(spoken, new, old) == expected


def test_rejects_malformed_input():
    with pytest.raises(ValueError):
        compare_last_four(["12a4"], "4821", None)
    with pytest.raises(ValueError):
        compare_last_four(["4821"], "482", None)


digits = st.text(alphabet="0123456789", min_size=4, max_size=4)


@given(st.lists(st.one_of(st.text(alphabet="0123456789", min_size=1, max_size=4), st.sampled_from(["refused", "dont_know"])), max_size=4),
       digits, st.one_of(st.none(), digits))
def test_always_returns_a_known_result(spoken, new, old):
    assert compare_last_four(spoken, new, old) in LAST_FOUR_RESULTS


@given(digits, digits)
def test_a_match_is_only_ever_the_exact_new_digits(said, new):
    result = compare_last_four([said], new, None)
    assert (result == "match") == (said == new)
