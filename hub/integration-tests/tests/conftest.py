import os
import time

import httpx
import pytest

_MAX_RERUN_DELAY = 32


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Apply exponential backoff between reruns for flaky-marked tests."""
    if not item.get_closest_marker("flaky"):
        return
    count = getattr(item, "execution_count", 0)
    if count > 0:
        delay = min(2**count, _MAX_RERUN_DELAY)
        time.sleep(delay)


def pytest_collection_modifyitems(items):
    """Run kafka tests first. Agent consumer handles one alert at a time."""
    items.sort(key=lambda item: 0 if "/kafka/" in str(item.fspath) else 1)


@pytest.fixture(scope="session")
def chatbot_client():
    base_url = os.environ.get("CHATBOT_SERVICE_URL", "http://localhost:8080")
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client
