"""Tests for the pass authentication header check.

The accepted token used to be a constant in ``service.py``, which put a working
credential in a public repository. These tests pin the behaviour it was replaced
with -- above all that a deployment which forgot to configure a token rejects
every request rather than accepting any.
"""

import pytest

from edutap.wallet_apple_vas_web_service.service import check_authentification_token

TOKEN = "a-configured-token"


def test_accepts_the_configured_token():
    assert check_authentification_token(f"ApplePass {TOKEN}", TOKEN) is True


def test_rejects_a_different_token():
    assert check_authentification_token("ApplePass another-token", TOKEN) is False


def test_rejects_a_missing_header():
    assert check_authentification_token(None, TOKEN) is False


@pytest.mark.parametrize(
    "header",
    [
        "Bearer " + TOKEN,  # right token, wrong scheme
        TOKEN,  # no scheme at all
        "ApplePass",  # scheme without a token
        "ApplePass a b",  # more parts than the two expected
        "",
    ],
)
def test_rejects_malformed_headers(header):
    """A malformed header must return False, never raise.

    ``"ApplePass".split()`` yields one element and ``"a b c".split()`` three, both
    of which the previous two-name unpacking turned into a ValueError -- a 500
    where a 401 belongs.
    """
    assert check_authentification_token(header, TOKEN) is False


def test_rejects_everything_when_no_token_is_configured():
    """Fail closed: an unconfigured service must not authenticate anybody."""
    assert check_authentification_token(f"ApplePass {TOKEN}", None) is False
    assert check_authentification_token("ApplePass anything", None) is False


def test_auth_required_false_lets_everything_through():
    """The documented escape hatch for local development, off by default."""
    assert check_authentification_token(None, None, auth_required=False) is True
