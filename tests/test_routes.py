"""Properties of the route functions themselves, independent of any database."""

import inspect

from edutap.wallet_apple_vas_web_service import service

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
