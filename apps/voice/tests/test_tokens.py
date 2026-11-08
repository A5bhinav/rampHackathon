"""The one-time call token is bound to the case, the attempt and the dialed number: a token minted
for one call never verifies for another (design, "How a call runs", step 2)."""
from __future__ import annotations

import uuid

from dialback_voice.tokens import mint, verify

KEY = b"call-token-key-for-tests-only-0123456789"
CASE = uuid.UUID("c0ffee00-1111-4222-8333-444455556666")
NUMBER = "+14155550100"


def test_a_minted_token_verifies_for_the_same_call():
    tok = mint(KEY, case_id=CASE, attempt=1, number_e164=NUMBER)
    assert verify(KEY, tok, case_id=CASE, attempt=1, number_e164=NUMBER)


def test_mint_is_deterministic():
    assert mint(KEY, case_id=CASE, attempt=1, number_e164=NUMBER) == \
        mint(KEY, case_id=CASE, attempt=1, number_e164=NUMBER)


def test_a_token_does_not_verify_for_a_different_call():
    tok = mint(KEY, case_id=CASE, attempt=1, number_e164=NUMBER)
    assert not verify(KEY, tok, case_id=CASE, attempt=2, number_e164=NUMBER)          # another attempt
    assert not verify(KEY, tok, case_id=uuid.uuid4(), attempt=1, number_e164=NUMBER)  # another case
    assert not verify(KEY, tok, case_id=CASE, attempt=1, number_e164="+14155550199")  # another number
    assert not verify(b"a-different-service-key-0123456789abcd", tok,
                      case_id=CASE, attempt=1, number_e164=NUMBER)                     # another key


def test_a_garbage_token_is_refused():
    assert not verify(KEY, "not-a-real-token", case_id=CASE, attempt=1, number_e164=NUMBER)
