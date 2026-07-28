"""Интеграционные тесты сквозных сценариев через HTTP API (п.8.2, 20.2 ТЗ):
розыгрыш -> открытие регистрации -> ручная регистрация -> подтверждение -> номерки;
webhook банка -> идемпотентная финализация онлайн-платежа."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login


def test_full_manual_sale_flow(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)

    resp = api_client.post(
        "/api/giveaways",
        json={
            "name": "Осенний розыгрыш",
            "prefix": "OSN",
            "ticket_price": 15000,
            "max_tickets": 20,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    giveaway_id = resp.json()["id"]

    resp = api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_registration_open"] is True

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "+7 999 111-22-33",
            "participant_full_name": "Иван Иванов",
            "quantity": 3,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    registration_id = resp.json()["id"]
    assert resp.json()["status"] == "PENDING"
    assert resp.json()["participant_full_name"] == "Иван Иванов"
    assert resp.json()["giveaway_name"] == "Осенний розыгрыш"
    assert resp.json()["operator_login"] == "admin"
    assert resp.json()["revenue"] == 3 * 15000

    resp = api_client.post(f"/api/manual-registrations/{registration_id}/confirm", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "CONFIRMED"

    resp = api_client.get("/api/tickets", headers=headers)
    assert resp.status_code == 200
    tickets = [t for t in resp.json()["items"] if t["giveaway_id"] == giveaway_id]
    assert len(tickets) == 3
    assert all(t["source"] == "manual" for t in tickets)
    assert all(t["participant_full_name"] == "Иван Иванов" for t in tickets)
    assert all(t["giveaway_name"] == "Осенний розыгрыш" for t in tickets)

    # Фильтр по manual_registration_id — для модалки «Показать номерки» на панели.
    resp = api_client.get(
        "/api/tickets",
        params={"manual_registration_id": registration_id},
        headers=headers,
    )
    assert resp.status_code == 200
    assert {t["id"] for t in resp.json()["items"]} == {t["id"] for t in tickets}

    resp = api_client.get("/api/dashboard", headers=headers)
    assert resp.status_code == 200
    dashboard = resp.json()
    assert dashboard["revenue_offline"] == 3 * 15000
    assert dashboard["revenue_online"] == 0
    assert dashboard["revenue_total"] == 3 * 15000

    resp = api_client.get(f"/api/giveaways/{giveaway_id}", headers=headers)
    assert resp.json()["tickets_issued"] == 3

    resp = api_client.get("/api/manual-registrations?export=csv", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    resp = api_client.get("/api/manual-registrations?export=xlsx", headers=headers)
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"

    api_client.post(
        "/api/panel-users",
        json={"login": "op_export", "password": "op-export-strong-pass", "role": "operator"},
        headers=headers,
    ).raise_for_status()
    op_token = login(api_client, "op_export", "op-export-strong-pass")
    resp = api_client.get("/api/manual-registrations?export=csv", headers=auth_headers(op_token))
    assert resp.status_code == 403
    resp = api_client.get("/api/manual-registrations", headers=auth_headers(op_token))
    assert (
        resp.status_code == 200
    )  # список без экспорта доступен всем ролям (MANUAL_REGISTRATION_CREATE)


def test_manual_registration_generate_qr_flow(api_client: TestClient) -> None:
    """Оператор формирует QR по просьбе покупателя (безнал), затем подтверждает
    после поступления денег — номерки выдаются как обычно, но выручка размечена
    как безналичная (см. DECISIONS.md)."""
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)

    resp = api_client.post(
        "/api/giveaways",
        json={"name": "QR Test", "prefix": "QRF", "ticket_price": 5000, "max_tickets": 10},
        headers=headers,
    )
    giveaway_id = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers)

    resp = api_client.get("/api/manual-registrations/999999/qr.png", headers=headers)
    assert resp.status_code == 404

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "+7 999 222-33-44",
            "participant_full_name": "Пётр Петров",
            "quantity": 2,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    registration_id = resp.json()["id"]
    assert resp.json()["payment_method"] == "CASH"
    assert resp.json()["invoice_no"] is None

    resp = api_client.get(f"/api/manual-registrations/{registration_id}/qr.png", headers=headers)
    assert resp.status_code == 404

    resp = api_client.post(
        f"/api/manual-registrations/{registration_id}/generate-qr", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["payment_method"] == "CASHLESS"
    invoice_no = resp.json()["invoice_no"]
    assert invoice_no is not None
    assert invoice_no.startswith("QRF-")

    resp = api_client.get(f"/api/manual-registrations/{registration_id}/qr.png", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert len(resp.content) > 0

    # Повторный вызов — тот же номер счёта, без повторного инкремента счётчика.
    resp = api_client.post(
        f"/api/manual-registrations/{registration_id}/generate-qr", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["invoice_no"] == invoice_no

    resp = api_client.post(f"/api/manual-registrations/{registration_id}/confirm", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "CONFIRMED"
    assert resp.json()["payment_method"] == "CASHLESS"

    resp = api_client.get("/api/reports/online-vs-offline", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["offline_cashless"]["amount"] == 2 * 5000
    assert body["offline_cash"]["amount"] == 0

    # QR можно сформировать только для PENDING.
    resp = api_client.post(
        f"/api/manual-registrations/{registration_id}/generate-qr", headers=headers
    )
    assert resp.status_code == 409


def test_manual_registration_switch_to_cash_after_failed_qr_payment(api_client: TestClient) -> None:
    """Покупатель сформировал QR, но не смог/не захотел оплатить по нему и
    решил заплатить оператору наличными (см. DECISIONS.md)."""
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)

    resp = api_client.post(
        "/api/giveaways",
        json={"name": "Cash Fallback", "prefix": "CSHF", "ticket_price": 3000, "max_tickets": 10},
        headers=headers,
    )
    giveaway_id = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers)

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "+7 999 333-44-55",
            "participant_full_name": "Сидор Сидоров",
            "quantity": 1,
        },
        headers=headers,
    )
    registration_id = resp.json()["id"]

    resp = api_client.post(
        f"/api/manual-registrations/{registration_id}/generate-qr", headers=headers
    )
    assert resp.json()["payment_method"] == "CASHLESS"
    invoice_no = resp.json()["invoice_no"]

    resp = api_client.post(
        f"/api/manual-registrations/{registration_id}/switch-to-cash", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["payment_method"] == "CASH"
    # Номер счёта не стирается — просто перестаёт быть актуальным способом оплаты.
    assert resp.json()["invoice_no"] == invoice_no

    resp = api_client.post(f"/api/manual-registrations/{registration_id}/confirm", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "CONFIRMED"
    assert resp.json()["payment_method"] == "CASH"

    resp = api_client.get("/api/reports/online-vs-offline", headers=headers)
    body = resp.json()
    assert body["offline_cash"]["amount"] == 3000
    assert body["offline_cashless"]["amount"] == 0

    # Сменить способ оплаты после подтверждения уже нельзя.
    resp = api_client.post(
        f"/api/manual-registrations/{registration_id}/switch-to-cash", headers=headers
    )
    assert resp.status_code == 409


def test_giveaway_immutable_fields_not_editable_after_open(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    resp = api_client.post(
        "/api/giveaways",
        json={"name": "G", "prefix": "IMM", "ticket_price": 1000, "max_tickets": 5},
        headers=headers,
    )
    giveaway_id = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers)

    # Повторное открытие запрещено
    resp = api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers)
    assert resp.status_code == 400

    # Разрешено менять только name/digital_poster_caption — prefix/price/max_tickets
    # физически не принимаются схемой GiveawayUpdateRequest.
    resp = api_client.patch(
        f"/api/giveaways/{giveaway_id}", json={"name": "Новое имя"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Новое имя"
    assert resp.json()["prefix"] == "IMM"


def test_manual_registration_insufficient_tickets_returns_409(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    resp = api_client.post(
        "/api/giveaways",
        json={"name": "Small", "prefix": "SML", "ticket_price": 1000, "max_tickets": 2},
        headers=headers,
    )
    giveaway_id = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers)

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990000000",
            "participant_full_name": "Пётр Петров",
            "quantity": 5,
        },
        headers=headers,
    )
    assert resp.status_code == 409


def test_manual_registration_for_unopened_giveaway_returns_409_not_500(
    api_client: TestClient,
) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    resp = api_client.post(
        "/api/giveaways",
        json={"name": "Not Open", "prefix": "NOP", "ticket_price": 1000, "max_tickets": 10},
        headers=headers,
    )
    giveaway_id = resp.json()["id"]
    # Регистрацию не открываем — розыгрыш не продаётся.

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990000001",
            "participant_full_name": "Не Продаётся",
            "quantity": 1,
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert "не открыта" in resp.json()["detail"]


def test_operator_sees_only_own_manual_registrations(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    admin_headers = auth_headers(admin_token)

    api_client.post(
        "/api/panel-users",
        json={"login": "op_a", "password": "op-a-strong-pass", "role": "operator"},
        headers=admin_headers,
    ).raise_for_status()
    api_client.post(
        "/api/panel-users",
        json={"login": "op_b", "password": "op-b-strong-pass", "role": "operator"},
        headers=admin_headers,
    ).raise_for_status()

    resp = api_client.post(
        "/api/giveaways",
        json={"name": "Shared", "prefix": "SHR", "ticket_price": 1000, "max_tickets": 20},
        headers=admin_headers,
    )
    giveaway_id = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=admin_headers)

    op_a_token = login(api_client, "op_a", "op-a-strong-pass")
    op_b_token = login(api_client, "op_b", "op-b-strong-pass")

    api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79991110000",
            "participant_full_name": "Оператор А Клиент",
            "quantity": 1,
        },
        headers=auth_headers(op_a_token),
    ).raise_for_status()
    api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79992220000",
            "participant_full_name": "Оператор Б Клиент",
            "quantity": 1,
        },
        headers=auth_headers(op_b_token),
    ).raise_for_status()

    resp_a = api_client.get("/api/manual-registrations", headers=auth_headers(op_a_token))
    resp_b = api_client.get("/api/manual-registrations", headers=auth_headers(op_b_token))
    assert resp_a.json()["total"] == 1
    assert resp_b.json()["total"] == 1
    assert len(resp_a.json()["items"]) == 1
    assert len(resp_b.json()["items"]) == 1
    assert resp_a.json()["items"][0]["id"] != resp_b.json()["items"][0]["id"]

    # Администратор видит обе
    resp_admin = api_client.get("/api/manual-registrations", headers=admin_headers)
    assert resp_admin.json()["total"] == 2
    assert len(resp_admin.json()["items"]) == 2


def test_manual_registration_name_overwrite_restricted_by_role(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")  # super_admin
    admin_headers = auth_headers(admin_token)

    api_client.post(
        "/api/panel-users",
        json={"login": "adm1", "password": "adm1-strong-pass", "role": "administrator"},
        headers=admin_headers,
    ).raise_for_status()
    api_client.post(
        "/api/panel-users",
        json={"login": "op1", "password": "op1-strong-pass", "role": "operator"},
        headers=admin_headers,
    ).raise_for_status()

    resp = api_client.post(
        "/api/giveaways",
        json={"name": "Overwrite Test", "prefix": "OVR", "ticket_price": 1000, "max_tickets": 100},
        headers=admin_headers,
    )
    giveaway_id = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=admin_headers)

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79995551234",
            "participant_full_name": "Исходное Имя",
            "quantity": 1,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["participant_full_name"] == "Исходное Имя"
    # Каждая регистрация подтверждается сразу же — иначе следующая для того же
    # участника упрётся в правило "не более одной активной покупки" (см. DECISIONS.md).
    api_client.post(
        f"/api/manual-registrations/{resp.json()['id']}/confirm", headers=admin_headers
    ).raise_for_status()

    adm_token = login(api_client, "adm1", "adm1-strong-pass")
    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79995551234",
            "participant_full_name": "Имя От Администратора",
            "quantity": 1,
        },
        headers=auth_headers(adm_token),
    )
    assert resp.status_code == 201
    assert resp.json()["participant_full_name"] == "Исходное Имя"  # Administrator не может изменить
    api_client.post(
        f"/api/manual-registrations/{resp.json()['id']}/confirm", headers=admin_headers
    ).raise_for_status()

    op_token = login(api_client, "op1", "op1-strong-pass")
    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79995551234",
            "participant_full_name": "Имя От Оператора",
            "quantity": 1,
        },
        headers=auth_headers(op_token),
    )
    assert resp.status_code == 201
    assert resp.json()["participant_full_name"] == "Исходное Имя"  # Operator тоже не может
    api_client.post(
        f"/api/manual-registrations/{resp.json()['id']}/confirm", headers=admin_headers
    ).raise_for_status()

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79995551234",
            "participant_full_name": "Имя От Super Admin",
            "quantity": 1,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["participant_full_name"] == "Имя От Super Admin"  # Super Admin может


def test_participant_lookup_by_phone(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(admin_token)

    resp = api_client.get(
        "/api/participants/by-phone", params={"phone": "79995559999"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json() is None

    resp = api_client.post(
        "/api/giveaways",
        json={"name": "Lookup Test", "prefix": "LKP", "ticket_price": 1000, "max_tickets": 10},
        headers=headers,
    )
    giveaway_id = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers)
    api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79995559999",
            "participant_full_name": "Найденный Участник",
            "quantity": 1,
        },
        headers=headers,
    ).raise_for_status()

    resp = api_client.get(
        "/api/participants/by-phone", params={"phone": "+7 999 555-99-99"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Найденный Участник"


def test_audit_log_records_significant_actions(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    api_client.post(
        "/api/giveaways",
        json={"name": "Audited", "prefix": "AUD", "ticket_price": 1000, "max_tickets": 5},
        headers=headers,
    )
    resp = api_client.get("/api/audit", headers=headers)
    assert resp.status_code == 200
    actions = {row["action"] for row in resp.json()}
    assert "panel_login_success" in actions
    assert "giveaway_create" in actions
