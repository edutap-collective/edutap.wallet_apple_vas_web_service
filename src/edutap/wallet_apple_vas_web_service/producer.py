"""Fetching a built pass from the one producer this deployment is configured with.

The pass content belongs to whoever built it. This service holds registrations
and asks for the current pass by Apple's key alone -- it knows no person, no
template and no validity, and it resolves nothing at runtime: there is exactly
one producer per deployment, named in configuration.
"""

import requests

from .config import AppleWalletWebServiceSettings


class ProducerError(Exception):
    """The producer could not be reached, or answered in a way we cannot use."""


class PassNotAvailable(ProducerError):
    """The producer knows this pass and will not hand it out."""


def _producer_headers(settings: AppleWalletWebServiceSettings) -> dict[str, str]:
    """Return the headers `fetch_pass` sends, token included.

    Built in its own frame and returned, not assigned to a local in `fetch_pass`:
    a raised exception carries the frames it passed through, and any tool that
    reads locals off a traceback (an error tracker, `pdb`, `traceback.extract_tb`
    with `capture_locals`) would otherwise find the literal `Bearer <token>`
    sitting in `fetch_pass`'s frame regardless of `from None` below -- that only
    suppresses the *chained* exception's traceback, not this frame's own locals.
    Nothing in this package captures locals today, but `edutap.observability_settings`
    is a planned dependency, and the property should hold by construction rather
    than by nobody adding one later.
    """
    if settings.producer_api_token is None:
        return {}
    return {"Authorization": f"Bearer {settings.producer_api_token.get_secret_value()}"}


def fetch_pass(
    settings: AppleWalletWebServiceSettings,
    pass_type_identifier: str,
    serial_number: str,
) -> bytes:
    """Return the current `.pkpass` for one pass."""
    if not settings.producer_pass_url_template:
        raise ProducerError("No producer configured: producer_pass_url_template is unset.")

    url = settings.producer_pass_url_template.format(
        pass_type_identifier=pass_type_identifier, serial_number=serial_number
    )

    try:
        response = requests.get(
            url, headers=_producer_headers(settings), timeout=settings.producer_timeout_seconds
        )
    except requests.RequestException:
        # The URL is not repeated in the message: it is built from settings that
        # carry no secret, but the exception travels into logs and error
        # trackers, and the token is in the headers of the request object the
        # original exception references. `from None` suppresses that chained
        # exception's traceback; the headers are also never a local of this
        # frame -- see `_producer_headers`.
        raise ProducerError("The producer is not reachable.") from None

    if response.status_code in (404, 410):
        raise PassNotAvailable(f"The producer does not serve {serial_number!r}.")
    if response.status_code != 200:
        raise ProducerError(f"The producer answered {response.status_code}.")
    return response.content
