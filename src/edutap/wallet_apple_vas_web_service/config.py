"""Settings of the service, including the Docker ``<ENV>_FILE`` secrets convention."""

# Imported under an alias: upstream renamed this class from ``AppleWalletSettings``
# to ``Settings`` in December 2024, and the bare name would sit here next to two
# other settings classes without saying which package it belongs to.
import os
from pathlib import Path
from typing import Any

from edutap.wallet_apple.settings import Settings as WalletAppleSettings
from pydantic import Field, HttpUrl, SecretStr
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
)
from sqlalchemy import URL as SQLAlchemyURL


class FileSecretsSource(PydanticBaseSettingsSource):
    """Support the Docker ``<ENV>_FILE`` convention shared across eduTAP services.

    For any field, if ``<env_prefix><field>_FILE`` (case-insensitive) points to an
    existing file, the field takes that file's stripped contents. This lets
    secrets arrive as Docker secrets (``/run/secrets/<name>``) instead of via the
    environment.

    A **complex** field -- a list, a dict, a model -- takes its file contents as
    JSON, the same way pydantic-settings parses one from an environment
    variable. That is not what this source did: it reported every field as
    simple and never called its own ``prepare_field_value``, so
    ``previous_authentication_secrets`` (a ``list[SecretStr]``) could only be
    configured through the environment. A retired issuer secret is exactly the
    kind of value that should be able to arrive as a Docker secret, and the
    field whose absence silently makes every pass built before a rotation
    unusable is a poor one to have no secure path for.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        """Return the field's value from the file named by ``<PREFIX><FIELD>_FILE``.

        The third element says whether the value still has to be decoded, and it
        is the field's own answer (``field_is_complex``) rather than a constant
        ``False``. A hardcoded ``False`` meant a JSON array read out of a file
        reached the validator as one long string.
        """
        prefix = self.config.get("env_prefix", "") or ""
        base = f"{prefix}{field_name}_file"
        for env_name in (base, base.upper()):
            path = os.environ.get(env_name)
            if path and Path(path).is_file():
                return Path(path).read_text().strip(), field_name, self.field_is_complex(field)
        return None, field_name, False

    def prepare_field_value(
        self, field_name: str, field: FieldInfo, value: Any, value_is_complex: bool
    ) -> Any:
        """Decode a complex field's JSON; pass a simple field's contents through.

        ``decode_complex_value`` rather than a bare ``json.loads``: it is the
        base class's own helper, so ``NoDecode``/``ForceDecode`` and
        ``enable_decoding`` mean here what they mean for an environment
        variable.

        The failure is raised *after* the ``except`` block rather than inside
        it, and nothing about the original exception is carried out but its
        class name. ``json.JSONDecodeError`` keeps the whole document it failed
        to parse in its ``doc`` attribute -- which here is the plaintext
        contents of a secrets file. Chaining it, with ``from error`` or with
        ``from None`` (which only sets ``__suppress_context__``), would put that
        on the raised exception's object graph, where an error tracker that
        walks the chain would collect it as an exception *attribute*. Same
        reasoning, same shape, as ``producer.py``; read its module docstring for
        the measurement.

        Stated precisely rather than rounded up: this closes the chain, not the
        frame. ``value`` is this function's own parameter and is therefore a
        local on the raised exception's traceback, exactly as ``settings`` is in
        ``producer.fetch_pass`` -- and as the file's contents are in
        pydantic-settings' own ``EnvSettingsSource``. A tool that captures frame
        locals still sees it. The easier path is removed; the harder one is
        accepted, and named here so nobody reads this as more than it is.
        """
        if value is None or not value_is_complex:
            return value
        error_type: str | None = None
        try:
            return self.decode_complex_value(field_name, field, value)
        except ValueError as error:
            error_type = type(error).__name__
        variable = f"{self.config.get('env_prefix', '') or ''}{field_name}_file".upper()
        raise SettingsError(
            f"{variable} does not contain valid JSON for the complex field "
            f"{field_name!r} ({error_type})."
        )

    def __call__(self) -> dict[str, Any]:
        """Collect every field that has a readable ``_FILE`` companion.

        Goes through ``prepare_field_value``, which it used to skip -- the third
        element of ``get_field_value`` was discarded and the value stored raw.
        A source that never calls its own preparation step cannot decode
        anything, however correct that step is.
        """
        data: dict[str, Any] = {}
        for field_name, field in self.settings_cls.model_fields.items():
            value, key, value_is_complex = self.get_field_value(field, field_name)
            if value is not None:
                data[key] = self.prepare_field_value(field_name, field, value, value_is_complex)
        return data


class _FileAwareSettings(BaseSettings):
    """Base that enables the ``_FILE`` secrets convention, ranked above env vars."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            FileSecretsSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )


