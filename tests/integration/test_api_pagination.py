"""Пагинация и фильтры по столбцам для Продажи/Номерки/Ручные регистрации/
Участники (запрос владельца: 5000 участников/20000 номерков/150 розыгрышей —
списки не должны отдаваться целиком)."""

from __future__ import annotations

import datetime as dt

from app.core.config import get_settings
from app.core.db import Database
from app.models.channel_binding import ChannelBinding
from app.models.enums import ChannelType, PaymentProviderType, PaymentStatus
from app.models.payment import Payment
from app.payments.requisites_qr import RequisitesQrProvider
from app.services import payment_service
from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login


def _seed_payments(giveaway_id: int, participant_id: int) -> None:
    """Платежи создаются ботом/банком, у панели нет HTTP-эндпоинта для этого —
    сеем напрямую через ORM (тот же паттерн, что tests/unit/test_reports.py)."""
    db = Database(get_settings())
    with db.session() as session:
        session.add(
            Payment(
                order_id="pg-mock-1",
                participant_id=participant_id,
                giveaway_id=giveaway_id,
                provider=PaymentProviderType.MOCK,
                amount=10000,
                quantity=1,
                status=PaymentStatus.SUCCEEDED,
            )
        )
        session.add(
            Payment(
                order_id="pg-tbank-1",
                participant_id=participant_id,
                giveaway_id=giveaway_id,
                provider=PaymentProviderType.TBANK,
                amount=20000,
                quantity=2,
                status=PaymentStatus.SUCCEEDED,
            )
        )
    db.engine.dispose()


def _seed_custom_payment(
    giveaway_id: int,
    participant_id: int,
    *,
    order_id: str,
    payment_number: int | None = None,
    amount: int = 10000,
    amount_mismatch: bool = False,
    amount_mismatch_bank_amount: int | None = None,
    oversold: bool = False,
) -> None:
    """Как `_seed_payments` — платежи с конкретными invoice_no/расхождением/oversold
    для проверки новых фильтров /api/payments (нет HTTP-пути их создать)."""
    db = Database(get_settings())
    with db.session() as session:
        session.add(
            Payment(
                order_id=order_id,
                participant_id=participant_id,
                giveaway_id=giveaway_id,
                provider=PaymentProviderType.REQUISITES_QR,
                payment_number=payment_number,
                amount=amount,
                quantity=1,
                status=PaymentStatus.SUCCEEDED,
                amount_mismatch=amount_mismatch,
                amount_mismatch_bank_amount=amount_mismatch_bank_amount,
                oversold=oversold,
            )
        )
    db.engine.dispose()


def _create_open_giveaway(api_client: TestClient, headers: dict[str, str], **kwargs: object) -> int:
    payload = {"name": "Pagination", "prefix": "PG1", "ticket_price": 1000, "max_tickets": 30}
    payload.update(kwargs)
    resp = api_client.post("/api/giveaways", json=payload, headers=headers)
    resp.raise_for_status()
    giveaway_id: int = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers).raise_for_status()
    return giveaway_id


def test_tickets_pagination_pages_through_results(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="PGT")

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990001111",
            "participant_full_name": "Постранично Иванов",
            "quantity": 15,
        },
        headers=headers,
    )
    resp.raise_for_status()
    api_client.post(
        f"/api/manual-registrations/{resp.json()['id']}/confirm", headers=headers
    ).raise_for_status()

    resp = api_client.get("/api/tickets", params={"page": 1, "page_size": 10}, headers=headers)
    body = resp.json()
    assert body["total"] == 15
    assert len(body["items"]) == 10
    assert body["page"] == 1
    assert body["page_size"] == 10

    resp = api_client.get("/api/tickets", params={"page": 2, "page_size": 10}, headers=headers)
    body = resp.json()
    assert body["total"] == 15
    assert len(body["items"]) == 5

    resp = api_client.get("/api/tickets", params={"page": 3, "page_size": 10}, headers=headers)
    body = resp.json()
    assert body["total"] == 15
    assert body["items"] == []


