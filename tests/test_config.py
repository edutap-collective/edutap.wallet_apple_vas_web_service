"""Tests for the Apple Wallet web service settings."""

import pytest
from pydantic import SecretStr

from edutap.wallet_apple_vas_web_service.config import AppleWalletWebServiceSettings


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
