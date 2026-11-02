import os

import pytest
from hypothesis import settings

import api
from rules import public_rules_dict, rules_from_dict

# CI runs every property test on a fixed set of examples, so a check can't pass on one run and
# fail on the next; a counterexample found there reproduces from the log alone. Local runs keep
# the random search (and its example database) for finding new cases. GitHub Actions sets CI.
# Loaded here, before the test modules import, so each @settings inherits it.
settings.register_profile("ci", derandomize=True, database=None, print_blob=True)
settings.load_profile("ci" if os.environ.get("CI") else "default")


@pytest.fixture(autouse=True)
def public_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """API tests run against the public rules, whatever private file this checkout holds, and
    without an API key; tests of the private mode set both themselves."""
    monkeypatch.setattr(api, "LOADED", rules_from_dict(public_rules_dict()))
    monkeypatch.setattr(api, "API_KEY", None)
