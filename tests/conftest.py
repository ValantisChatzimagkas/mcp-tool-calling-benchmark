import json

import pytest

import config
import sandbox

# Every suite file the integrity tests cover.
SUITE_PATHS = [config.CASES_PATH, "mcp_smoke.json"]


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


@pytest.fixture(autouse=True)
def forbid_server_installs(request, monkeypatch):
    """Only `-m live` tests may install a server (npm/uv, network). Everything else fails."""
    if request.node.get_closest_marker("live"):
        return

    def refuse(argv):
        pytest.fail(f"server install attempted outside -m live: {argv}")

    monkeypatch.setattr(sandbox, "_run_installer", refuse)
