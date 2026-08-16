"""Tests for the Apple Wallet web service settings."""

import pytest
from pydantic import SecretStr, ValidationError

from edutap.wallet_apple_vas_web_service.config import (
    AppleWalletWebServiceSettings,
    DatabaseSettings,
)


def test_accepted_secrets_without_any_configured():
    """No secret configured means nothing is accepted."""
    settings = AppleWalletWebServiceSettings()
    assert settings.accepted_secrets() == []


def test_accepted_secrets_with_empty_authentication_secret():
    """An empty authentication_secret is treated as no secret (fail-closed)."""
    settings = AppleWalletWebServiceSettings(authentication_secret=SecretStr(""))
    assert settings.accepted_secrets() == []


def test_accepted_secrets_with_current_only():
    """Only the current secret is returned when no previous secrets are configured."""
    settings = AppleWalletWebServiceSettings(authentication_secret=SecretStr("current-secret"))
    assert settings.accepted_secrets() == ["current-secret"]


def test_accepted_secrets_with_current_and_previous():
    """Current secret comes first, followed by previous secrets in configured order."""
    settings = AppleWalletWebServiceSettings(
        authentication_secret=SecretStr("current-secret"),
        previous_authentication_secrets=[
            SecretStr("older-secret"),
            SecretStr("oldest-secret"),
        ],
    )
    assert settings.accepted_secrets() == [
        "current-secret",
        "older-secret",
        "oldest-secret",
    ]


def test_accepted_secrets_drops_empty_previous_secrets():
    """Empty strings in previous_authentication_secrets are dropped while others survive."""
    settings = AppleWalletWebServiceSettings(
        authentication_secret=SecretStr("current-secret"),
        previous_authentication_secrets=[
            SecretStr("older-secret"),
            SecretStr(""),  # empty — should be dropped
            SecretStr("oldest-secret"),
        ],
    )
    assert settings.accepted_secrets() == [
        "current-secret",
        "older-secret",
        "oldest-secret",
    ]


@pytest.mark.xfail(
    reason=(
        "FileSecretsSource returns file contents as a string without JSON parsing. "
        "Pydantic-settings only parses JSON when values come from environment variables, "
        "not from files. So previous_authentication_secrets (list[SecretStr]) cannot be "
        "loaded via the _FILE convention. Use environment variables or hardcoding instead."
    )
)
def test_previous_authentication_secrets_file_convention(tmp_path, monkeypatch):
    """The _FILE convention should support previous_authentication_secrets from a file."""
    # Create a temporary file containing a JSON array
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text('["older-secret","oldest-secret"]')

    # Point the environment variable to the file
    monkeypatch.setenv(
        "EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_PREVIOUS_AUTHENTICATION_SECRETS_FILE",
        str(secrets_file),
    )

    # Load settings and check if the previous secrets were loaded
    settings = AppleWalletWebServiceSettings()
    assert len(settings.previous_authentication_secrets) == 2
    assert settings.previous_authentication_secrets[0].get_secret_value() == "older-secret"
    assert settings.previous_authentication_secrets[1].get_secret_value() == "oldest-secret"


def test_authentication_cannot_be_switched_off_by_configuration(monkeypatch):
    """No environment variable makes `_authorized` say yes to a wrong token.

    `EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_AUTH_REQUIRED=false` used to make
    `_authorized` return `True` unconditionally -- the full pass to anyone who
    guessed a serial number, from one variable. The field is gone and the
    settings ignore unknown environment variables, so the name is now inert.

    Asserted through `_authorized` rather than through `model_fields`: what
    matters is the answer, not whether an attribute of that particular name
    exists.
    """
    from edutap.wallet_apple_vas_web_service.service import _authorized

    monkeypatch.setenv("EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_AUTH_REQUIRED", "false")
    settings = AppleWalletWebServiceSettings(authentication_secret=SecretStr("a-secret"))

    assert not _authorized(None, settings, "pass.de.lmu.events", "serial-one")
    assert not _authorized("ApplePass wrong", settings, "pass.de.lmu.events", "serial-one")


def test_the_database_password_is_masked_in_repr():
    """`repr(settings)` must not carry the database password in clear text.

    Measured, before this field was a `SecretStr`: `'db-secret' in
    repr(settings)` was `True`, and `settings` is bound at every raise site
    in `producer.py`'s `fetch_pass` -- a strictly easier leak than the
    producer's bearer token, which took three rounds of fixes to close.
    """
    settings = AppleWalletWebServiceSettings(db=DatabaseSettings(password="db-secret"))
    assert "db-secret" not in repr(settings)
    assert settings.db.password.get_secret_value() == "db-secret"


def test_the_database_username_is_not_masked():
    """`username` stays a plain `str` -- a deliberate decision, not an oversight.

    It names a role, not a credential, and does not by itself grant access to
    anything. Pinned so a future "mask everything settings-shaped" pass does
    not quietly change this without the reasoning in `config.py` being
    revisited.
    """
    settings = DatabaseSettings(username="a-role-name")
    assert not isinstance(settings.username, SecretStr)
    assert "a-role-name" in repr(settings)


def test_a_non_positive_producer_timeout_is_rejected():
    """`producer_timeout_seconds` must be strictly positive.

    Measured: `requests.get(timeout=0)` raises a bare `ValueError` from
    `urllib3`, outside the `requests.RequestException` family `producer.py`
    otherwise handles, before any network call happens. Rejecting the value
    here removes the one trigger for that path known today; `producer.py`'s
    broad exception handling is what closes the path itself.
    """
    with pytest.raises(ValidationError):
        AppleWalletWebServiceSettings(producer_timeout_seconds=0)