class DatabaseSettings(_FileAwareSettings):
    """Connection parameters for the shared eduTAP PostgreSQL database."""

    model_config = SettingsConfigDict(
        env_prefix="EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_DB_",
        case_sensitive=False,
        extra="ignore",
    )

    type: str | None = "postgresql"
    driver: str | None = "psycopg2"
    host: str | None = None
    port: int = 5432
    name: str | None = None

    # `username` stays a plain `str`: it names a role, not a credential, and
    # does not by itself grant access to anything -- the ordinary distinction
    # database tooling already relies on (a username routinely shows up in
    # `pg_stat_activity`, connection logs, and slow-query logs; a password
    # never should). `password` is the actual credential. It was a plain
    # `str` here, meaning `repr(settings)` -- and by extension anything that
    # captures locals or a settings object on an exception -- put it in
    # clear text; see `producer.py`'s module docstring, which documents this
    # module for the same threat model on the producer's bearer token.
    username: str | None = None
    password: SecretStr | None = None

    def url(self) -> SQLAlchemyURL:
        """Return the SQLAlchemy connection URL for these settings.

        A `sqlalchemy.URL` object, not an f-string DSN: `URL.__repr__` and
        `__str__` mask the password by default
        (`render_as_string(hide_password=True)`), the same way `SecretStr`
        does for the fields above. An f-string DSN is the plaintext password
        *as* the value, with nothing to mask it -- measured: passing one
        straight to `create_engine` put it in clear text as a local in three
        frames, including two of SQLAlchemy's own, the moment the URL failed
        to parse. `URL` is hashable, so `get_engine`'s `lru_cache` still
        works with it as the key. `port=None` when the port is the default
        (`5432`), not `self.port` unconditionally: `URL.create` omits a
        `None` port from the rendered string, matching what the previous
        f-string did by omission.
        """
        return SQLAlchemyURL.create(
            drivername=f"{self.type}+{self.driver}",
            username=self.username,
            password=self.password.get_secret_value() if self.password is not None else None,
            host=self.host,
            port=self.port if self.port != 5432 else None,
            database=self.name,
        )


class AppleWalletWebServiceSettings(_FileAwareSettings):
    """Settings of the Apple Wallet web service, prefix
    ``EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_``.
    """

    model_config = SettingsConfigDict(
        env_prefix="EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_",
        case_sensitive=False,
        extra="ignore",
    )

    # There is deliberately no `auth_required` switch. It existed, defaulted to
    # `True`, and made `_authorized` return `True` unconditionally when set to
    # `false` -- handing the full pass to anyone who guessed a serial number,
    # from a single environment variable, on an endpoint that is public by
    # design. It appeared in no test, no README and no compose file, so nothing
    # was using it and no deployment that omits it is affected by its removal.
    # A deployment that wants authentication off does not want this service.
    log_file_path: Path = Path("/") / "var" / "log" / "wallet_apple_vas_web_service"

    url: HttpUrl | None = None
    authentication_secret: SecretStr | None = None
    """The issuer secret every pass token is derived from.

    One value, shared with the producer that builds the passes. Arrives from the
    vault, or as a Docker secret through the `_FILE` convention above.
    """

    previous_authentication_secrets: list[SecretStr] = Field(default_factory=list)
    """Secrets rotated away from, newest first.

    Apple: "there may still be devices with the old pass and the old
    authentication token. Your server would have to check the authentication
    token against the list of every token that has ever been valid." A pass
    picks up the new token at its next rebuild; until then its device
    authenticates with an entry from this list.

    From the environment as a JSON array, which is how pydantic-settings parses
    a complex type: `["older-secret", "oldest-secret"]`.
    """

    bootstrap_servers: str | None = None
    topic: str | None = None

    producer_pass_url_template: str | None = None
    """Where to fetch a built pass, with `{pass_type_identifier}` and `{serial_number}`.

    A template rather than a base URL, because the retrieval contract with the
    producer is not settled (see the design document). A deployment supplies the
    shape it agreed, and this service hardcodes no assumption about it.
    """

    producer_api_token: SecretStr | None = None
    """Bearer token this service presents to its producer."""

    producer_timeout_seconds: float = Field(10.0, gt=0)
    """How long to wait for a built pass. Apple's device is waiting behind it.

    `gt=0`, not just a sensible-looking default: a value of `0` (or negative)
    reaches `urllib3`'s own timeout validation, which raises a plain
    `ValueError` -- outside the `requests.RequestException` family `producer.py`
    otherwise handles -- from underneath `requests.get`, before any network
    call happens. Rejecting the value here removes the one trigger for that
    path that is known; `producer.py`'s broad `except Exception` around the
    request call is what closes the path itself, for this and any other
    exception class not yet identified.
    """

    # ``default_factory`` rather than an instance in the class body, matching ``db``
    # below: an instance here is built while this module is imported, so a settings
    # error would surface as an import failure rather than at the call that reads it.
    apple: WalletAppleSettings = Field(default_factory=WalletAppleSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)

    def accepted_secrets(self) -> list[str]:
        """Return every secret a presented token may have been derived from."""
        secrets = [self.authentication_secret, *self.previous_authentication_secrets]
        # An empty string is treated as no secret: fail-closed means an unquoted value
        # expanding to "" in a .env file does not punch a hole in authentication.
        return [
            secret.get_secret_value()
            for secret in secrets
            if secret is not None and secret.get_secret_value()
        ]


def get_settings() -> AppleWalletWebServiceSettings:
    """Build the settings for one request; used as a FastAPI dependency."""
    # Nothing is printed here. The settings carry the database password and the
    # pass authentication token, and printing the instance put both in the container
    # log in clear text on every request.
    return AppleWalletWebServiceSettings()
