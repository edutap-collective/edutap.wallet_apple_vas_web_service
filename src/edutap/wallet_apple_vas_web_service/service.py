"""The Apple Wallet web service endpoints: register, list, deliver, unregister, log.

**None of the five routes is `async def`, and that is deliberate.** Every one of
them does blocking work -- psycopg2 through SQLModel in all five, and
`requests.get` to the producer in `send_updated_pass`. FastAPI runs a sync route
in a threadpool and an `async def` route body on the event loop itself, so an
`async def` here would let one unreachable producer stall *every* request that
worker is serving, for up to `producer_timeout_seconds`. A fleet of devices
fetching after a push is exactly this route's load, so that is the ordinary case
rather than an edge one.

The proper async rework -- an async driver, an async HTTP client -- is a
separate piece of work (see the design document's open points). Until it lands,
the word `async` is what would be wrong here, not what is missing. Anything
added to these bodies has to stay blocking-friendly; the moment one of them
needs to `await`, the whole route has to move to a genuinely async stack rather
than gain an `async` keyword.
"""

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import Response
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, select

from .config import AppleWalletWebServiceSettings, get_settings
from .db_models import UPDATE_TAG_SEQUENCE, Device, PassRecord, Registration
from .http_models import AppleWalletWebServiceAuthorizationPayload, LogEntries, SerialNumbers
from .producer import PassNotAvailable, ProducerError, fetch_pass
from .session import get_session
from .tokens import verify_authorization

LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(router: APIRouter):
    """Router lifespan; nothing to set up or tear down yet."""
    # setup phase
    yield
    # shutdown


router = APIRouter(
    prefix="/apple_update_service/v1",
    lifespan=lifespan,
)


"""
see: https://developer.apple.com/documentation/walletpasses/adding_a_web_service_to_update_passes
"""


def next_update_tag(session: Session) -> int:
    """Return the next value of the update-tag sequence.

    Through the `Sequence` construct rather than a hand-written `nextval(...)`:
    the sequence lives in this package's schema, and a bare name would resolve
    through `search_path` to whatever the connecting role happens to look at.
    """
    return session.execute(sa.select(UPDATE_TAG_SEQUENCE.next_value())).scalar_one()


def _authorized(
    authorization: str | None,
    settings: AppleWalletWebServiceSettings,
    pass_type_identifier: str,
    serial_number: str,
) -> bool:
    """Whether this request carries the authentication token of this pass.

    There is no way to switch this off; see `config.py` for the `auth_required`
    flag that used to sit here and what it did.
    """
    return verify_authorization(
        authorization, settings.accepted_secrets(), pass_type_identifier, serial_number
    )


def _upsert_device(session: Session, device_library_identifier: str, push_token: str) -> None:
    """Insert the device, or refresh its push token if it already exists.

    `ON CONFLICT DO UPDATE`, not a `SELECT` followed by an `INSERT`/`UPDATE`: a
    phone and its paired watch are two different devices that register the same
    pass within milliseconds of each other -- an ordinary case, not an edge
    one. A check-then-act would let the loser of that race hit an unhandled
    `IntegrityError` on `commit()` and turn into a bare 500 instead of taking
    the row it should have updated.

    `updated_at` is set explicitly, and it has to be. The column carries both a
    Python-side default and a server-side one, and neither of them fires here:
    SQLAlchemy applies a column's `onupdate` on an ORM flush and on a Core
    `update()`, never inside an `ON CONFLICT` `set_`, and a `server_default`
    answers for the INSERT alone. Measured before this line existed: after a
    push-token change, `updated_at` was byte-identical to `created_at` -- so
    the column said "never touched" about the one event it exists to record.
    See `db_models._timestamp`, which carries the same warning and was copied
    without it.
    """
    statement = (
        pg_insert(Device.__table__)
        .values(device_library_identifier=device_library_identifier, push_token=push_token)
        .on_conflict_do_update(
            index_elements=["device_library_identifier"],
            set_={"push_token": push_token, "updated_at": sa.func.now()},
        )
    )
    session.execute(statement)


def _upsert_pass_record(session: Session, pass_type_identifier: str, serial_number: str) -> None:
    """Insert the pass record if it does not exist yet; never touch an existing one.

    `DO NOTHING`, not `DO UPDATE`: `last_update_tag` is the announcement state,
    and a racing insert overwriting it would un-announce a change already
    delivered. The sequence value this call consumes is spent either way --
    tags only have to rise, not be dense, so one going unused because this
    insert lost the race is harmless.
    """
    statement = (
        pg_insert(PassRecord.__table__)
        .values(
            pass_type_identifier=pass_type_identifier,
            serial_number=serial_number,
            last_update_tag=next_update_tag(session),
        )
        .on_conflict_do_nothing(index_elements=["pass_type_identifier", "serial_number"])
    )
    session.execute(statement)


