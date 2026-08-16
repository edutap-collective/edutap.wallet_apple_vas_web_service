"""Fetching a built pass from the one producer this deployment is configured with.

The pass content belongs to whoever built it. This service holds registrations
and asks for the current pass by Apple's key alone -- it knows no person, no
template and no validity, and it resolves nothing at runtime: there is exactly
one producer per deployment, named in configuration.

The property this module holds throughout: no frame reachable from a raised
exception -- including through `__context__` and `__cause__`, not only the
exception's own traceback -- binds anything from which the producer's bearer
token is reachable in plain form. Not the header dict, not the
`requests.Response` (whose `.request.headers` carries the same token), not a
`PreparedRequest`, and not the original `requests.RequestException` itself:
`raise ... from None` only sets `__suppress_context__`, which keeps a printed
traceback from showing the chained exception -- `__context__` still points at
it, and its own frames still hold the failed request. That is measured, not
assumed: `edutap.data_provider`'s `api/routers.py` and `api/app.py` record an
error tracker that sends the full `__cause__` chain regardless of the
suppression flag, and both close it the same way this module now does --
capture what is safe to know inside the `except`, then raise after it, with
no exception being handled at that point, so the original is genuinely
absent from the new exception's object graph rather than merely hidden from
it. The cost, accepted there and here for the same reason: the traceback no
longer shows which call inside the `try` failed.

Every raise in `fetch_pass` happens from a frame that has, at most,
primitives in scope plus `settings` itself: a URL built from non-secret
settings, an HTTP status code, and the pass bytes it is this function's job
to return anyway. `settings` is unavoidably a local at every raise site -- it
is the function's own parameter -- but it holds the token behind a
`SecretStr`, which is pydantic's own answer to the same threat: its
`repr()`/`str()` and its JSON dump are masked, so it does not hand the token
to a generic serializer either. Reaching it from `settings` needs an explicit
`.get_secret_value()` call, not passive introspection.
"""

import requests

from .config import AppleWalletWebServiceSettings


class ProducerError(Exception):
    """The producer could not be reached, or answered in a way we cannot use."""


class PassNotAvailable(ProducerError):
    """The producer knows this pass and will not hand it out."""


def _producer_headers(settings: AppleWalletWebServiceSettings) -> dict[str, str]:
    """Return the headers `_get` sends, token included.

    Built in its own frame and passed straight through, never assigned to a
    local of `fetch_pass`: see the module docstring for why that matters.
    """
    if settings.producer_api_token is None:
        return {}
    return {"Authorization": f"Bearer {settings.producer_api_token.get_secret_value()}"}


def _get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    """Perform the GET and return only the status code and body.

    Not a `requests.Response`: its `.request.headers` carries the same bearer
    token `_producer_headers` built, so returning it would hand the token right
    back to `fetch_pass` as a local -- undoing the point of not binding
    `headers` there. `response` lives only in this frame, and only for the
    line that reduces it to primitives; this frame is popped, off any
    traceback, before `fetch_pass` ever raises on what it decides from them.
    """
    response = requests.get(url, headers=headers, timeout=timeout)
    return response.status_code, response.content


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

    # `result` stays `None` on a `RequestException`; nothing about the failure
    # is captured, because the constant message below needs nothing dynamic
    # -- unlike `data_provider`'s `_describe`, which does have safe facts
    # worth recording. Whichever branch runs, the raise for it happens after
    # this block, not inside the `except`: see the module docstring for why.
    result: tuple[int, bytes] | None = None
    try:
        result = _get(url, _producer_headers(settings), settings.producer_timeout_seconds)
    except requests.RequestException:
        pass

    if result is None:
        raise ProducerError("The producer is not reachable.")
    status_code, content = result

    if status_code in (404, 410):
        raise PassNotAvailable(f"The producer does not serve {serial_number!r}.")
    if status_code != 200:
        raise ProducerError(f"The producer answered {status_code}.")
    return content
