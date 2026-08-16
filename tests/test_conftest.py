"""Behaviour of the shared fixtures declared in conftest.py.

`db_session`'s rollback isolation matters most for `.commit()`, not `.flush()`:
every endpoint test from here on calls `.commit()` through this exact fixture,
and SQLAlchemy 2.0's "conditional savepoint" join mode is what is relied on to
demote that `.commit()` to releasing a savepoint instead of committing the
outer transaction. That is a claim about a library version, not about this
package's own code, so it gets a test of its own rather than staying an
assumption behind the fixture.
"""

import pytest
from sqlmodel import select

from edutap.wallet_apple_vas_web_service.db_models import Device

# These two tests share the module-scoped `engine` fixture (via `db_session`) and
# are deliberately order-dependent: the second one only proves anything if it runs
# after the first, in the same module. That ordering is itself the property under
# test -- pytest keeps file order, and no randomising plugin is installed here (see
# the plugin list `pytest -v` reports). Do not reorder these or split them across
# files.


@pytest.mark.integration
def test_a_committed_row_is_rolled_back_by_the_fixture(db_session):
    db_session.add(Device(device_library_identifier="commit-isolation-dev", push_token="tok"))
    db_session.commit()


@pytest.mark.integration
def test_the_previous_commit_did_not_escape_to_the_shared_connection(db_session):
    rows = db_session.exec(select(Device)).all()
    assert rows == []
