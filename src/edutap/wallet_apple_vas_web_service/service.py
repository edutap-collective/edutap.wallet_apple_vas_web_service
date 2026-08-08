from .config import AppleWalletWebServiceSettings, get_settings
from .db_models import AppleDeviceRegistry
from .db_models import ApplePassData
from .db_models import ApplePassRegistry
from .db_models import get_session
from .http_models import AppleWalletWebServiceAuthorizationPayload
from .http_models import LogEntries
from .http_models import SerialNumbers
from contextlib import asynccontextmanager
from datetime import datetime
from edutap.wallet_apple.models import Pass
from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import Request
from fastapi.responses import Response
from pathlib import Path
from sqlmodel import select
from sqlmodel import Session
from typing import Annotated


logfile = Path
registeredAuthTokens = [
    "b5b2597a1c5aa2c6019aa065922785d54760a784b98731c344b4fd1eb6dd8eed",
]


@asynccontextmanager
async def lifespan(router: APIRouter):
    # setup phase
    yield
    # shutdown


router = APIRouter(
    prefix="/apple_update_service/v1",
    lifespan=lifespan,
)


def check_authentification_token(
    authorization_header_string: str | None, auth_required: bool = True
) -> bool:
    if auth_required:
        if authorization_header_string is not None:
            authType, authToken = authorization_header_string.split()
            if authType != "ApplePass":
                return False
            if authToken not in registeredAuthTokens:
                return False
        else:
            return False
    return True


"""
see: https://developer.apple.com/documentation/walletpasses/adding_a_web_service_to_update_passes
"""


@router.post(
    "/devices/{deviceLibraryIdentitfier}/registrations/{passTypeIdentifier}/{serialNumber}"
)
async def register_pass(
    request: Request,
    deviceLibraryIdentitfier: str,
    passTypeIdentifier: str,
    serialNumber: str,
    authorization: Annotated[str | None, Header()] = None,
    data: AppleWalletWebServiceAuthorizationPayload | None = None,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
    session: Session = Depends(get_session),
):
    """
    Registration: register a device to receive push notifications for a pass.

    see: https://developer.apple.com/documentation/walletpasses/register_a_pass_for_update_notifications

    URL: POST https://yourpasshost.example.com/v1/devices/{deviceLibraryIdentifier}/registrations/{passTypeIdentifier}/{serialNumber}
    HTTP-Methode: POST
    HTTP-PATH: /v1/devices/{deviceLibraryIdentifier}/registrations/{passTypeIdentifier}/{serialNumber}
    HTTP-Path-Parameters:
        * deviceLibraryIdentifier: str (required) A unique identifier you use to identify and authenticate the device.
        * passTypeIdentifier: str (required) The pass type identifier of the pass to register for update notifications. This value corresponds to the value of the passTypeIdentifier key of the pass.
        * serialNumber: str (required)
    HTTP-Headers:
        * Authorization: ApplePass <authenticationToken>
    HTTP-Body: JSON payload:
        * pushToken: <push token, which the server needs to send push notifications to this device> }

    Params definition
    :deviceLibraryIdentitfier      - the device's identifier
    :passTypeIdentifier   - the bundle identifier for a class of passes, sometimes referred to as the pass topic, e.g. pass.com.apple.backtoschoolgift, registered with WWDR
    :serialNumber  - the pass' serial number
    :pushToken      - the value needed for Apple Push Notification service

    server action: if the authentication token is correct, associate the given push token and device identifier with this pass
    server response:
    --> if registration succeeded: 201
    --> if this serial number was already registered for this device: 304
    --> if not authorized: 401

    :async:
    :param str deviceLibraryIdentifier: A unique identifier you use to identify and authenticate the device.
    :param str passTypeIdentifier:      The pass type identifier of the pass to register for update notifications. This value corresponds to the value of the passTypeIdentifier key of the pass.
    :param str serialNumber:            The serial number of the pass to register. This value corresponds to the serialNumber key of the pass.

    :return:
    """

    print("register pass:")
    print(f"{deviceLibraryIdentitfier=}")
    print(f"{passTypeIdentifier=}")
    print(f"{serialNumber=}")
    print(f"{authorization=}")
    print(f"{data=}")

    if not check_authentification_token(authorization, settings.auth_required):
        return Response(status_code=401)

    assert data is not None

    with (settings.log_file_path / "wallet_apple_vas_web_service_register_device.log").open(mode="a") as output:
        output.write(f"Register Event for Device: {deviceLibraryIdentitfier=}, {data.pushToken=}\n")
    with (settings.log_file_path / "wallet_apple_vas_web_service_register_pass.log").open(mode="a") as output:
        output.write(f"Register Event for Pass: {deviceLibraryIdentitfier=}, {passTypeIdentifier=}, {serialNumber=}\n")

    # Register Device
    statement = select(AppleDeviceRegistry).where(
        AppleDeviceRegistry.deviceLibraryIdentitfier == deviceLibraryIdentitfier
    )
    db_device_entry = session.exec(statement)
    print(db_device_entry)
    db_device_entry = db_device_entry.first()
    print(db_device_entry)
    if db_device_entry is None:
        new_device_entry = AppleDeviceRegistry(
            deviceLibraryIdentitfier=deviceLibraryIdentitfier, pushToken=data.pushToken
        )
        session.add(new_device_entry)
        session.commit()

    # Register Pass
    statement = select(ApplePassRegistry).where(
        ApplePassRegistry.deviceLibraryIdentitfier == deviceLibraryIdentitfier
        and ApplePassRegistry.passTypeIdentifier == passTypeIdentifier
        and ApplePassRegistry.serialNumber == serialNumber
    )
    db_entry = session.exec(statement).first()

    if db_entry is None:
        new_entry = ApplePassRegistry(
            deviceLibraryIdentitfier=deviceLibraryIdentitfier,
            passTypeIdentifier=passTypeIdentifier,
            serialNumber=serialNumber,
        )
        session.add(new_entry)
        session.commit()

        print(f"write pass to registry: {new_entry}")
        return Response(status_code=201)

    print(f"pass {db_entry} already exists.")
    return Response(status_code=200)


