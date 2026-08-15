"""Database models for registered devices, passes and their registrations."""

import json
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

# See the note in service.py: `models` carries no __init__.py upstream, so the name
# has to come from the module that defines it.
from edutap.wallet_apple.models.passes import Pass
from sqlalchemy import Column, LargeBinary
from sqlalchemy.types import JSON
from sqlmodel import Field, Session, SQLModel, create_engine
from sqlmodel.main import SQLModelConfig

from .config import AppleWalletWebServiceSettings

# from typing import Literal


# Based on: https://developer.apple.com/documentation/walletpasses/adding_a_web_service_to_update_passes#3733252


class AppleDeviceRegistry(SQLModel, table=True):  # type: ignore[call-arg]
    """
    represents a registered device (Cellphone,tablet, watch, etc.)
    onto which a pass can be registered
    """

    id: int | None = Field(default=None, primary_key=True)
    deviceLibraryIdentitfier: str
    pushToken: str
    registrationTime: datetime = Field(default=datetime.now(tz=UTC))


class ApplePassData(SQLModel, table=True):  # type: ignore[call-arg]
    """
    the full representation of an apple pass
    including the pass json data and all binary data (images, logos, etc.)

    TODO: state machine, which states a pass can have
    """

    model_config = SQLModelConfig(
        arbitrary_types_allowed=True,
    )

    passTypeIdentifier: str = Field(primary_key=True)
    serialNumber: str = Field(primary_key=True)
    lastUpdateTag: datetime = Field(default=datetime.now(tz=UTC))
    # passStatus: Literal["downloaded", "registered", "unregistered"]
    passfile: dict = Field(sa_column=Column(JSON), default={})
    # passFiles: list[LargeBinary]=Field(default=None, sa_column=Column(ARRAY(LargeBinary())))
    pass_files: dict[str, LargeBinary] = Field(default_factory=dict, sa_column=Column(JSON))

    @classmethod
    def from_pass(cls, pass_: Pass) -> "ApplePassData":
        """Creates a ApplePassData record from a Pass object"""
        filedata = pass_.files_uuencoded
        passdata = cls(
            passTypeIdentifier=pass_.passTypeIdentifier,
            serialNumber=pass_.serialNumber,
            lastUpdateTag=datetime.now(tz=UTC),
            passfile=json.loads(pass_.pass_json),
            pass_files=filedata,
        )
        return passdata

    def to_pass(self) -> Pass:
        """Creates a Pass object from a ApplePassData record"""
        pass_ = Pass.model_validate(self.passfile)
        pass_.files_uuencoded = self.pass_files
        return pass_


class ApplePassRegistry(SQLModel, table=True):  # type: ignore[call-arg]
    """Represents the registration of a pass on a device
    TODO: add state machine (downloaded, registered, unregistered)

    It can happen thata pass gets registered,but the passdata is not (yet) available.
    In this case the passdata will be created epty and filled later.

    """

    id: int | None = Field(default=None, primary_key=True)
    deviceLibraryIdentitfier: str  # Foreign key to AppleDeviceRegistry
    passTypeIdentifier: str  # Forein key to ApplePassData
    serialNumber: str
    registrationTime: datetime = Field(default=datetime.now(tz=UTC))


def init_model(engine):
    """Create the tables this service owns if they are absent."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, Any, Any]:
    """Yield a database session; used as a FastAPI dependency."""
    settings: AppleWalletWebServiceSettings = AppleWalletWebServiceSettings()

    # No ``print`` and no ``echo``: the settings instance carries the database
    # password, and echo writes every statement including its bound parameters --
    # which for this service is registered device tokens and pass serial numbers.
    port = f":{settings.db.port}" if settings.db.port != 5432 else ""
    engine = create_engine(
        f"{settings.db.type}+{settings.db.driver}://"
        f"{settings.db.username}:{settings.db.password}"
        f"@{settings.db.host}{port}/{settings.db.name}",
    )

    # Generate Tables
    init_model(engine)
    with Session(engine) as session:
        yield session
