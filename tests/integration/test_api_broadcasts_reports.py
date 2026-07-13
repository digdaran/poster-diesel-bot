"""Интеграционные тесты рассылок и отчётов через HTTP (п.15, 16, 11.3 ТЗ):
Operator не имеет доступа, Administrator/Super Admin — да; экспорт CSV/XLSX."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login


def test_operator_forbidden_from_broadcasts_and_reports(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    api_client.post(
        "/api/panel-users",
        json={"login": "op_br", "password": "op-br-strong-pass", "role": "operator"},
        headers=auth_headers(admin_token),
    ).raise_for_status()
    op_token = login(api_client, "op_br", "op-br-strong-pass")

    for path in ["/api/broadcasts", "/api/reports/financial-summary", "/api/reports/by-provider"]:
        resp = api_client.get(path, headers=auth_headers(op_token))
        assert resp.status_code == 403, path


def test_administrator_can_create_and_send_broadcast(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(admin_token)

    resp = api_client.post(
        "/api/broadcasts",
        json={
            "title": "Акция",
            "message_text": "Успей купить!",
            "audience_filter": {"segment": "all"},
        },
        headers=headers,
    )
    assert resp.status_code == 201
    broadcast_id = resp.json()["id"]
    assert resp.json()["status"] == "DRAFT"

    resp = api_client.post(f"/api/broadcasts/{broadcast_id}/send", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "SENT"


def test_financial_summary_and_export(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(admin_token)

    resp = api_client.get("/api/reports/financial-summary", headers=headers)
    assert resp.status_code == 200
    assert "revenue_total" in resp.json()

    resp = api_client.get("/api/reports/by-provider?export=csv", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    resp = api_client.get("/api/reports/by-provider?export=xlsx", headers=headers)
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"