@router.get("/devices/{deviceLibraryIdentitfier}/registrations/{passTypeIdentifier}")
async def update_pass(
    request: Request,
    deviceLibraryIdentitfier: str,
    passTypeIdentifier: str,
    passesUpdatedSince: str | None = None,
    authorization: Annotated[str | None, Header()] = None,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
    session: Session = Depends(get_session),
):
    """
    Get List of Updatable Passes

    see: https://developer.apple.com/documentation/walletpasses/get_the_list_of_updatable_passes

    Send the serial numbers for updated passes to a device.

    get all serial #s associated with a device for passes that need an update
    Optionally with a query limiter to scope the last update since

    GET /v1/devices/<deviceID>/registrations/<typeID>
    GET /v1/devices/<deviceID>/registrations/<typeID>?passesUpdatedSince=<tag>

    server action: figure out which passes associated with this device have been modified since the supplied tag (if no tag provided, all associated serial #s)
    server response:
    --> if there are matching passes: 200, with JSON payload: { "lastUpdated" : <new tag>, "serialNumbers" : [ <array of serial #s> ] }
    --> if there are no matching passes: 204
    --> if unknown device identifier: 404

    :async:
    :param str deviceLibraryIdentitfier: The unique identifier for the device.
    :param str

    """

    print("update pass:")
    print(f"{deviceLibraryIdentitfier=}")
    print(f"{passTypeIdentifier=}")
    print(f"{passesUpdatedSince=}")
    print(f"{authorization=}")

    with (settings.log_file_path / "wallet_apple_vas_web_service_update_check.log").open(mode="a") as output:
        output.write(f"Update Check: {deviceLibraryIdentitfier=}, {passTypeIdentifier=}, {passesUpdatedSince=}\n")

    if not check_authentification_token(authorization):
        return Response(status_code=401)

    updatedSince = datetime(1970, 1, 1)
    if passesUpdatedSince:
        updatedSince = datetime.fromtimestamp(float(passesUpdatedSince))

    statement = select(ApplePassData).where(
        ApplePassData.passTypeIdentifier == passTypeIdentifier
    )
    db_entries = session.exec(statement).all()

    if db_entries:
        passes_to_update = []
        last_update = datetime(1970, 1, 1)
        for entry in db_entries:
            if entry.lastUpdateTag > updatedSince:
                passes_to_update.append(entry.serialNumber)
                last_update = max(entry.lastUpdateTag, last_update)

        if passes_to_update:
            payload = SerialNumbers(
                serialNumers=passes_to_update, lastUpdated=last_update.timestamp()
            )
            return Response(payload, status_code=200, media_type="application/json")

    # No matching passes:
    return Response(status_code=204)