def _insert_registration_if_absent(
    session: Session,
    device_library_identifier: str,
    pass_type_identifier: str,
    serial_number: str,
) -> bool:
    """Insert the registration if absent; return whether this call created it.

    `RETURNING` on a `DO NOTHING` insert answers "did I just create this row?"
    in the same round trip that does the inserting -- no separate `SELECT`
    whose answer a concurrent insert could invalidate before this statement
    runs. That answer is exactly Apple's 201-vs-200 distinction.
    """
    statement = (
        pg_insert(Registration.__table__)
        .values(
            device_library_identifier=device_library_identifier,
            pass_type_identifier=pass_type_identifier,
            serial_number=serial_number,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "device_library_identifier",
                "pass_type_identifier",
                "serial_number",
            ]
        )
        .returning(Registration.__table__.c.device_library_identifier)
    )
    return session.execute(statement).first() is not None


@router.post("/devices/{deviceLibraryIdentifier}/registrations/{passTypeIdentifier}/{serialNumber}")
def register_pass(
    deviceLibraryIdentifier: str,
    passTypeIdentifier: str,
    serialNumber: str,
    authorization: Annotated[str | None, Header()] = None,
    data: AppleWalletWebServiceAuthorizationPayload | None = None,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> Response:
    """Register a device to receive update notifications for a pass.

    https://developer.apple.com/documentation/walletpasses/register-a-pass-for-update-notifications

    Apple's tasks for this call: create the pass entry if it does not exist,
    create the device entry if it does not exist, store the mapping between
    them. Answers 201 on a new registration, 200 when the serial number was
    already registered for the device, 401 when unauthorized.
    """
    if not _authorized(authorization, settings, passTypeIdentifier, serialNumber):
        return Response(status_code=401)

    # A missing body is a bad request, not an assertion: `assert` disappears
    # under `python -O`, and a None would reach `data.pushToken` below and turn
    # a malformed registration into a 500.
    if data is None:
        return Response(status_code=400)

    _upsert_device(session, deviceLibraryIdentifier, data.pushToken)
    _upsert_pass_record(session, passTypeIdentifier, serialNumber)
    created = _insert_registration_if_absent(
        session, deviceLibraryIdentifier, passTypeIdentifier, serialNumber
    )
    session.commit()
    return Response(status_code=201 if created else 200)


def _cursor(*candidates: str | None) -> int | None:
    """Return the first parsable cursor value, or None.

    Apple's current endpoint page names the parameter `previousLastUpdated`,
    while the wire has carried `passesUpdatedSince` since the protocol was
    published; see the design document. Both are accepted, and a value that does
    not parse is treated as absent -- an unreadable cursor must widen the answer,
    never narrow it.
    """
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            # `float(candidate)` also has to be guarded: "inf" and "1e400" both
            # parse to a float without raising, and `int()` of an infinite float
            # raises OverflowError rather than ValueError. This route has no
            # credential -- Apple authenticates it with the device library
            # identifier, not a token -- so any caller can send a one-word
            # payload here, and it must land in the same "treat as absent"
            # branch as any other unparsable value rather than surface as a
            # bare 500.
            return int(float(candidate))
        except (ValueError, OverflowError):
            continue
    return None


@router.get("/devices/{deviceLibraryIdentifier}/registrations/{passTypeIdentifier}")
def list_updatable_passes(
    deviceLibraryIdentifier: str,
    passTypeIdentifier: str,
    passesUpdatedSince: str | None = None,
    previousLastUpdated: str | None = None,
    *,
    session: Session = Depends(get_session),
) -> Response:
    """Send the serial numbers for updated passes to a device.

    https://developer.apple.com/documentation/walletpasses/get-the-list-of-updatable-passes

    No authorization: Apple authenticates this call with the device library
    identifier itself, which is the other of the two shared secrets. The URL
    carries no serial number, so a per-pass token could not be checked here even
    if one were sent.

    Answers 200 with a SerialNumbers body, or 204 when nothing matches.
    """
    cursor = _cursor(passesUpdatedSince, previousLastUpdated)

    rows = session.exec(
        select(Registration, PassRecord)
        .join(
            PassRecord,
            (Registration.pass_type_identifier == PassRecord.pass_type_identifier)
            & (Registration.serial_number == PassRecord.serial_number),
        )
        .where(
            Registration.device_library_identifier == deviceLibraryIdentifier,
            Registration.pass_type_identifier == passTypeIdentifier,
        )
    ).all()

    behind = [
        (registration, record)
        for registration, record in rows
        # Our own record is the precise filter, the device's cursor the safety
        # net. A missing cursor contributes no evidence of its own -- it must
        # not widen the list beyond what our own record already says, or a
        # device that is fully caught up but omits the cursor would be told
        # it is behind on every pass it holds.
        if registration.delivered_tag is None
        or record.last_update_tag > registration.delivered_tag
        or (cursor is not None and record.last_update_tag > cursor)
    ]
    if not behind:
        return Response(status_code=204)

    payload = SerialNumbers(
        serialNumbers=[record.serial_number for _, record in behind],
        lastUpdated=str(max(record.last_update_tag for _, record in behind)),
    )
    return Response(payload.model_dump_json(), status_code=200, media_type="application/json")


_SAFE_FILENAME_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _content_disposition(serial_number: str) -> str:
    """Return the `Content-Disposition` value for one delivered pass.

    The serial number used to be interpolated into `filename="..."` unescaped,
    and it is caller-controlled: it arrives in the URL, and this service accepts
    a delivery request for a pass it has never heard of. A serial containing a
    double quote closes the quoted string and everything after it is read as
    further header parameters; a serial containing CR or LF is a header
    injection attempt, which the ASGI server rejects -- with a 500 on an
    endpoint whose contract is 200 or 401.

    Two forms, per RFC 6266. `filename*` carries the real value, percent-encoded
    by RFC 5987, so nothing in it can be a delimiter; `filename` is the fallback
    for a client that does not read the extended form, reduced to characters
    that cannot escape a quoted string. A client that understands both must
    prefer `filename*`, so the reduction never changes what a modern client
    sees.

    Neither form matters to Wallet, which goes by the `Content-Type` -- this is
    for the human who fetches the URL with a browser or curl while debugging,
    and for not handing a header parser something shaped like an injection.
    """
    filename = f"{serial_number}.pkpass"
    reduced = "".join(
        character if character in _SAFE_FILENAME_CHARACTERS else "_" for character in filename
    )
    return f"attachment; filename=\"{reduced}\"; filename*=UTF-8''{quote(filename, safe='')}"


@router.get("/passes/{passTypeIdentifier}/{serialNumber}")
def send_updated_pass(
    passTypeIdentifier: str,
    serialNumber: str,
    authorization: Annotated[str | None, Header()] = None,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> Response:
    """Create and sign an updated pass, and send it to the device.

    https://developer.apple.com/documentation/walletpasses/send-an-updated-pass

    The pass is built by the configured producer, which resolves the person and
    reads current data. This service passes Apple's key through and records what
    the device now holds.

    Apple documents 200 and 401 for this endpoint and nothing else, so both
    answers below stay inside those two codes plus 503. A producer `410` --
    this pass is permanently gone -- becomes 401, the strongest statement the
    contract allows. A producer `404` becomes 503: it is the recoverable one,
    and telling a device its credential is dead because a producer lost a pass
    for thirty seconds is not something the device can recover from. See
    `producer.fetch_pass`.
    """
    if not _authorized(authorization, settings, passTypeIdentifier, serialNumber):
        return Response(status_code=401)

    try:
        content = fetch_pass(settings, passTypeIdentifier, serialNumber)
    except PassNotAvailable:
        return Response(status_code=401)
    except ProducerError:
        LOGGER.exception("The producer could not deliver %s/%s", passTypeIdentifier, serialNumber)
        return Response(status_code=503)

    record = session.get(PassRecord, (passTypeIdentifier, serialNumber))
    if record is not None:
        delivered_at = datetime.now(tz=UTC)
        # Apple's delivery URL carries no device identifier, so which device is
        # asking cannot be known here. Every registration of this pass that is
        # behind is recorded as current -- see "Known limitation" in the plan
        # this route comes from.
        for registration in session.exec(
            select(Registration)
            .where(
                Registration.pass_type_identifier == passTypeIdentifier,
                Registration.serial_number == serialNumber,
            )
            # Ordered so two concurrent deliveries of the same pass take their
            # row locks in the same sequence. Without it the two SELECTs may
            # return the same rows in different orders -- a plan change is
            # enough -- and each then updates them one by one, holding a row the
            # other is waiting for. PostgreSQL breaks that by aborting one side
            # with `DeadlockDetected`, which surfaces as a 500, outside the two
            # codes Apple documents for this endpoint. A phone and its paired
            # Watch both fetching after a push is the ordinary case, not an edge
            # one -- the same reasoning as the lock ordering in
            # `unregister_pass`.
            .order_by(Registration.device_library_identifier)
        ).all():
            # Written on *every* successful delivery, not only when the tag has
            # moved. Both of the things `last_delivered_at` is for are about
            # when the device last actually came: evidence that a voided pass
            # was collected, and "pushed but never fetched" in support. A
            # re-fetch of an unchanged pass is contact, and gating the write on
            # the tag would make a column called `last_delivered_at` mean
            # "first delivery of the current tag" instead.
            registration.delivered_tag = record.last_update_tag
            registration.last_delivered_at = delivered_at
            session.add(registration)
        session.commit()

    return Response(
        content,
        status_code=200,
        media_type="application/vnd.apple.pkpass",
        headers={"Content-Disposition": _content_disposition(serialNumber)},
    )


@router.delete(
    "/devices/{deviceLibraryIdentifier}/registrations/{passTypeIdentifier}/{serialNumber}"
)
def unregister_pass(
    deviceLibraryIdentifier: str,
    passTypeIdentifier: str,
    serialNumber: str,
    authorization: Annotated[str | None, Header()] = None,
    *,
    settings: AppleWalletWebServiceSettings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> Response:
    """Stop sending update notifications for a pass on a device.

    https://developer.apple.com/documentation/walletpasses/unregister-a-pass-for-update-notifications

    Apple's tasks: delete the mapping, then delete the device if no registration
    is left for it. Answers 200 or 401 — the two codes Apple documents.
    """
    if not _authorized(authorization, settings, passTypeIdentifier, serialNumber):
        return Response(status_code=401)

    # Lock the device row first, before touching the registration -- and
    # before deciding whether the device still holds any registrations at
    # all. Two reasons, not one:
    #
    # 1. That decision is a negative existence check ("nothing references
    #    this device"), and Postgres has no lock that protects the *absence*
    #    of a row -- only rows that exist can be locked. Without locking the
    #    device row, this transaction could see "no registrations left"
    #    while a registration for this device is being created elsewhere,
    #    and then delete the device out from under it.
    # 2. `register_pass` takes its own locks device-first: `_upsert_device`
    #    locks the device row via `ON CONFLICT DO UPDATE`, then
    #    `_insert_registration_if_absent`'s FK-child insert takes a
    #    `FOR KEY SHARE` lock on that same device row. Locking the
    #    registration row here before the device row would be the reverse
    #    order -- an ABBA cycle with `register_pass`, which Postgres's
    #    deadlock detector would eventually break by aborting one side with
    #    an unhandled `DeadlockDetected`, a `500` outside the two codes
    #    Apple documents for this endpoint. Device-first here matches
    #    device-first there, so the two routes can never deadlock against
    #    each other.
    #
    # `with_for_update=True` takes the lock `_upsert_device`'s own
    # `ON CONFLICT DO UPDATE` needs, so a concurrent registration for this
    # device blocks here until this transaction commits or rolls back:
    # whichever side finishes first is the one the other one sees. Do not
    # remove this thinking it is redundant with the foreign key -- it is
    # not: a concurrent *insert* of a registration only needs the FK-child's
    # `FOR KEY SHARE` lock, which does not conflict with a plain read.
    device = session.get(Device, deviceLibraryIdentifier, with_for_update=True)

    registration = session.get(
        Registration, (deviceLibraryIdentifier, passTypeIdentifier, serialNumber)
    )
    if registration is not None:
        session.delete(registration)
        session.flush()

    remaining = session.exec(
        select(Registration).where(
            Registration.device_library_identifier == deviceLibraryIdentifier
        )
    ).first()
    if remaining is None and device is not None:
        session.delete(device)

    session.commit()
    return Response(status_code=200)


@router.post("/log")
def device_log(
    request: Request,
    data: LogEntries,
):
    """Logging/Debugging from the device

    log an error or unexpected server behavior, to help with server debugging
    POST /v1/log
    JSON payload: { "description" : <human-readable description of error> }

    server response: 200
    """
    # To the process log, not to a file: three replicas write to three container
    # filesystems that nothing collects and the next deploy discards. The path this
    # wrote to does not exist in the image either. Structured logging with the rest
    # of the estate's fields comes with edutap.observability_settings.
    for line in data.logs:
        LOGGER.info("apple device log: %s", line)

    return Response(status_code=200)
