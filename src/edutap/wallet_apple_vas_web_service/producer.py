"""Fetching a built pass from the one producer this deployment is configured with.

The pass content belongs to whoever built it. This service holds registrations
and asks for the current pass by Apple's key alone -- it knows no person, no
template and no validity, and it resolves nothing at runtime: there is exactly
one producer per deployment, named in configuration.

The property this module aims to hold, stated precisely rather than rounded
up -- an earlier version of this docstring overclaimed it, which is exactly
the failure mode this comment now exists to prevent a reader from repeating:

    No frame reachable from a raised `Exception` -- through its own
    traceback, or through `__context__`/`__cause__` -- binds the producer's
    bearer token or the database password in a form a generic `repr()`-based
    capture tool would expose.

`Exception`, deliberately, not "a raised exception" unqualified -- see the
two named exceptions to this property near the end of this docstring before
treating it as unconditional.

What makes that true, mechanism by mechanism:

- The header dict, and the `requests.Response`/`PreparedRequest` whose
  `.request.headers` carries the same token: never bound in `fetch_pass`'s
  own frame at all (`_producer_headers`, `_get`).
- The original exception `requests.get` raises: `raise ... from None` alone
  is not enough -- it only sets `__suppress_context__`, which keeps a
  *printed* traceback from showing the chained exception, but `__context__`
  still points at it and its own frames still hold the failed request.
  Measured for this estate, not assumed: `edutap.data_provider`'s
  `api/routers.py` and `api/app.py` record an error tracker that sends the
  full `__cause__` chain regardless of the suppression flag. `fetch_pass`
  closes it the way both of those do -- capture what is safe to know inside
  the `except`, then raise after it, with no exception being handled at that
  point, so the original is genuinely absent from the new exception's object
  graph rather than merely hidden from it.
- The `str.format` on the URL template: a template this deployment got
  wrong raises `KeyError`/`IndexError`/`ValueError` from a frame that is
  `fetch_pass`'s own, and the exception is not chained out of the `except`
  either. Its own exposure is small -- a `KeyError` names a placeholder,
  not a value -- but the file keeps one rule rather than two, and a
  reader should not have to work out which raise sites are covered.
- Not only `requests.RequestException`: `requests.get` can also raise a
  plain exception from underneath it -- measured, a non-positive
  `producer_timeout_seconds` reaches a bare `ValueError` in `urllib3`, before
  `requests` itself gets a chance to guard it, and that path is not a
  `RequestException`. `fetch_pass` catches broadly around the request call
  for exactly this reason: the positivity constraint on
  `producer_timeout_seconds` (see `config.py`) removes the one trigger that
  is known, the broad catch removes the exposure for any trigger that isn't.

The cost of both exception-handling fixes, accepted for the same reason
`data_provider` accepts it: the traceback no longer shows which call inside
the `try` failed, and neither does a bare `str(exception)`. `error_type`
(the failing exception's class name, nothing more) is kept and put in the
message raised afterward so an operator is not left with a message that
cannot distinguish a DNS failure from a broken TLS handshake -- see the
comment in `fetch_pass`.

What the property above does **not** cover -- two things, named here rather
than left for a reader to assume, because a false "closed" is worse than an
honest "not closed":

1. `settings` is unavoidably a local at every raise site in `fetch_pass` --
   it is the function's own parameter, carrying both the producer's bearer
   token and (through `settings.db`) the database password. Both now sit
   behind pydantic's `SecretStr`, which defeats `repr()`, `str()`, and JSON
   serialization -- the class of capture this module defends against
   throughout. It does not defeat a tool that walks object attributes
   looking for a `SecretStr` specifically and reads its private
   `_secret_value`, or that calls `.get_secret_value()` itself. That gap is
   accepted, not closed: no code in this codebase treats `SecretStr` as a
   complete defense against a deliberately targeted extraction, only against
   generic serialization -- and `settings` cannot be kept out of
   `fetch_pass`'s frame the way the header dict and the response were,
   because the function genuinely needs it.

2. `BaseException` subclasses outside `Exception` -- `KeyboardInterrupt`,
   `SystemExit`, `GeneratorExit` -- are not caught by `except Exception:`
   below and escape `fetch_pass` with `_get`'s frame (and `requests`' own
   frames) still attached, `headers` and all. Measured: raising
   `KeyboardInterrupt` from inside the mocked `requests.get` call lets it
   propagate out of `fetch_pass` uncaught, and
   `traceback.TracebackException.from_exception(error, capture_locals=True)`
   renders the plaintext bearer token from `_get`'s frame. This is
   deliberately **not** closed by widening the catch to `except
   BaseException:` -- swallowing a `KeyboardInterrupt` or `SystemExit` into a
   503 would be a worse defect than the leak it would hide, since it would
   mean this function absorbs the process's own shutdown signal. The reach is
   narrow in practice: this service's Dockerfile runs `uvicorn`, which
   installs its own `SIGINT` handling, and `fetch_pass` is fully synchronous,
   so an `asyncio.CancelledError` (itself a `BaseException` subclass) cannot
   interrupt it mid-call either. What remains is a runner that leaves
   Python's default `SIGINT` handler in place, combined with a capture tool
   that records `BaseException` and not only `Exception` -- accepted, not
   fixed, because the fix on offer is strictly worse than the exposure.
"""

