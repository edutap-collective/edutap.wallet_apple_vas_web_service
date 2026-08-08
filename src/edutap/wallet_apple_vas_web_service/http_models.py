from pydantic import BaseModel
from pydantic import ConfigDict


class AppleWalletWebServiceAuthorizationPayload(BaseModel):
    """
    An object that contains the push notification token for a registered pass on a device.

    see: https://developer.apple.com/documentation/walletpasses/pushtoken
    """

    model_config = ConfigDict(
        # extra="forbid",
        # extra="ignore",
        extra="allow",
    )
    pushToken: str


class SerialNumbers(BaseModel):
    """
    An object that contains serial numbers for the updatable passes on a device.

    see: https://developer.apple.com/documentation/walletpasses/serialnumbers
    """

    serialNumers: list[str]
    lastUpdated: str


class LogEntries(BaseModel):
    """
    An object that contains a list of messages.

    see: https://developer.apple.com/documentation/walletpasses/logentries
    """

    model_config = ConfigDict(
        # extra="forbid",
        # extra="ignore",
        extra="allow",
    )
    logs: list[str] = []
