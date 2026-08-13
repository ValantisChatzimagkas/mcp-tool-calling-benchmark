import json

import pytest

import config


@pytest.fixture(scope="session")
def suite():
    """The parsed cases.json, as run_eval.py sees it."""
    with open(config.CASES_PATH) as f:
        return json.load(f)
