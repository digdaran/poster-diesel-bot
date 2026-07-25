"""API загрузки/просмотра/удаления цифровых постеров розыгрыша (см.
DECISIONS.md №46) — загрузка через веб-админку вместо ручной правки БД."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login


def _create_giveaway(api_client: TestClient, headers: dict[str, str]) -> int:
    resp = api_client.post(
        "/api/giveaways",
        json={"name": "Posters", "prefix": "PST", "ticket_price": 1000, "max_tickets": 10},
        headers=headers,
    )
    resp.raise_for_status()
    return resp.json()["id"]  # type: ignore[no-any-return]


def test_upload_list_and_download_poster(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_giveaway(api_client, headers)

    upload_resp = api_client.post(
        f"/api/giveaways/{giveaway_id}/posters",
        files={"file": ("poster.png", b"fake-png-bytes", "image/png")},
        headers=headers,
    )
    upload_resp.raise_for_status()
    poster = upload_resp.json()
    assert poster["giveaway_id"] == giveaway_id
    assert poster["original_filename"] == "poster.png"
    assert poster["content_type"] == "image/png"

    list_resp = api_client.get(f"/api/giveaways/{giveaway_id}/posters", headers=headers)
    list_resp.raise_for_status()
    assert [p["id"] for p in list_resp.json()] == [poster["id"]]

    file_resp = api_client.get(
        f"/api/giveaways/{giveaway_id}/posters/{poster['id']}/file", headers=headers
    )
    file_resp.raise_for_status()
    assert file_resp.content == b"fake-png-bytes"
    assert file_resp.headers["content-type"] == "image/png"


def test_upload_poster_rejects_disallowed_content_type(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_giveaway(api_client, headers)

    resp = api_client.post(
        f"/api/giveaways/{giveaway_id}/posters",
        files={"file": ("poster.pdf", b"%PDF-fake", "application/pdf")},
        headers=headers,
    )
    assert resp.status_code == 400


def test_upload_poster_404_for_unknown_giveaway(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)

    resp = api_client.post(
        "/api/giveaways/999/posters",
        files={"file": ("poster.png", b"data", "image/png")},
        headers=headers,
    )
    assert resp.status_code == 404


def test_delete_poster_removes_it_from_list(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_giveaway(api_client, headers)
    upload_resp = api_client.post(
        f"/api/giveaways/{giveaway_id}/posters",
        files={"file": ("poster.png", b"data", "image/png")},
        headers=headers,
    )
    poster_id = upload_resp.json()["id"]

    delete_resp = api_client.delete(
        f"/api/giveaways/{giveaway_id}/posters/{poster_id}", headers=headers
    )
    assert delete_resp.status_code == 204

    list_resp = api_client.get(f"/api/giveaways/{giveaway_id}/posters", headers=headers)
    assert list_resp.json() == []

    file_resp = api_client.get(
        f"/api/giveaways/{giveaway_id}/posters/{poster_id}/file", headers=headers
    )
    assert file_resp.status_code == 404


def test_delete_poster_404_for_unknown_poster(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_giveaway(api_client, headers)

    resp = api_client.delete(f"/api/giveaways/{giveaway_id}/posters/999", headers=headers)
    assert resp.status_code == 404


def test_operator_cannot_upload_or_delete_poster_but_can_view(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    admin_headers = auth_headers(admin_token)
    giveaway_id = _create_giveaway(api_client, admin_headers)
    upload_resp = api_client.post(
        f"/api/giveaways/{giveaway_id}/posters",
        files={"file": ("poster.png", b"data", "image/png")},
        headers=admin_headers,
    )
    poster_id = upload_resp.json()["id"]

    operator_resp = api_client.post(
        "/api/panel-users",
        json={"login": "operator1", "password": "operator-strong-pass-123", "role": "operator"},
        headers=admin_headers,
    )
    operator_resp.raise_for_status()
    operator_token = login(api_client, "operator1", "operator-strong-pass-123")
    operator_headers = auth_headers(operator_token)

    forbidden_upload = api_client.post(
        f"/api/giveaways/{giveaway_id}/posters",
        files={"file": ("poster2.png", b"data", "image/png")},
        headers=operator_headers,
    )
    assert forbidden_upload.status_code == 403

    forbidden_delete = api_client.delete(
        f"/api/giveaways/{giveaway_id}/posters/{poster_id}", headers=operator_headers
    )
    assert forbidden_delete.status_code == 403

    allowed_list = api_client.get(f"/api/giveaways/{giveaway_id}/posters", headers=operator_headers)
    assert allowed_list.status_code == 200