import requests

from .config import AppleWalletWebServiceSettings


class ProducerError(Exception):
    """The producer could not be reached, or answered in a way we cannot use.

    Answered `503` towards the device: something on our side is wrong or
    temporarily broken, and the device should come back.
    """


class PassNotAvailable(ProducerError):
    """The producer knows this pass and will not hand it out — permanently.

    Answered `401` towards the device, which is what Apple leaves us: the
    delivery endpoint documents `200` and `401` and nothing else, so "this pass
    is gone" has to be said as "your credential is not good for this pass".

    Raised for `410 Gone` alone. A `404` is *not* this: see `fetch_pass`.
    """


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
    token `_producer_headers` built, so returning it would hand the token
    right back to `fetch_pass` as a local -- undoing the point of not binding
    `headers` there. `response` lives only in this frame, and only for the
    line that reduces it to primitives.

    This frame's own exception references never survive past `fetch_pass`'s
    `except Exception:` around the call to this function -- true regardless
    of what `requests.get` raises, `RequestException` or otherwise, because
    that catch is broad and does not chain. See the module docstring.
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

    # Its own `try`, and deliberately not folded into the broad one below.
    # `str.format` raises on a template this deployment got wrong -- `KeyError`
    # for a placeholder nobody supplies, `IndexError` for a positional `{}`,
    # `ValueError` for an unmatched brace or a bad conversion -- and an
    # unguarded call turns a configuration mistake into a 500 on an endpoint
    # whose contract is 200 or 401. It must not go inside the `except Exception`
    # around the request either: that block's message says the producer is not
    # reachable, and reporting a typo in our own configuration as an unreachable
    # producer would send an operator to look at the wrong machine.
    #
    # Captured and raised afterwards, the same shape as below, so no exception
    # is being handled at the raise point -- see the module docstring. The
    # exposure here is smaller (a `KeyError` names a placeholder, not a value),
    # but one rule in one file is worth more than two.
    url: str | None = None
    template_error: str | None = None
    try:
        url = settings.producer_pass_url_template.format(
            pass_type_identifier=pass_type_identifier, serial_number=serial_number
        )
    except (KeyError, IndexError, ValueError, AttributeError) as error:
        template_error = type(error).__name__

    if url is None:
        raise ProducerError(f"The producer URL template is malformed ({template_error}).")

    # Caught broadly, not `except requests.RequestException:`: `requests.get`
    # can also raise a plain exception from underneath it (a non-positive
    # `producer_timeout_seconds` is one measured trigger, guarded against in
    # `config.py`, but not the only conceivable one), and every one of those
    # exception classes carries the same failed request through its own
    # frames as a `RequestException` would. `error_type` is the one fact kept
    # -- a class name, never a value -- so the message below can still tell a
    # DNS failure from a broken handshake; nothing more is captured, and
    # nothing is raised inside this block. The raise for either outcome
    # happens after it, with no exception being handled at that point: see
    # the module docstring for why that -- not `from None` -- is what keeps
    # the original genuinely out of the new exception's object graph.
    result: tuple[int, bytes] | None = None
    error_type: str | None = None
    try:
        result = _get(url, _producer_headers(settings), settings.producer_timeout_seconds)
    except Exception as error:
        error_type = type(error).__name__

    if result is None:
        raise ProducerError(f"The producer is not reachable ({error_type}).")
    status_code, content = result

    # `410` and `404` are not the same answer, and collapsing them cost the
    # recoverable one its recovery. `410 Gone` is the producer stating that this
    # pass is permanently withdrawn -- there is nothing to come back for, and
    # `PassNotAvailable` turns it into the `401` that is the strongest thing
    # Apple's two documented codes let us say. `404` is the producer not having
    # the pass *right now*: a deploy mid-flight, a restored replica, a
    # mistyped template. Telling a device its credential is dead because a
    # producer lost a pass for thirty seconds is unrecoverable from the
    # device's side -- Wallet does not re-authenticate -- while a `503` is a
    # "come back later" the device already handles.
    if status_code == 410:
        raise PassNotAvailable(f"The producer no longer serves {serial_number!r}.")
    if status_code != 200:
        raise ProducerError(f"The producer answered {status_code}.")
    return content
