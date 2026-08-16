"""Properties of the route functions themselves, independent of any database."""

import inspect

import pytest

from edutap.wallet_apple_vas_web_service import service
from edutap.wallet_apple_vas_web_service.service import _content_disposition

ROUTES = (
    "register_pass",
    "list_updatable_passes",
    "send_updated_pass",
    "unregister_pass",
    "device_log",
)


def test_no_route_runs_its_body_on_the_event_loop():
    """Every route is `def`, not `async def` -- see the module docstring.

    All five do blocking work: psycopg2 through SQLModel, and `requests.get`
    in `send_updated_pass`. FastAPI runs a sync route in a threadpool and an
    `async def` route body on the event loop, so one `async` keyword here means
    one unreachable producer stalls every request that worker is serving.

    Asserted against the function objects rather than by reading the source:
    `iscoroutinefunction` is what FastAPI itself asks, and it survives a
    decorator or a rename that a grep would not.
    """
    for name in ROUTES:
        route = getattr(service, name)
        assert not inspect.iscoroutinefunction(route), (
            f"{name} is async; its blocking body would run on the event loop"
        )


def test_an_ordinary_serial_number_keeps_its_filename():
    """The common case is unchanged: the serial appears in both forms, as itself."""
    value = _content_disposition("b2c3d4e5-0000-4000-8000-000000000001")
    assert 'filename="b2c3d4e5-0000-4000-8000-000000000001.pkpass"' in value
    assert "filename*=UTF-8''b2c3d4e5-0000-4000-8000-000000000001.pkpass" in value


@pytest.mark.parametrize(
    "serial",
    [
        pytest.param('a";x="y', id="closes-the-quoted-string"),
        pytest.param("a\r\nX-Injected: 1", id="header-injection"),
        pytest.param("a; filename=other", id="extra-parameter"),
        pytest.param("Ausweis-Müller", id="non-ascii"),
        pytest.param("a\\b", id="backslash-escape"),
    ],
)
def test_a_hostile_serial_number_cannot_escape_the_header(serial):
    """The serial is caller-controlled and used to be interpolated unescaped.

    It arrives in the URL, and this endpoint answers for a pass the service has
    never heard of, so nothing upstream constrains it. A double quote closes
    the quoted string and everything after it is read as further parameters; a
    CR or LF is a header injection, which the ASGI server rejects with a 500 on
    an endpoint whose contract is 200 or 401.

    The check is structural rather than a list of forbidden characters: the
    value has to parse as exactly two parameters, and the raw serial must not
    appear in it at all.
    """
    value = _content_disposition(serial)

    assert "\r" not in value and "\n" not in value
    assert serial not in value
    # `attachment`, `filename="..."`, `filename*=...` -- and nothing else that a
    # header parser would read as a parameter of its own.
    assert value.count(";") == 2
    assert value.count('"') == 2
    assert value.startswith('attachment; filename="')
    assert "; filename*=UTF-8''" in value
