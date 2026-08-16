"""Fetching a built pass from the configured producer."""

import pytest
import requests

from edutap.wallet_apple_vas_web_service.config import AppleWalletWebServiceSettings
from edutap.wallet_apple_vas_web_service.producer import (
    PassNotAvailable,
    ProducerError,
    fetch_pass,
)

PTID = "pass.de.lmu.events"
SERIAL = "serial-one"
TEMPLATE = "https://builder.invalid/api/v1/passes/{pass_type_identifier}/{serial_number}"
EXPECTED_URL = f"https://builder.invalid/api/v1/passes/{PTID}/{SERIAL}"


@pytest.fixture
def settings():
    return AppleWalletWebServiceSettings(
        producer_pass_url_template=TEMPLATE,
        producer_api_token="a-producer-token",
    )


def test_the_template_names_the_pass(settings, requests_mock):
    requests_mock.get(EXPECTED_URL, content=b"PK\x03\x04")
    assert fetch_pass(settings, PTID, SERIAL) == b"PK\x03\x04"


def test_the_producer_token_is_sent(settings, requests_mock):
    requests_mock.get(EXPECTED_URL, content=b"PK\x03\x04")
    fetch_pass(settings, PTID, SERIAL)
    assert requests_mock.last_request.headers["Authorization"] == "Bearer a-producer-token"


@pytest.mark.parametrize("status", [404, 410])
def test_a_withdrawn_pass_raises_pass_not_available(settings, requests_mock, status):
    requests_mock.get(EXPECTED_URL, status_code=status)
    with pytest.raises(PassNotAvailable):
        fetch_pass(settings, PTID, SERIAL)


def test_a_failing_producer_raises_producer_error(settings, requests_mock):
    requests_mock.get(EXPECTED_URL, status_code=500)
    with pytest.raises(ProducerError):
        fetch_pass(settings, PTID, SERIAL)


def test_an_unconfigured_producer_raises_producer_error():
    with pytest.raises(ProducerError):
        fetch_pass(AppleWalletWebServiceSettings(), PTID, SERIAL)


def _fetch_pass_frame(traceback):
    """Return the frame belonging to `fetch_pass` from a raised exception's traceback.

    Not "every frame of the traceback": the calling test frame is part of that
    chain too, and frame locals reflect live state at inspection time, not at
    raise time -- any local the test itself binds before the check, however
    unrelated, would then read back as "found" and produce a false positive
    with no bearing on `fetch_pass`. Isolating the one frame under test avoids
    that trap.
    """
    while traceback.tb_frame.f_code.co_name != "fetch_pass":
        traceback = traceback.tb_next
    return traceback.tb_frame


