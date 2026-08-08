"""The retired settings prefix must stop the service, not be ignored."""

import pytest

from edutap.wallet_apple_vas_web_service.env_guard import (
    RETIRED_PREFIX,
    check_retired_env_prefix,
)


def test_clean_environment_passes():
    check_retired_env_prefix({"EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_DB_HOST": "db"})


def test_empty_environment_passes():
    check_retired_env_prefix({})


def test_retired_prefix_raises():
    with pytest.raises(RuntimeError) as excinfo:
        check_retired_env_prefix({f"{RETIRED_PREFIX}db_host": "db"})
    message = str(excinfo.value)
    assert RETIRED_PREFIX in message
    assert "EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_" in message


def test_detection_is_case_insensitive():
    """Environment variables are matched case-insensitively by pydantic-settings,
    so a deployment shouting the old prefix must be caught just the same."""
    with pytest.raises(RuntimeError):
        check_retired_env_prefix({RETIRED_PREFIX.upper() + "DB_HOST": "db"})


def test_message_names_every_offending_variable():
    with pytest.raises(RuntimeError) as excinfo:
        check_retired_env_prefix(
            {
                f"{RETIRED_PREFIX}db_host": "db",
                f"{RETIRED_PREFIX}db_name": "edutap",
                "PATH": "/usr/bin",
            }
        )
    message = str(excinfo.value)
    assert f"{RETIRED_PREFIX}db_host" in message
    assert f"{RETIRED_PREFIX}db_name" in message
    assert "PATH" not in message
    assert "2 environment variable" in message


def test_new_prefix_is_not_mistaken_for_the_old_one():
    """The new prefix does not contain the old one, but a sloppy `in` check would
    still match on the shared tail. Guard against that regression."""
    check_retired_env_prefix(
        {
            "EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_DB_HOST": "db",
            "EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_KAFKA_TOPIC": "t",
        }
    )
