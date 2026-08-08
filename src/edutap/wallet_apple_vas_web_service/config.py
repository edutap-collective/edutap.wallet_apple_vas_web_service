from edutap.wallet_apple.settings import AppleWalletSettings
from pathlib import Path
from pydantic import Field
from pydantic import HttpUrl
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings
from pydantic_settings import PydanticBaseSettingsSource
from pydantic_settings import SettingsConfigDict
from typing import Any

import os


class FileSecretsSource(PydanticBaseSettingsSource):
    """Support the Docker ``<ENV>_FILE`` convention shared across eduTAP services.

    For any field, if ``<env_prefix><field>_FILE`` (case-insensitive) points to an
    existing file, the field takes that file's stripped contents. This lets
    secrets arrive as Docker secrets (``/run/secrets/<name>``) instead of via the
    environment.
    """

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        prefix = self.config.get("env_prefix", "") or ""
        base = f"{prefix}{field_name}_file"
        for env_name in (base, base.upper()):
            path = os.environ.get(env_name)
            if path and Path(path).is_file():
                return Path(path).read_text().strip(), field_name, False
        return None, field_name, False

    def prepare_field_value(
        self, field_name: str, field: FieldInfo, value: Any, value_is_complex: bool
    ) -> Any:
        return value

    def __call__(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for field_name, field in self.settings_cls.model_fields.items():
            value, key, _ = self.get_field_value(field, field_name)
            if value is not None:
                data[key] = value
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
    """ """

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

    username: str | None = None
    password: str | None = None


class AppleWalletWebServiceSettings(_FileAwareSettings):
    """ """

    model_config = SettingsConfigDict(
        env_prefix="EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_",
        case_sensitive=False,
        extra="ignore",
    )

    auth_required: bool = True
    log_file_path: Path = Path("/") / "var" / "log" / "wallet_apple_vas_web_service"

    url: HttpUrl | None = None
    authentication_token: str | None = None

    bootstrap_servers: str | None = None
    topic: str | None = None

    apple: AppleWalletSettings = AppleWalletSettings()
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)


def get_settings() -> AppleWalletWebServiceSettings:
    print("Read AppleWalletWebServiceSettings")
    print(AppleWalletWebServiceSettings())
    return AppleWalletWebServiceSettings()
