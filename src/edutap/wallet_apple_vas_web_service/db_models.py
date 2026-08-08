import json
from sqlalchemy import ARRAY, Column, LargeBinary
from .config import AppleWalletWebServiceSettings
from datetime import datetime
from datetime import timezone
from sqlalchemy.types import JSON
from sqlmodel import create_engine
from sqlmodel import Field
from sqlmodel import Session
from sqlmodel import SQLModel
from sqlmodel.main import SQLModelConfig
from typing import Any
from typing import Generator

from edutap.wallet_apple.models import Pass

# from typing import Literal
# from edutap.wallet_apple.models import Pass


# Based on: https://developer.apple.com/documentation/walletpasses/adding_a_web_service_to_update_passes#3733252


class AppleDeviceRegistry(SQLModel, table=True):  # type: ignore[call-arg]
    """ 
    represents a registered device (Cellphone,tablet, watch, etc.)
    onto which a pass can be registered
    """

    id: int | None = Field(default=None, primary_key=True)
    deviceLibraryIdentitfier: str
    pushToken: str
    registrationTime: datetime = Field(default=datetime.now(tz=timezone.utc))


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
    lastUpdateTag: datetime = Field(default=datetime.now(tz=timezone.utc))
    # passStatus: Literal["downloaded", "registered", "unregistered"]
    passfile: dict = Field(sa_column=Column(JSON), default={})
    # passFiles: list[LargeBinary]=Field(default=None, sa_column=Column(ARRAY(LargeBinary())))
    pass_files: dict[str, LargeBinary] = Field(default_factory=dict, sa_column=Column(JSON))

    @classmethod
    def from_pass(cls, pass_: Pass) -> "ApplePassData":
        """
        creates a ApplePassData record from a Pass object
        """
        filedata = pass_.files_uuencoded
        passdata = cls(
            passTypeIdentifier=pass_.passTypeIdentifier,
            serialNumber=pass_.serialNumber,
            lastUpdateTag=datetime.now(tz=timezone.utc),
            passfile=json.loads(pass_.pass_json),
            pass_files=filedata
        )
        return passdata

    def to_pass(self) -> Pass:
        """
        creates a Pass object from a ApplePassData record
        """
        pass_ = Pass.model_validate(self.passfile)
        pass_.files_uuencoded = self.pass_files
        return pass_

      
class ApplePassRegistry(SQLModel, table=True):  # type: ignore[call-arg]
    """
    Represents the registration of a pass on a device
     TODO: add state machine (downloaded, registered, unregistered)
     
     It can happen thata pass gets registered,but the passdata is not (yet) available.
     In this case the passdata will be created epty and filled later.
     
    """

    id: int | None = Field(default=None, primary_key=True)
    deviceLibraryIdentitfier: str # Foreign key to AppleDeviceRegistry
    passTypeIdentifier: str # Forein key to ApplePassData
    serialNumber: str
    registrationTime: datetime = Field(default=datetime.now(tz=timezone.utc))


def init_model(engine):
    SQLModel.metadata.create_all(engine)
    
    
def get_session() -> Generator[Session, Any, Any]:
    print("Read Settings for Create Session")
    settings: AppleWalletWebServiceSettings = AppleWalletWebServiceSettings()
    print(settings)

    print("Create Engine")
    engine = create_engine(
        f"{settings.db.type}+{settings.db.driver}://{settings.db.username}:{settings.db.password}@{settings.db.host}{':' + str(settings.db.port) if settings.db.port != 5432 else ''}/{settings.db.name}",
        echo=True,
    )
    print(engine.url)

    # Generate Tables
    init_model(engine)
    print("Create Session")
    with Session(engine) as session:
        yield session
