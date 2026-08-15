"""ASGI application and console entry point of the Apple Wallet web service."""

from contextlib import asynccontextmanager
from importlib.metadata import version

import uvicorn
from fastapi import FastAPI, Request
from fastapi.logger import logger

from .config import AppleWalletWebServiceSettings
from .service import router

logger.setLevel("DEBUG")

__version__ = version("edutap.wallet_apple_vas_web_service")

settings = AppleWalletWebServiceSettings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: mount the router before the first request."""
    # Initializing
    app.include_router(router)

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