def test_tickets_page_size_rejects_arbitrary_values(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    resp = api_client.get("/api/tickets", params={"page_size": 37}, headers=headers)
    assert resp.status_code == 422


def test_tickets_export_ignores_pagination_but_respects_filters(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="PGE")
    other_giveaway_id = _create_open_giveaway(api_client, headers, prefix="PGO")

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990002222",
            "participant_full_name": "Экспорт Петров",
            "quantity": 15,
        },
        headers=headers,
    )
    resp.raise_for_status()
    api_client.post(
        f"/api/manual-registrations/{resp.json()['id']}/confirm", headers=headers
    ).raise_for_status()
    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": other_giveaway_id,
            "participant_phone": "79990003333",
            "participant_full_name": "Другой Розыгрыш",
            "quantity": 2,
        },
        headers=headers,
    )
    resp.raise_for_status()
    api_client.post(
        f"/api/manual-registrations/{resp.json()['id']}/confirm", headers=headers
    ).raise_for_status()

    # page_size=10 бы отдал только часть, но export должен вернуть все 15
    # строк отфильтрованного розыгрыша, не всю таблицу (17) и не одну страницу.
    resp = api_client.get(
        "/api/tickets",
        params={"giveaway_id": giveaway_id, "page": 1, "page_size": 10, "export": "csv"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    csv_lines = [line for line in resp.text.strip().split("\n") if line]
    assert len(csv_lines) - 1 == 15  # минус строка заголовка


def test_manual_registrations_filter_by_giveaway_and_participant_query(
    api_client: TestClient,
) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    g1 = _create_open_giveaway(api_client, headers, prefix="MRF1")
    g2 = _create_open_giveaway(api_client, headers, prefix="MRF2")

    api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": g1,
            "participant_phone": "79990004444",
            "participant_full_name": "Найди Меня Пожалуйста",
            "quantity": 1,
        },
        headers=headers,
    ).raise_for_status()
    api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": g2,
            "participant_phone": "79990005555",
            "participant_full_name": "Совсем Другой Человек",
            "quantity": 1,
        },
        headers=headers,
    ).raise_for_status()

    resp = api_client.get("/api/manual-registrations", params={"giveaway_id": g1}, headers=headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["giveaway_id"] == g1

    resp = api_client.get(
        "/api/manual-registrations", params={"participant_query": "Найди"}, headers=headers
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["participant_full_name"] == "Найди Меня Пожалуйста"

    resp = api_client.get(
        "/api/manual-registrations", params={"participant_query": "Никого Нет"}, headers=headers
    )
    assert resp.json()["total"] == 0


def test_payments_filter_by_provider_and_order_id(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="PAY")
    participant_resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990006666",
            "participant_full_name": "Плательщик",
            "quantity": 1,
        },
        headers=headers,
    )
    participant_resp.raise_for_status()
    participant_id = participant_resp.json()["participant_id"]
    _seed_payments(giveaway_id, participant_id)

    resp = api_client.get("/api/payments", params={"provider": "tbank"}, headers=headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["order_id"] == "pg-tbank-1"

    resp = api_client.get("/api/payments", params={"order_id": "mock"}, headers=headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["provider"] == "mock"

    future = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    resp = api_client.get("/api/payments", params={"created_from": future}, headers=headers)
    assert resp.json()["total"] == 0


def test_participants_filter_by_blocked_and_verified(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="PRT")

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990007777",
            "participant_full_name": "Будет Заблокирован",
            "quantity": 1,
        },
        headers=headers,
    )
    resp.raise_for_status()
    participant_id = resp.json()["participant_id"]
    api_client.post(f"/api/participants/{participant_id}/block", headers=headers).raise_for_status()

    resp = api_client.get("/api/participants", params={"is_blocked": True}, headers=headers)
    body = resp.json()
    assert body["total"] >= 1
    assert all(p["is_blocked"] for p in body["items"])
    assert any(p["id"] == participant_id for p in body["items"])

    resp = api_client.get(
        "/api/participants",
        params={"is_blocked": False, "q": "Будет Заблокирован"},
        headers=headers,
    )
    assert resp.json()["total"] == 0  # заблокирован, под этим фильтром не пройдёт


