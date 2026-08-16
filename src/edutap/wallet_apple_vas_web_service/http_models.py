"""Request and response bodies of the Apple Wallet web service protocol."""

from pydantic import BaseModel, ConfigDict


class AppleWalletWebServiceAuthorizationPayload(BaseModel):
    """An object that contains the push notification token for a registered pass on a device.

    see: https://developer.apple.com/documentation/walletpasses/pushtoken
    """

    model_config = ConfigDict(
        # extra="forbid",
        # extra="ignore",
        extra="allow",
    )
    pushToken: str


class SerialNumbers(BaseModel):
    """The body of a successful list response.

    https://developer.apple.com/documentation/walletpasses/serialnumbers

    `lastUpdated` is a string. Apple's example shows `"1351901923"`, and the
    device stores the value opaquely and returns it unchanged.
    """

    serialNumbers: list[str]
    lastUpdated: str


class LogEntries(BaseModel):
    """An object that contains a list of messages.

    see: https://developer.apple.com/documentation/walletpasses/logentries
    """

    model_config = ConfigDict(
        # extra="forbid",
        # extra="ignore",
        extra="allow",
    )
    logs: list[str] = []
