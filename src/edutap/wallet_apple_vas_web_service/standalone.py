"""ASGI application and console entry point of the Apple Wallet web service."""

import os
from contextlib import asynccontextmanager
from importlib.metadata import version

import uvicorn
from edutap.observability_settings import (
    OTLP_ENDPOINT_VARIABLE,
    ObservabilitySettings,
    install_observability,
    instrument_fastapi_safely,
)
from fastapi import FastAPI, Request
from fastapi.logger import logger

from .config import AppleWalletWebServiceSettings
from .service import router

logger.setLevel("DEBUG")

__version__ = version("edutap.wallet_apple_vas_web_service")

#: The name telemetry travels under: the distribution name, which is what `pip show`
#: prints and what the sibling services use. In Loki `service_name` is the only
#: indexed label, so one spelling across a span, a log line and the installed
#: distribution is what makes it selectable at all.
SERVICE_NAME = "edutap.wallet_apple_vas_web_service"

#: Error reporting, tracing and structured logging, resolved at import.
#:
#: BEFORE `AppleWalletWebServiceSettings()` below, deliberately. That call reads this
#: service's own configuration and can raise on a malformed value; installing
#: reporting first is what makes a process refusing to start visible rather than
#: silently absent. Reading this can never fail for want of a value -- no field of
#: `ObservabilitySettings` is required, which is precisely what makes the ordering
#: possible.
#:
#: The prefix is `EDUTAP_`, not this package's own: these fields are defined by an
#: eduTAP package and mean the same thing in every eduTAP service.
observability = ObservabilitySettings()
install_observability(
    observability,
    service_name=SERVICE_NAME,
    service_version=__version__,
)


def exports_to_a_collector() -> bool:
    """Whether an exporter will actually carry a span off this process.

    Both conditions are needed: `telemetry_enabled` is the deliberate off switch, and
    the endpoint decides whether anything is listening. The endpoint is read from the
    environment rather than from a field because `OTEL_EXPORTER_OTLP_ENDPOINT` is the
    variable every OpenTelemetry SDK reads by itself -- giving it a second name here
    would ask an operator to set the same address twice.
    """
    return observability.telemetry_enabled and bool(os.environ.get(OTLP_ENDPOINT_VARIABLE))


settings = AppleWalletWebServiceSettings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: mount the router before the first request."""
    # Initializing
    app.include_router(router)

    # THE HTTP SIDE OF OBSERVABILITY, and it has to happen HERE -- after the line
    # above. The call at import configures the *process* and knows nothing about this
    # application, so it produces no request spans by itself. Instrumenting needs the
    # finished route table, and this service registers its routes inside the lifespan
    # rather than at import.
    #
    # `instrument_fastapi_safely` rather than `logfire.instrument_fastapi`: the bare
    # instrumentation writes the raw request path into five span attributes. Apple's
    # web service paths carry the pass serial number, so the house helper substitutes
    # the route template and thereby honours `person_uid_mode`.
    #
    # ONLY WHEN SOMETHING EXPORTS. Instrumentation patches the application whether or
    # not a receiver exists, and a span nobody collects is work done on every request.
    if exports_to_a_collector():
        instrument_fastapi_safely(app, observability)
        logger.info("FastAPI instrumentation active")

    logger.info("creating stream processor for google wallet notifications")
    # asyncio.create_task(
    #     process_messages(
    #         settings.broker_url,
    #         settings.topic
    #     )
    # )
    yield
    # Shutdown


app = FastAPI(
    title="eduTAP Apple Wallet Web Service",
    description="A fastAPI based Web Service for Apple Wallet",
    # summary=""" """,
    version=__version__,
    lifespan=lifespan,
)


@app.get("/")
async def info():
    """Report package name and version."""
    return {
        "package": "edutap.wallet_apple_vas_web_service",
        "version": __version__,
        # "broker_url": settings.broker_url,
        # "topic": settings.notification_topic,
    }


@app.get("/openapi.json")
async def openapi():
    """Return the generated OpenAPI schema."""
    return app.openapi()


@app.post("/test/message")
async def test_message(request: Request, msg: str):
    """Accept a test message and do nothing with it; the producer is not wired up."""
    return
    # await kafka_producer.send_and_wait("test", msg.encode("utf-8"))


def main():
    """Run the service with uvicorn; the console script entry point."""
    uvicorn.run(
        "edutap.wallet_apple_vas_web_service.standalone:app",
        # Binding to every interface is what a container needs -- the process owns
        # its network namespace, and the published port is the deployment's decision.
        host="0.0.0.0",  # noqa: S104
        port=8084,
        log_level="debug",
        reload=True,
    )


if __name__ == "__main__":
    main()
