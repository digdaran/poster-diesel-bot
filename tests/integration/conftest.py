"""Фикстуры для интеграционных тестов API (FastAPI TestClient + изолированная БД)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest
from app.core.config import get_settings
from fastapi.testclient import TestClient


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)

    env_overrides = {
        "DATABASE_PATH": db_path,
        "JWT_SECRET": "test-secret-for-api-integration-tests-only",
        "SUPERADMIN_LOGIN": "admin",
        "SUPERADMIN_PASSWORD": "admin-strong-pass-123",
        "PAYMENT_PROVIDER": "mock",
    }
    old_env = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update(env_overrides)
    get_settings.cache_clear()

    from backend.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client

    get_settings.cache_clear()
    for k, v in old_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = db_path + suffix
        if os.path.exists(p):
            os.remove(p)


def login(client: TestClient, login_: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"login": login_, "password": password})
    resp.raise_for_status()
    return resp.json()["access_token"]  # type: ignore[no-any-return]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
