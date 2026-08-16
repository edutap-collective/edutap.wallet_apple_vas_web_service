"""The three tables this service owns.

The shape follows Apple's own storage model in "Adding a Web Service to Update
Passes": two entities, devices and passes, and one many-to-many relationship,
registrations.

The `pass` table holds bookkeeping and no pass content. The content belongs to
the producer that built the pass; this service fetches it at delivery time.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlmodel import Field

from .base import SCHEMA, Base, metadata


def _utcnow() -> datetime:
    """Timezone-aware now, for the Python-side default."""
    return datetime.now(tz=UTC)


def _timestamp(on_update: bool = False) -> sa.Column:
    """A timestamptz column whose value the database computes."""
    kwargs: dict[str, object] = {"server_default": sa.func.now()}
    if on_update:
        kwargs["onupdate"] = sa.func.now()
    return sa.Column(sa.DateTime(timezone=True), nullable=False, **kwargs)


def _identifier(*args: object, **kwargs: object) -> sa.Column:
    """A column holding an opaque external identifier.

    Byte collation, so comparison and index order do not depend on the
    database's locale — these values are compared for equality by machines,
    never sorted for humans.

    `*args` carries schema constructs that are positional to `Column`, such as
    a `ForeignKey`.
    """
    return sa.Column(sa.String(255, collation="C"), *args, **kwargs)


UPDATE_TAG_SEQUENCE = sa.Sequence("update_tag_seq", schema=SCHEMA, metadata=metadata)
"""Source of `PassRecord.last_update_tag`.

A sequence rather than a clock. The notifier runs with several replicas, and
wall clocks on different hosts are not comparable — which is exactly when the
tag has to be.
"""


class Device(Base, table=True):
    """A device that holds at least one updatable pass."""

    __tablename__ = "device"

    device_library_identifier: str = Field(sa_column=_identifier(primary_key=True))
    push_token: str = Field(
        sa_column=sa.Column(sa.String(255), nullable=False),
        description="APNs token. A credential: never logged, never returned by an endpoint.",
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp())
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp(on_update=True))


class PassRecord(Base, table=True):
    """One updatable pass — bookkeeping only, no content.

    Named `PassRecord` rather than `Pass`: `pass` is a Python keyword, and
    `Pass` is already the pass model of `edutap.wallet_apple`.
    """

    __tablename__ = "pass"

    pass_type_identifier: str = Field(sa_column=_identifier(primary_key=True))
    serial_number: str = Field(sa_column=_identifier(primary_key=True))
    last_update_tag: int = Field(
        sa_column=sa.Column(sa.BigInteger, nullable=False),
        description="Rises on every change of content. Apple leaves its contents to us.",
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp())
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp(on_update=True))


class Registration(Base, table=True):
    """One device holding one pass, and how far behind that device is."""

    __tablename__ = "registration"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["pass_type_identifier", "serial_number"],
            [f"{SCHEMA}.pass.pass_type_identifier", f"{SCHEMA}.pass.serial_number"],
            ondelete="CASCADE",
        ),
        # The hot path: every list request filters on exactly these two, and the
        # result is not small — one pass type identifier may cover many pass
        # kinds, so a device can hold a two-digit number of registrations under
        # one of them.
        sa.Index(
            "ix_registration_device_library_identifier",
            "device_library_identifier",
            "pass_type_identifier",
        ),
    )

    device_library_identifier: str = Field(
        sa_column=_identifier(
            sa.ForeignKey(f"{SCHEMA}.device.device_library_identifier", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    pass_type_identifier: str = Field(sa_column=_identifier(primary_key=True))
    serial_number: str = Field(sa_column=_identifier(primary_key=True))
    delivered_tag: int | None = Field(
        default=None,
        sa_column=sa.Column(sa.BigInteger, nullable=True),
        description="The tag this device provably holds. Null until the first delivery.",
    )
    last_pushed_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )
    last_delivered_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )
    created_at: datetime = Field(default_factory=_utcnow, sa_column=_timestamp())
