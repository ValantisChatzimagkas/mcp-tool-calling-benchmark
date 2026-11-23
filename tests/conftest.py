import json

import pytest

import config

# Every suite file the integrity tests cover. `mcp_smoke.json` joins in Phase 3.
SUITE_PATHS = [config.CASES_PATH]


@pytest.fixture(scope="session", params=SUITE_PATHS)
def suite(request):
    """A parsed suite file, as run_eval.py sees it."""
    with open(request.param) as f:
        return json.load(f)


@pytest.fixture(scope="session")
def main_suite():
    """cases.json specifically, for checks that only make sense for the main sweep."""
    with open(config.CASES_PATH) as f:
        return json.load(f)