@pytest.mark.parametrize(
    "mock_kwargs",
    [
        pytest.param({"exc": requests.ConnectionError("boom")}, id="connection-failure"),
        pytest.param(
            {"exc": ValueError("timeout value out of bounds")}, id="non-request-exception"
        ),
        pytest.param({"status_code": 404}, id="withdrawn-404"),
        pytest.param({"status_code": 410}, id="withdrawn-410"),
        pytest.param({"status_code": 500}, id="producer-failure"),
    ],
)
def test_the_token_never_becomes_a_frame_local_on_a_producer_error(
    settings, requests_mock, mock_kwargs
):
    """The property the module docstring states, pinned structurally, on all five raises.

    Two independent checks, because the token is reachable from a local in two
    different shapes and only one of them is a string match:

    - directly, as a substring of a local's `repr()` -- the shape a `headers`
      dict bound in `fetch_pass` had before the first fix, and the shape a
      message-string assertion ("Bearer" not in str(exception)) would have
      missed just the same, since the token was never in the *message*.
    - structurally, as a `requests.Response` or `requests.PreparedRequest`
      object whose `.headers` / `.request.headers` carries the token without
      it ever showing in `repr()` -- `repr(response)` is just `<Response
      [404]>`. A tool that captures frame locals via `repr()` (Python's own
      `traceback.extract_tb(..., capture_locals=True)`) would not catch this
      shape, but one that walks object attributes (as some error trackers do)
      would -- this is the shape the second fix closed, and a `repr()`-only
      check would pass against it by accident, same as the message-string
      check it replaces.

    `non-request-exception` uses a bare `ValueError`, not a
    `requests.RequestException` subclass -- the shape `requests.get(timeout=0)`
    actually raises (measured), and the shape the fourth fix closed. Before
    that fix, `except requests.RequestException:` did not catch it at all, so
    it escaped `fetch_pass` with `_get`'s frame (and `requests`' own internal
    frames) still attached, `headers` and all.
    """
    requests_mock.get(EXPECTED_URL, **mock_kwargs)
    with pytest.raises(ProducerError) as excinfo:
        fetch_pass(settings, PTID, SERIAL)

    frame = _fetch_pass_frame(excinfo.value.__traceback__)
    for local_value in frame.f_locals.values():
        assert "a-producer-token" not in repr(local_value)
        assert not isinstance(local_value, (requests.Response, requests.PreparedRequest))


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(requests.ConnectionError("boom"), id="connection-failure"),
        pytest.param(ValueError("timeout value out of bounds"), id="non-request-exception"),
    ],
)
def test_a_failed_request_leaves_no_chain_on_the_raised_error(settings, requests_mock, exc):
    """`__context__`/`__cause__` genuinely `None`, not merely suppressed -- for either shape.

    `raise ... from None` alone sets `__suppress_context__`, which keeps a
    *printed* traceback from showing the original exception -- but
    `__context__` still points at it, and that exception's own frames still
    hold the failed request with its headers. `edutap.data_provider` measured
    that an error tracker sends the full chain regardless of the suppression
    flag; see the module docstring. Asserting `__context__ is None` is the
    cheap, exact way to pin that the original is genuinely absent from this
    exception's object graph, not merely hidden from a rendering of it -- for
    a `RequestException` and for the exception class outside that family the
    fourth fix started catching too.
    """
    requests_mock.get(EXPECTED_URL, exc=exc)
    with pytest.raises(ProducerError) as excinfo:
        fetch_pass(settings, PTID, SERIAL)

    assert excinfo.value.__context__ is None
    assert excinfo.value.__cause__ is None


def test_the_message_names_the_failing_exception_class(settings, requests_mock):
    """The one fact kept for an operator: which exception class failed, not a constant message.

    `except requests.RequestException: pass` (the shape introduced while
    closing the `__context__` leak) made a DNS-dead producer and a
    reachable-but-TLS-broken one indistinguishable in the raised message --
    a real diagnosability regression, even though it carried no credential.
    `data_provider`'s pattern explicitly permits recording what an operator
    needs inside the `except`; it only forbids carrying the exception itself
    out. A class name identifies neither this deployment's producer nor its
    credential, so it is safe to keep.
    """
    requests_mock.get(EXPECTED_URL, exc=requests.ConnectTimeout("boom"))
    with pytest.raises(ProducerError, match="ConnectTimeout"):
        fetch_pass(settings, PTID, SERIAL)


def test_a_keyboard_interrupt_is_not_swallowed(settings, requests_mock):
    """Pins the round-5 decision not to widen the catch to `except BaseException:`.

    `KeyboardInterrupt` is deliberately not caught by `fetch_pass` -- see the
    module docstring's second named exception to the no-leak property.
    `except Exception:` does not match it, so it propagates unmodified rather
    than becoming a `ProducerError`/503. If this test starts failing because
    `fetch_pass` began catching it, that is exactly the "fix" the module
    docstring argues against: swallowing a process shutdown signal is a worse
    defect than the narrow, low-severity leak it would hide. This test exists
    so that change cannot happen silently.
    """
    requests_mock.get(EXPECTED_URL, exc=KeyboardInterrupt("interrupted"))
    with pytest.raises(KeyboardInterrupt):
        fetch_pass(settings, PTID, SERIAL)
