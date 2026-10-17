from __future__ import annotations

import pytest
from dashboard import api


@pytest.fixture(autouse=True)
def _allow_testclient_host(monkeypatch):
    """`TestClient` sends `Host: testserver`. Added here, never to `api.ALLOWED_HOSTS` itself."""
    monkeypatch.setattr(api, "ALLOWED_HOSTS", (*api.ALLOWED_HOSTS, "testserver"))
