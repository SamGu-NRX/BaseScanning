"""Regenerate the committed synthetic fixtures before the suite reads them."""

import pytest

from server.result.tests.support import write_fixtures


@pytest.fixture(scope="session", autouse=True)
def _fixtures():
    write_fixtures()
