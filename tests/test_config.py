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


def test_previous_authentication_secrets_file_convention(tmp_path, monkeypatch):
    """A complex field loads from a `_FILE` secret, as JSON.

    This was an `xfail` on the claim that reworking `FileSecretsSource` would
    touch every settings field. It does not: the source reports each field's
    own `field_is_complex`, decodes in `prepare_field_value`, and calls that
    step from `__call__` -- which it never did. A retired issuer secret is
    exactly the kind of value that should be able to arrive as a Docker secret
    rather than as an environment variable.
    """
    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text('["older-secret","oldest-secret"]')

    monkeypatch.setenv(
        "EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_PREVIOUS_AUTHENTICATION_SECRETS_FILE",
        str(secrets_file),
    )

    settings = AppleWalletWebServiceSettings()
    assert len(settings.previous_authentication_secrets) == 2
    assert settings.previous_authentication_secrets[0].get_secret_value() == "older-secret"
    assert settings.previous_authentication_secrets[1].get_secret_value() == "oldest-secret"


def test_a_simple_field_still_loads_from_a_file_verbatim(tmp_path, monkeypatch):
    """The plain-string path is unaffected by the decoding added for complex fields.

    A secret is an arbitrary string. If the new branch reached it, a value that
    happens to look like JSON -- a number, `null`, a quoted string -- would come
    back as something other than itself, and one that does not would raise.
    """
    secret_file = tmp_path / "issuer-secret"
    secret_file.write_text("  12345  \n")

    monkeypatch.setenv(
        "EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_AUTHENTICATION_SECRET_FILE", str(secret_file)
    )

    settings = AppleWalletWebServiceSettings()
    assert settings.authentication_secret.get_secret_value() == "12345"


def test_a_malformed_secrets_file_is_reported_without_its_contents(tmp_path, monkeypatch):
    """The raised error names the variable and the field, and carries no chain.

    `json.JSONDecodeError` keeps the whole document it failed to parse in its
    `doc` attribute, and here that document is the plaintext of a secrets file.
    Chaining it -- with `from error`, or with `from None`, which only sets
    `__suppress_context__` -- would put that on the raised exception's object
    graph, where an error tracker that walks the chain collects it as an
    exception *attribute*. That is what the capture-then-raise shape in
    `prepare_field_value` prevents, and it is what this pins.

    **What it does not pin**, deliberately: the file's contents are still a
    frame local of `prepare_field_value` on the raised exception's traceback.
    That cannot be helped -- `value` is the parameter the function was called
    with, the same accepted residue `producer.py` documents for `settings`, and
    pydantic-settings' own `EnvSettingsSource` has exactly the same shape. A
    `capture_locals` tracker sees it either way; what this fix removes is the
    second, easier path, not the first.
    """
    from pydantic_settings import SettingsError

    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text('["a-retired-secret", not json at all')

    monkeypatch.setenv(
        "EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_PREVIOUS_AUTHENTICATION_SECRETS_FILE",
        str(secrets_file),
    )

    with pytest.raises(SettingsError) as excinfo:
        AppleWalletWebServiceSettings()

    assert "a-retired-secret" not in str(excinfo.value)
    assert "previous_authentication_secrets" in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


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
