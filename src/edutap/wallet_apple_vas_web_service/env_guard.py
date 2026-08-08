"""Refuse to start when the deployment still carries the pre-rename settings prefix.

Every field of the settings model has a default. A deployment that still exports
the old prefix would therefore not fail -- it would start cleanly against the
development database and publish nowhere. This turns that silent misconfiguration
into a loud one.

TEMPORARY. Remove once production and staging are on the new prefix; see
docs/superpowers/specs/2026-08-08-paket-umbenennung-runde-2-design.md in
lmu_edutap_dev_setup.
"""

from collections.abc import Mapping

RETIRED_PREFIX = "edutap_apple_wallet_vas_web_service_"
CURRENT_PREFIX = "EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_"


def check_retired_env_prefix(environ: Mapping[str, str]) -> None:
    """Raise if any variable still uses the retired prefix.

    Matching is case-insensitive because pydantic-settings resolves environment
    variables that way -- a deployment using either spelling would be picked up
    by the old code and must therefore be caught here.
    """
    stale = sorted(
        key for key in environ if key.lower().startswith(RETIRED_PREFIX.lower())
    )
    if not stale:
        return
    raise RuntimeError(
        f"Found {len(stale)} environment variable(s) with the retired prefix "
        f"'{RETIRED_PREFIX}': {', '.join(stale)}. "
        f"This service now reads '{CURRENT_PREFIX}'. "
        f"Update the deployment configuration."
    )