def test_participant_id_filter_scopes_payments_tickets_and_registrations(
    api_client: TestClient,
) -> None:
    """Раздел «Участники» — просмотр заказов/оплат/номерков конкретного
    участника (по запросу заказчика) фильтрует /payments, /tickets и
    /manual-registrations по participant_id, не задевая данные других
    участников."""
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="PID")

    reg_a = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990008881",
            "participant_full_name": "Участник А",
            "quantity": 1,
        },
        headers=headers,
    )
    reg_a.raise_for_status()
    participant_a_id = reg_a.json()["participant_id"]
    api_client.post(
        f"/api/manual-registrations/{reg_a.json()['id']}/confirm", headers=headers
    ).raise_for_status()
    _seed_payments(giveaway_id, participant_a_id)

    reg_b = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990008882",
            "participant_full_name": "Участник Б",
            "quantity": 1,
        },
        headers=headers,
    )
    reg_b.raise_for_status()
    participant_b_id = reg_b.json()["participant_id"]
    api_client.post(
        f"/api/manual-registrations/{reg_b.json()['id']}/confirm", headers=headers
    ).raise_for_status()

    resp = api_client.get(
        "/api/payments", params={"participant_id": participant_a_id}, headers=headers
    )
    body = resp.json()
    assert body["total"] == 2
    assert all(p["participant_id"] == participant_a_id for p in body["items"])

    resp = api_client.get(
        "/api/tickets", params={"participant_id": participant_a_id}, headers=headers
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["participant_id"] == participant_a_id

    resp = api_client.get(
        "/api/tickets", params={"participant_id": participant_b_id}, headers=headers
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["participant_id"] == participant_b_id

    resp = api_client.get(
        "/api/manual-registrations", params={"participant_id": participant_a_id}, headers=headers
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["participant_id"] == participant_a_id


def test_audit_log_filters_and_pagination(api_client: TestClient) -> None:
    """Журнал аудита: подстрочный поиск по action/actor_label/ip, точное
    совпадение entity_type/entity_id, диапазон по created_at, и постраничная
    выдача вместо жёсткого limit=100 (см. рекомендации по доработке поиска)."""
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)

    create_resp = api_client.post(
        "/api/giveaways",
        json={"name": "AuditFilter", "prefix": "AFL", "ticket_price": 500, "max_tickets": 10},
        headers=headers,
    )
    create_resp.raise_for_status()
    giveaway_id = create_resp.json()["id"]

    unfiltered = api_client.get("/api/audit", headers=headers).json()
    create_row = next(
        row
        for row in unfiltered["items"]
        if row["action"] == "giveaway_create" and row["entity_id"] == giveaway_id
    )
    assert create_row["entity_type"] == "giveaway"
    ip_value = create_row["ip_address"]

    # action ищется подстрокой — "giveaway_c" находит giveaway_create без
    # точного значения enum'а (действий много и список постоянно растёт).
    resp = api_client.get("/api/audit", params={"action": "giveaway_c"}, headers=headers)
    assert any(row["id"] == create_row["id"] for row in resp.json()["items"])

    resp = api_client.get(
        "/api/audit",
        params={"entity_type": "giveaway", "entity_id": giveaway_id},
        headers=headers,
    )
    body = resp.json()
    assert body["total"] >= 1
    assert all(row["entity_type"] == "giveaway" for row in body["items"])
    assert all(row["entity_id"] == giveaway_id for row in body["items"])

    resp = api_client.get("/api/audit", params={"actor_query": "adm"}, headers=headers)
    assert resp.json()["total"] >= 1
    assert all("adm" in row["actor_label"].lower() for row in resp.json()["items"])

    if ip_value:
        resp = api_client.get("/api/audit", params={"ip_address": ip_value}, headers=headers)
        assert resp.json()["total"] >= 1

    today = dt.date.today()
    resp = api_client.get(
        "/api/audit",
        params={"created_from": today.isoformat(), "created_to": today.isoformat()},
        headers=headers,
    )
    assert any(row["id"] == create_row["id"] for row in resp.json()["items"])

    resp = api_client.get(
        "/api/audit",
        params={"created_to": (today - dt.timedelta(days=1)).isoformat()},
        headers=headers,
    )
    assert all(row["id"] != create_row["id"] for row in resp.json()["items"])

    resp = api_client.get("/api/audit", params={"page_size": 10, "page": 1}, headers=headers)
    body = resp.json()
    assert len(body["items"]) <= 10
    assert body["total"] >= len(body["items"])


def test_giveaways_filter_by_name_prefix_and_status(api_client: TestClient) -> None:
    """Раздел «Коллекции» раньше отдавался целиком без единого фильтра —
    добавлены q (название/префикс) и is_registration_open/is_locked. Ответ
    остаётся плоским списком (не items/total) — этот же эндпоинт без
    параметров используется как источник select-опций на других страницах
    (Продажи On-Line/Номера/Ручные регистрации/Отчёты)."""
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)

    open_id = _create_open_giveaway(api_client, headers, name="Открытая Летняя", prefix="OPN")
    closed_resp = api_client.post(
        "/api/giveaways",
        json={"name": "Закрытая Зимняя", "prefix": "CLS", "ticket_price": 100, "max_tickets": 5},
        headers=headers,
    )
    closed_resp.raise_for_status()
    closed_id = closed_resp.json()["id"]
    api_client.post(f"/api/giveaways/{closed_id}/lock", headers=headers).raise_for_status()

    resp = api_client.get("/api/giveaways", params={"q": "Летняя"}, headers=headers)
    ids = {g["id"] for g in resp.json()}
    assert open_id in ids
    assert closed_id not in ids

    resp = api_client.get("/api/giveaways", params={"q": "OPN"}, headers=headers)
    assert any(g["id"] == open_id for g in resp.json())

    resp = api_client.get("/api/giveaways", params={"is_registration_open": True}, headers=headers)
    ids = {g["id"] for g in resp.json()}
    assert open_id in ids
    assert closed_id not in ids

    resp = api_client.get("/api/giveaways", params={"is_locked": True}, headers=headers)
    ids = {g["id"] for g in resp.json()}
    assert closed_id in ids
    assert open_id not in ids

    resp = api_client.get("/api/giveaways", headers=headers)
    assert isinstance(resp.json(), list)


def test_sales_filter_by_invoice_no_amount_mismatch_and_oversold(api_client: TestClient) -> None:
    """Раздел «Продажи On-Line»: столбец «Счёт №» раньше не участвовал ни в
    каком фильтре (только order_id) — добавлен invoice_no; плюс чекбоксы
    "только с расхождением суммы"/"только oversold" поверх уже отображаемых
    бейджей."""
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="SLI")

    reg_resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990001122",
            "participant_full_name": "Продажи Фильтр",
            "quantity": 1,
        },
        headers=headers,
    )
    reg_resp.raise_for_status()
    participant_id = reg_resp.json()["participant_id"]

    _seed_custom_payment(
        giveaway_id, participant_id, order_id="sli-plain", payment_number=1, amount=10000
    )
    _seed_custom_payment(
        giveaway_id,
        participant_id,
        order_id="sli-mismatch",
        payment_number=2,
        amount=10000,
        amount_mismatch=True,
        amount_mismatch_bank_amount=12000,
    )
    _seed_custom_payment(
        giveaway_id, participant_id, order_id="sli-oversold", payment_number=3, oversold=True
    )

    resp = api_client.get("/api/payments", params={"invoice_no": "SLI-00002"}, headers=headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["order_id"] == "sli-mismatch"

    resp = api_client.get("/api/payments", params={"invoice_no": "SLI"}, headers=headers)
    assert resp.json()["total"] == 3

    resp = api_client.get("/api/payments", params={"invoice_no": "00003"}, headers=headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["order_id"] == "sli-oversold"

    resp = api_client.get("/api/payments", params={"amount_mismatch": True}, headers=headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["order_id"] == "sli-mismatch"

    resp = api_client.get("/api/payments", params={"oversold": True}, headers=headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["order_id"] == "sli-oversold"


def test_manual_registrations_filter_by_operator_payment_method_and_invoice_no(
    api_client: TestClient,
) -> None:
    """Раздел «Ручные регистрации»: столбцы «Оператор»/«Оплата»/«Счёт №»
    отображались, но не участвовали ни в каком фильтре — добавлены
    operator_query, payment_method, invoice_no."""
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="MRO")

    resp = api_client.post(
        "/api/panel-users",
        json={"login": "operator-mro", "password": "operator-strong-pass", "role": "operator"},
        headers=headers,
    )
    resp.raise_for_status()

    reg_admin = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990006666",
            "participant_full_name": "Регистрация Админа",
            "quantity": 1,
        },
        headers=headers,
    )
    reg_admin.raise_for_status()
    admin_reg_id = reg_admin.json()["id"]

    op_token = login(api_client, "operator-mro", "operator-strong-pass")
    op_headers = auth_headers(op_token)
    reg_operator = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990007777",
            "participant_full_name": "Регистрация Оператора",
            "quantity": 1,
        },
        headers=op_headers,
    )
    reg_operator.raise_for_status()
    operator_reg_id = reg_operator.json()["id"]

    # Переводим регистрацию оператора на безнал по QR — только тогда
    # заполняются payment_method=CASHLESS и invoice_no.
    qr_resp = api_client.post(
        f"/api/manual-registrations/{operator_reg_id}/generate-qr", headers=headers
    )
    qr_resp.raise_for_status()
    invoice_no = qr_resp.json()["invoice_no"]
    assert invoice_no is not None

    resp = api_client.get(
        "/api/manual-registrations", params={"operator_query": "operator-mro"}, headers=headers
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == operator_reg_id

    resp = api_client.get(
        "/api/manual-registrations", params={"operator_query": "admin"}, headers=headers
    )
    ids = {r["id"] for r in resp.json()["items"]}
    assert admin_reg_id in ids
    assert operator_reg_id not in ids

    resp = api_client.get(
        "/api/manual-registrations", params={"payment_method": "CASHLESS"}, headers=headers
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == operator_reg_id

    resp = api_client.get(
        "/api/manual-registrations", params={"payment_method": "CASH"}, headers=headers
    )
    ids = {r["id"] for r in resp.json()["items"]}
    assert admin_reg_id in ids
    assert operator_reg_id not in ids

    resp = api_client.get(
        "/api/manual-registrations", params={"invoice_no": invoice_no}, headers=headers
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == operator_reg_id


def test_participants_filter_by_channel(api_client: TestClient) -> None:
    """Раздел «Участники»: столбец «Каналы» отображался, но фильтра по каналу
    привязки не было (в отличие от Продаж On-Line/Номеров)."""
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="PCH")

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990008888",
            "participant_full_name": "Без Привязки Канала",
            "quantity": 1,
        },
        headers=headers,
    )
    resp.raise_for_status()
    no_channel_id = resp.json()["participant_id"]

    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990009999",
            "participant_full_name": "С Привязкой VK",
            "quantity": 1,
        },
        headers=headers,
    )
    resp.raise_for_status()
    vk_participant_id = resp.json()["participant_id"]

    db = Database(get_settings())
    with db.session() as session:
        session.add(
            ChannelBinding(
                participant_id=vk_participant_id,
                channel=ChannelType.VK,
                external_user_id="vk-12345",
            )
        )
    db.engine.dispose()

    resp = api_client.get("/api/participants", params={"channel": "vk"}, headers=headers)
    body = resp.json()
    ids = {p["id"] for p in body["items"]}
    assert vk_participant_id in ids
    assert no_channel_id not in ids

    resp = api_client.get("/api/participants", params={"channel": "telegram"}, headers=headers)
    ids = {p["id"] for p in resp.json()["items"]}
    assert vk_participant_id not in ids
    assert no_channel_id not in ids


