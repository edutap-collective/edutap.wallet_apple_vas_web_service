"""Derivation and verification of the per-pass authentication token."""

import pytest

from edutap.wallet_apple_vas_web_service.tokens import derive_token, verify_authorization

SECRET = "an-issuer-secret"
OTHER_SECRET = "a-rotated-away-secret"
PTID = "pass.de.lmu.events"
SERIAL = "b2c3d4e5-0000-4000-8000-000000000001"


def test_derivation_is_stable():
    # Apple forbids changing a token after creation, so the same inputs must
    # always give the same value.
    assert derive_token(SECRET, PTID, SERIAL) == derive_token(SECRET, PTID, SERIAL)


def test_derivation_clears_the_minimum_length():
    # edutap.wallet_apple assumes at least 16 characters.
    assert len(derive_token(SECRET, PTID, SERIAL)) >= 16


def test_different_passes_get_different_tokens():
    assert derive_token(SECRET, PTID, SERIAL) != derive_token(SECRET, PTID, "other-serial")
    assert derive_token(SECRET, PTID, SERIAL) != derive_token(SECRET, "pass.de.lmu.ub", SERIAL)


def test_concatenation_cannot_collide():
    # Without a separator that cannot occur in either identifier, ("ab", "c")
    # and ("a", "bc") would hash the same message.
    assert derive_token(SECRET, "ab", "c") != derive_token(SECRET, "a", "bc")


def test_a_correct_header_is_accepted():
    token = derive_token(SECRET, PTID, SERIAL)
    assert verify_authorization(f"ApplePass {token}", [SECRET], PTID, SERIAL) is True


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "ApplePass",
        "Bearer sometoken",
        "ApplePass wrong-token",
        "ApplePass token with spaces",
    ],
)
def test_malformed_or_wrong_headers_are_rejected(header):
    assert verify_authorization(header, [SECRET], PTID, SERIAL) is False


def test_a_token_of_another_pass_is_rejected():
    token = derive_token(SECRET, PTID, "a-different-serial")
    assert verify_authorization(f"ApplePass {token}", [SECRET], PTID, SERIAL) is False


def test_a_previous_secret_is_still_accepted():
    # Apple: devices may still hold a pass carrying the old token.
    token = derive_token(OTHER_SECRET, PTID, SERIAL)
    assert verify_authorization(f"ApplePass {token}", [SECRET, OTHER_SECRET], PTID, SERIAL) is True


def test_without_configured_secrets_everything_is_rejected():
    token = derive_token(SECRET, PTID, SERIAL)
    assert verify_authorization(f"ApplePass {token}", [], PTID, SERIAL) is False
