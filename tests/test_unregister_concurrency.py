"""The race between unregistering a device's last pass and registering a new one.

Uses two independent sessions on the module `engine` fixture, not `db_session`:
`db_session` shares one connection and one transaction across the whole test, so
it cannot express two overlapping, independently-committing transactions at all.
Both sessions here commit for real, so this test cleans up its own rows.

The routes are called directly from two threads, which is what FastAPI itself
does with them: they are `def`, not `async def`, so the framework hands each one
to its threadpool (see `service.py`'s module docstring). This test used to wrap
each call in `asyncio.run`, which was the right shape while they were
coroutines and is a `TypeError` now.
"""

import threading
import time

import pytest
from sqlmodel import Session, select

from edutap.wallet_apple_vas_web_service import service
from edutap.wallet_apple_vas_web_service.config import AppleWalletWebServiceSettings
from edutap.wallet_apple_vas_web_service.db_models import Device, PassRecord, Registration
from edutap.wallet_apple_vas_web_service.http_models import (
    AppleWalletWebServiceAuthorizationPayload,
)
from edutap.wallet_apple_vas_web_service.tokens import derive_token

pytestmark = pytest.mark.integration

SECRET = "an-issuer-secret"
DEVICE = "concurrency-device"
PTID = "pass.de.lmu.events"
UNREGISTERING = "serial-being-unregistered"
REGISTERING = "serial-being-registered"

SETTINGS = AppleWalletWebServiceSettings(authentication_secret=SECRET)


def _auth(serial: str) -> str:
    return f"ApplePass {derive_token(SECRET, PTID, serial)}"


def test_unregistering_the_last_pass_does_not_lose_a_concurrent_registration(engine):
    """The hazard: session A deletes the device's only registration, decides the
    device has nothing left, and is about to delete it -- while session B is, at
    that exact moment, registering a *different* pass on the same device. Without
    a lock, A's decision is based on data from before B's registration exists;
    when A commits, `ondelete=CASCADE` on the registration's foreign key silently
    carries B's brand-new registration away with the device row. B's caller
    already received a 201 and has no way to know its registration is gone.

    `session_a.commit` is wrapped, not the production code: it pauses `session_a`
    at the exact point `unregister_pass` reaches on its own -- immediately before
    `session.commit()`, which is after the real `with_for_update=True` lookup has
    already run and the device row is genuinely locked. The pause only controls
    *when* `session_a` finishes; every statement up to that point, including the
    lock, is the real production code path.
    """
    session_a = Session(engine)
    session_b = Session(engine)
    setup_session = Session(engine)

    try:
        # Seed the device with the one registration `session_a` is about to remove.
        service.register_pass(
            deviceLibraryIdentifier=DEVICE,
            passTypeIdentifier=PTID,
            serialNumber=UNREGISTERING,
            authorization=_auth(UNREGISTERING),
            data=AppleWalletWebServiceAuthorizationPayload(pushToken="a-push-token"),
            settings=SETTINGS,
            session=setup_session,
        )

        lock_acquired = threading.Event()
        let_a_continue = threading.Event()
        original_commit = session_a.commit

        def paused_commit():
            lock_acquired.set()
            assert let_a_continue.wait(timeout=5), "test stalled: session_a was never released"
            return original_commit()

        session_a.commit = paused_commit

        unregister_errors: list[BaseException] = []

        def run_unregister() -> None:
            try:
                service.unregister_pass(
                    deviceLibraryIdentifier=DEVICE,
                    passTypeIdentifier=PTID,
                    serialNumber=UNREGISTERING,
                    authorization=_auth(UNREGISTERING),
                    settings=SETTINGS,
                    session=session_a,
                )
            except BaseException as exc:  # noqa: BLE001 -- surfaced via unregister_errors
                unregister_errors.append(exc)

        thread_a = threading.Thread(target=run_unregister)
        thread_a.start()
        # However this function exits from here on -- assertion failure or not --
        # `session_a` must be released and `thread_a` joined, or a failing test
        # leaves a thread parked forever on `let_a_continue.wait()` while the
        # outer `finally` closes the session out from under it. That would make
        # the *next* failure depend on timing luck instead of being deterministic.
        try:
            assert lock_acquired.wait(timeout=5), "session_a never reached its commit"

            register_errors: list[BaseException] = []

            def run_register() -> None:
                try:
                    service.register_pass(
                        deviceLibraryIdentifier=DEVICE,
                        passTypeIdentifier=PTID,
                        serialNumber=REGISTERING,
                        authorization=_auth(REGISTERING),
                        data=AppleWalletWebServiceAuthorizationPayload(pushToken="b-push-token"),
                        settings=SETTINGS,
                        session=session_b,
                    )
                except BaseException as exc:  # noqa: BLE001 -- surfaced via register_errors
                    register_errors.append(exc)

            thread_b = threading.Thread(target=run_register)
            thread_b.start()
            try:
                # A settle delay, not a synchronization point: correctness does
                # not depend on its length, only on session_a having reached the
                # lock beforehand (already guaranteed by `lock_acquired.wait()`
                # above). Its purpose is to give session_b time to actually
                # reach and block on the device row -- the assertions right
                # after turn that expectation into checked facts.
                time.sleep(0.3)
                # Checked first: if `run_register` raised before it ever
                # reached the database, `thread_b` dies on its own and
                # `is_alive()` below would read as "the lock is blocking it" --
                # the wrong cause, with the real one sitting unexamined here.
                assert not register_errors, (
                    f"register_pass raised before contending for the device "
                    f"lock: {register_errors!r}"
                )
                assert thread_b.is_alive(), (
                    "session_b finished before session_a released the device "
                    "lock -- with_for_update is not blocking the concurrent "
                    "registration"
                )
            finally:
                let_a_continue.set()
                thread_b.join(timeout=5)
        finally:
            let_a_continue.set()
            thread_a.join(timeout=5)

        assert not unregister_errors, f"unregister_pass raised: {unregister_errors!r}"
        assert not register_errors, f"register_pass raised: {register_errors!r}"

        verify_session = Session(engine)
        try:
            surviving = verify_session.exec(
                select(Registration).where(
                    Registration.device_library_identifier == DEVICE,
                    Registration.serial_number == REGISTERING,
                )
            ).first()
            assert surviving is not None, (
                "the concurrent registration was lost -- the device was deleted "
                "out from under it and ondelete=CASCADE took the registration with it"
            )
        finally:
            verify_session.close()
    finally:
        session_a.close()
        session_b.close()
        setup_session.close()

        cleanup = Session(engine)
        for registration in cleanup.exec(
            select(Registration).where(Registration.device_library_identifier == DEVICE)
        ).all():
            cleanup.delete(registration)
        for serial in (UNREGISTERING, REGISTERING):
            pass_record = cleanup.get(PassRecord, (PTID, serial))
            if pass_record is not None:
                cleanup.delete(pass_record)
        device = cleanup.get(Device, DEVICE)
        if device is not None:
            cleanup.delete(device)
        cleanup.commit()
        cleanup.close()
