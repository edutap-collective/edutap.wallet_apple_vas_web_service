"""The schema this service owns, and its isolation from the shared database."""

import sqlalchemy as sa
from sqlmodel import SQLModel

from edutap.wallet_apple_vas_web_service import db_models
from edutap.wallet_apple_vas_web_service.base import SCHEMA, metadata


def test_metadata_holds_exactly_the_three_tables():
    assert sorted(metadata.tables) == [
        f"{SCHEMA}.device",
        f"{SCHEMA}.pass",
        f"{SCHEMA}.registration",
    ]


def test_no_table_is_left_to_search_path():
    assert {table.schema for table in metadata.tables.values()} == {SCHEMA}


def test_tables_are_not_registered_on_the_global_singleton():
    # SQLModel.metadata is process-wide. A table registered there would be
    # created into another package's migration run.
    assert not any(
        name.endswith((".device", ".pass", ".registration")) for name in SQLModel.metadata.tables
    )


def test_pass_data_is_withdrawn():
    assert not hasattr(db_models, "ApplePassData")


def test_registration_has_the_hot_path_index():
    registration = metadata.tables[f"{SCHEMA}.registration"]
    indexed = {tuple(column.name for column in index.columns) for index in registration.indexes}
    assert ("device_library_identifier", "pass_type_identifier") in indexed


def test_update_tag_sequence_is_created_with_the_schema():
    assert isinstance(db_models.UPDATE_TAG_SEQUENCE, sa.Sequence)
    assert db_models.UPDATE_TAG_SEQUENCE.schema == SCHEMA
    assert db_models.UPDATE_TAG_SEQUENCE.metadata is metadata
