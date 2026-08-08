from .config import AppleWalletWebServiceSettings
from datetime import datetime
from datetime import timezone
from fastapi import logger
from kafka import KafkaProducer
from pydantic import BaseModel
from typing import Literal


producer: KafkaProducer = None
settings: AppleWalletWebServiceSettings = AppleWalletWebServiceSettings()


def producer_init():
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


def send_to_wallet_apple_vas_web_service(
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
            "createTime": createTime if createTime else datetime.now(tz=timezone.utc),
        },
        value=payload.model_dump_json(),
    )

    return True
