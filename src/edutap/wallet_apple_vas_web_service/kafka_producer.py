"""Kafka producer for pass lifecycle events.

NOT REACHED BY THE APPLICATION. This module imports ``kafka`` while the package
declares ``aiokafka``, and it reads ``settings.wallet_apple_vas_web_service.*``,
which does not exist. Nothing imports it. It is kept because the events it
describes are still wanted; it needs rewriting against ``edutap.data_models``
before it can run.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import logger
from kafka import KafkaProducer
from pydantic import BaseModel

from .config import AppleWalletWebServiceSettings

producer: KafkaProducer = None
settings: AppleWalletWebServiceSettings = AppleWalletWebServiceSettings()


def producer_init():
    """Create the module-level producer once, tolerating an absent broker."""
    global producer
    if producer is not None:
        return
    logger.info("Initializing producer")
    try:
        producer = KafkaProducer(
            bootstrap_servers=settings.wallet_apple_vas_web_service.bootstrap_servers,
            client_id="edutap-demo-service-producer",
            acks="all",
            retries=3,
        )
    except Exception:
        logger.exception("No brokers available")


producer_init()


def send_to_wallet_apple_vas_web_service(  # noqa: D103 -- see the module docstring
    passTypeIdentifier: str,
    internalPassTypeIdentifier: str,
    serialNumber: str,
    status: Literal["created", "updated"],
    payload: BaseModel,
    createTime: datetime | None = None,
) -> bool:
    global producer
    if producer is None:
        producer_init()
        if producer is None:
            raise ValueError(
                f"Producer can not be not initialized with "
                f"bootstrap_servers={settings.wallet_apple_vas_web_service.bootstrap_servers}"
            )
    producer.send(
        topic=settings.wallet_apple_vas_web_service.topic,
        key={
            "passTypeIdenitfier": passTypeIdentifier,
            "internalPassTypeIdentifier": internalPassTypeIdentifier,
            "serialNumber": serialNumber,
            "createTime": createTime if createTime else datetime.now(tz=UTC),
        },
        value=payload.model_dump_json(),
    )

    return True
