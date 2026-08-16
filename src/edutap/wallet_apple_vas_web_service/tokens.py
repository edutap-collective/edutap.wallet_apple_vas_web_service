"""The per-pass authentication token: how it is derived and how it is checked.

Apple states the purpose: the token "shows that the request for an update to a
pass is coming from the user who has the pass and not from a third party". An
issuer-wide token cannot make that statement — a `.pkpass` is a ZIP, every
holder can read the token out of `pass.json`, and the delivery endpoint returns
the full pass to whoever presents it with a serial number.

The value is derived rather than stored. Both sides hold the same issuer secret:
the producer computes the token when it builds the pass, this service computes
it when it verifies. That needs no write path from the producer into this
service, and it authenticates a registration for a pass this service has never
heard of — which is the ordinary case when a freshly issued pass is installed.
"""

import hmac
from collections.abc import Sequence
from hashlib import sha256

SCHEME = "ApplePass"

_SEPARATOR = b"\x00"
"""Separates the two identifiers in the derived message.

A byte that occurs in neither a pass type identifier nor a serial number, so no
two different pairs can produce the same message by concatenation.
"""


def derive_token(secret: str, pass_type_identifier: str, serial_number: str) -> str:
    """Return the authentication token of one pass, as lowercase hex."""
    message = pass_type_identifier.encode("utf-8") + _SEPARATOR + serial_number.encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, sha256).hexdigest()


def verify_authorization(
    header: str | None,
    secrets: Sequence[str],
    pass_type_identifier: str,
    serial_number: str,
) -> bool:
    """Check an `Authorization: ApplePass <token>` header against the secrets.

    With no secret configured every request is rejected: a deployment that
    forgot the value must fail closed rather than accept anything.
    """
    if not header or not secrets:
        return False
    scheme, _, presented = header.partition(" ")
    if scheme != SCHEME or not presented or " " in presented:
        return False

    # Every secret is tried and none of them exits early. Returning on the first
    # match would make the response time say which secret matched, and therefore
    # how long ago the pass was built.
    accepted = False
    for secret in secrets:
        expected = derive_token(secret, pass_type_identifier, serial_number)
        accepted |= hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
    return accepted