def test_tickets_filter_by_payment_id(api_client: TestClient) -> None:
    """Мониторинг продаж (панель) достаёт номерки конкретного онлайн-платежа
    через /api/tickets?payment_id=... — раньше такого фильтра не было, только
    manual_registration_id для ручных регистраций. Платёж создаётся и
    финализируется напрямую через payment_service (у панели нет HTTP-пути для
    онлайн-оплаты — см. _seed_payments выше), тот же паттерн, что и в
    tests/unit/test_payments.py."""
    token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(token)
    giveaway_id = _create_open_giveaway(api_client, headers, prefix="TPI")

    reg_resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79990001234",
            "participant_full_name": "Онлайн Плательщик",
            "quantity": 1,
        },
        headers=headers,
    )
    reg_resp.raise_for_status()
    participant_id = reg_resp.json()["participant_id"]

    provider = RequisitesQrProvider(
        recipient_name="ИП Тест",
        recipient_inn="770101001770",
        recipient_kpp="",
        personal_acc="40802810000000000001",
        bank_name="Тестбанк",
        bic="044525225",
        corresp_acc="30101810000000000225",
        vat_rate_percent=0,
    )
    db = Database(get_settings())
    outcome = payment_service.create_payment_safe(
        db,
        provider,
        giveaway_id=giveaway_id,
        participant_id=participant_id,
        participant_phone="79990001234",
        quantity=2,
    )
    assert outcome.ok
    finalize = payment_service.finalize_payment(
        db, order_id=outcome.order_id, new_status=PaymentStatus.SUCCEEDED
    )
    assert finalize.applied
    db.engine.dispose()

    resp = api_client.get(
        "/api/tickets", params={"payment_id": outcome.payment_id}, headers=headers
    )
    body = resp.json()
    assert body["total"] == 2
    assert all(t["source"] == "online" for t in body["items"])

    resp = api_client.get(
        "/api/tickets", params={"payment_id": (outcome.payment_id or 0) + 999999}, headers=headers
    )
    assert resp.json()["total"] == 0
