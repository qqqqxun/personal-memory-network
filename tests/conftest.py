from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="memory-net-"))
os.environ["MEMORY_NET_TEST"] = "1"
os.environ["MEMORY_NET_DATA_DIR"] = str(TMP)
os.environ["MEMORY_NET_VAULT_DIR"] = str(TMP / "vault")
os.environ["MEMORY_NET_MASTER_KEY"] = "ab" * 32
os.environ["MEMORY_NET_TOKEN"] = "test-token"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Store  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        test_client.headers.update({"X-Memory-Token": "test-token"})
        yield test_client


def fresh_store() -> Store:
    return Store(path=TMP / f"{uuid.uuid4()}.db")