@router.delete(
    "/devices/{deviceLibraryIdentitfier}/registrations/{passTypeIdentifier}/{serialNumber}"
)
async def unregister_pass(
    request: Request,
    deviceLibraryIdentitfier: str,
    passTypeIdentifier: str,
    serialNumber: str,
    authorization: Annotated[str | None, Header()] = None,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
    session: Session = Depends(get_session),
):
    """
    Unregister

    unregister a device to receive push notifications for a pass

    DELETE /v1/devices/<deviceID>/registrations/<passTypeID>/<serial#>
    Header: Authorization: ApplePass <authenticationToken>

    server action: if the authentication token is correct, disassociate the device from this pass
    server response:
    --> if disassociation succeeded: 200
    --> if not authorized: 401

    """
    print("unregister pass:")

    print(f"{deviceLibraryIdentitfier=}")
    print(f"{passTypeIdentifier=}")
    print(f"{serialNumber=}")
    print(f"{authorization=}")

    if not check_authentification_token(authorization):
        return Response(status_code=401)

    with (settings.log_file_path / "wallet_apple_vas_web_service_unregister.log").open(mode="a") as output:
        output.write(f"Unregister Event for: {deviceLibraryIdentitfier=}, {passTypeIdentifier=}, {serialNumber=}\n")

    statement = select(ApplePassRegistry).where(
        ApplePassRegistry.deviceLibraryIdentitfier == deviceLibraryIdentitfier
        and ApplePassRegistry.passTypeIdentifier == passTypeIdentifier
        and ApplePassRegistry.serialNumber == serialNumber
    )

    results = session.exec(statement)
    db_entries = results.all()

    if db_entries:
        for entry in db_entries:
            session.delete(entry)
        session.commit()
        return Response(status_code=200)

    return Response(status_code=404)


@router.get("/passes/{passTypeIdentifier}/{serialNumber}")
async def send_updated_pass(
    request: Request,
    passTypeIdentifier: str,
    serialNumber: str,
    authorization: Annotated[str | None, Header()] = None,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
    session: Session = Depends(get_session),
):
    """
    Pass delivery

    GET /v1/passes/<typeID>/<serial#>
    Header: Authorization: ApplePass <authenticationToken>

    server response:
    --> if auth token is correct: 200, with pass data payload as pkpass-file
    --> if auth token is incorrect: 401
    """
    print("send updated pass:")
    print(f"{passTypeIdentifier=}")
    print(f"{serialNumber=}")
    print(f"{authorization=}")
    print(f"{request.__dict__=}")

    if not check_authentification_token(authorization):
        return Response(status_code=401)

    with (settings.log_file_path / "wallet_apple_vas_web_service_unregister.log").open(mode="a") as output:
        output.write(f"Pass Update Request Event for: {passTypeIdentifier=}, {serialNumber=}\n")

    statement = select(ApplePassData).where(
        ApplePassData.passTypeIdentifier == passTypeIdentifier
        and ApplePassData.serialNumber == serialNumber
    )
    results = session.exec(statement)
    db_entries = results.all()

    print(db_entries)

    passfile = Pass(db_entries.first().passData)

    zip = passfile.create(
        settings.apple.certificate,
        settings.apple.key,
        settings.apple.wwdr_certificate,
        settings.apple.password,
    )

    return Response(
        zip.getvalue(),
        status_code=200,
        media_type="application/vnd.apple.pkpass",
        headers={
            "Content-Disposition": f'attachment; filename="{serialNumber}.pkpass"'
        },
    )


@router.post("/log")
async def device_log(
    request: Request,
    data: LogEntries,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
):
    """
    Logging/Debugging from the device

    log an error or unexpected server behavior, to help with server debugging
    POST /v1/log
    JSON payload: { "description" : <human-readable description of error> }

    server response: 200
    """

    print(f"logs: {data.logs=}")
    logfile = settings.log_file_path / "wallet_apple_vas_web_service_report.log"

    with logfile.open(mode="a") as output:
        for line in data.logs:
            output.write(line + "\n")

    return Response(status_code=200)
