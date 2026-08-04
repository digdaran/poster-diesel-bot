"""API аннулирования уже завершённой покупки (DECISIONS.md/DECISIONS_LOG.md
№69, п.20.1 ТЗ): доступно только Super Admin, номерки возвращаются в оборот,
причина обязательна и попадает в AuditLog."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.db import Database
from app.models.base import utcnow
from app.models.enums import ManualRegistrationStatus, PaymentProviderType, PaymentStatus
from app.models.giveaway import Giveaway
from app.models.manual_registration import ManualRegistration
from app.models.payment import Payment
from app.models.ticket import Ticket
from app.models.ticket_pool import TicketPool
from app.repositories import ticket_pool_repo as repo
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.integration.conftest import auth_headers, login
from tests.integration.test_api_permissions import create_panel_user


def _create_open_giveaway(
    api_client: TestClient, headers: dict[str, str], *, max_tickets: int = 10, prefix: str = "RFD"
) -> int:
    resp = api_client.post(
        "/api/giveaways",
        json={"name": "Refund", "prefix": prefix, "ticket_price": 1000, "max_tickets": max_tickets},
        headers=headers,
    )
    resp.raise_for_status()
    giveaway_id: int = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers).raise_for_status()
    return giveaway_id


def _create_participant_via_manual_registration(
    api_client: TestClient, headers: dict[str, str], giveaway_id: int, phone: str
) -> int:
    """Нет отдельного эндпоинта создания участника — как и в
    test_api_payment_receipts.py, используем побочный эффект ручной регистрации
    (сама регистрация остаётся PENDING/неиспользуемой, важен только участник)."""
    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": phone,
            "participant_full_name": "Тест Тестов",
            "quantity": 1,
        },
        headers=headers,
    )
    resp.raise_for_status()
    return int(resp.json()["participant_id"])


def _seed_succeeded_payment(giveaway_id: int, participant_id: int, *, quantity: int = 1) -> int:
    """Прямая вставка через сервисный слой (тот же приём, что и
    _seed_payment_with_receipt в test_api_payment_receipts.py) — резервирует и
    сразу выдаёт номерки, минуя провайдера/бота."""
    db = Database(get_settings())
    with db.immediate_session() as session:
        payment = Payment(
            order_id=f"refund-order-{giveaway_id}-{participant_id}",
            participant_id=participant_id,
            giveaway_id=giveaway_id,
            provider=PaymentProviderType.REQUISITES_QR,
            amount=quantity * 1000,
            quantity=quantity,
            payment_number=1,
            status=PaymentStatus.SUCCEEDED,
            confirmed_at=utcnow(),
        )
        session.add(payment)
        session.flush()
        payment_id = payment.id

        reserved = repo.reserve_tickets(
            session,
            giveaway_id=giveaway_id,
            quantity=quantity,
            participant_id=participant_id,
            payment_id=payment_id,
            reserved_until=utcnow(),
        )
        assert reserved.ok
        issued_rows = repo.issue_reserved(session, payment_id=payment_id, issued_at=utcnow())
        giveaway = session.get(Giveaway, giveaway_id)
        assert giveaway is not None
        for row in issued_rows:
            session.add(
                Ticket(
                    giveaway_id=giveaway_id,
                    pool_id=row.id,
                    number=row.number,
                    full_code=giveaway.format_code(row.number),
                    participant_id=participant_id,
                    source="online",
                    payment_id=payment_id,
                )
            )
        session.flush()
    db.engine.dispose()
    return payment_id


def test_refund_payment_requires_super_admin(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    admin_headers = auth_headers(admin_token)
    create_panel_user(
        api_client, admin_token, "manager-refund", "manager-strong-pass", "administrator"
    )
    manager_token = login(api_client, "manager-refund", "manager-strong-pass")

    giveaway_id = _create_open_giveaway(api_client, admin_headers, prefix="RFD1")
    participant_id = _create_participant_via_manual_registration(
        api_client, admin_headers, giveaway_id, "79993330001"
    )
    payment_id = _seed_succeeded_payment(giveaway_id, participant_id)

    resp = api_client.post(
        f"/api/payments/{payment_id}/refund",
        json={"reason": "тест прав доступа"},
        headers=auth_headers(manager_token),
    )
    assert resp.status_code == 403


def test_refund_payment_full_flow_returns_tickets_to_pool(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    admin_headers = auth_headers(admin_token)

    giveaway_id = _create_open_giveaway(api_client, admin_headers, max_tickets=5, prefix="RFD2")
    participant_id = _create_participant_via_manual_registration(
        api_client, admin_headers, giveaway_id, "79993330002"
    )
    payment_id = _seed_succeeded_payment(giveaway_id, participant_id, quantity=2)

    db_before = Database(get_settings())
    with db_before.session() as session:
        free_before = len(
            list(
                session.execute(
                    select(TicketPool).where(
                        TicketPool.giveaway_id == giveaway_id, TicketPool.status == "free"
                    )
                ).scalars()
            )
        )
    db_before.engine.dispose()

    resp = api_client.post(
        f"/api/payments/{payment_id}/refund",
        json={"reason": "Покупатель попросил вернуть деньги"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "REFUNDED"
    assert body["refund_reason"] == "Покупатель попросил вернуть деньги"
    assert body["refunded_by_login"] == "admin"

    db = Database(get_settings())
    with db.session() as session:
        payment = session.execute(select(Payment).where(Payment.id == payment_id)).scalar_one()
        assert payment.status == PaymentStatus.REFUNDED
        pool_rows = list(
            session.execute(
                select(TicketPool).where(
                    TicketPool.giveaway_id == giveaway_id, TicketPool.payment_id == payment_id
                )
            ).scalars()
        )
        assert pool_rows == []  # ссылка на платёж снята при возврате в free
        free_rows = list(
            session.execute(
                select(TicketPool).where(
                    TicketPool.giveaway_id == giveaway_id, TicketPool.status == "free"
                )
            ).scalars()
        )
        assert len(free_rows) == free_before + 2  # оба номерка платежа вернулись в оборот
    db.engine.dispose()

    # Повторная попытка — уже не SUCCEEDED, значит 409.
    resp2 = api_client.post(
        f"/api/payments/{payment_id}/refund",
        json={"reason": "повтор"},
        headers=admin_headers,
    )
    assert resp2.status_code == 409


def test_refund_payment_rejects_empty_reason(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    admin_headers = auth_headers(admin_token)
    giveaway_id = _create_open_giveaway(api_client, admin_headers, prefix="RFD3")
    participant_id = _create_participant_via_manual_registration(
        api_client, admin_headers, giveaway_id, "79993330003"
    )
    payment_id = _seed_succeeded_payment(giveaway_id, participant_id)

    resp = api_client.post(
        f"/api/payments/{payment_id}/refund", json={"reason": "   "}, headers=admin_headers
    )
    assert resp.status_code == 422


def test_refund_payment_writes_audit_log(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    admin_headers = auth_headers(admin_token)
    giveaway_id = _create_open_giveaway(api_client, admin_headers, prefix="RFD4")
    participant_id = _create_participant_via_manual_registration(
        api_client, admin_headers, giveaway_id, "79993330004"
    )
    payment_id = _seed_succeeded_payment(giveaway_id, participant_id)

    api_client.post(
        f"/api/payments/{payment_id}/refund", json={"reason": "аудит-тест"}, headers=admin_headers
    ).raise_for_status()

    resp = api_client.get("/api/audit", params={"action": "payment_refund"}, headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(
        i["entity_id"] == payment_id and i["details"]["reason"] == "аудит-тест" for i in items
    )


def test_refund_manual_registration_requires_super_admin_and_only_confirmed(
    api_client: TestClient,
) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    admin_headers = auth_headers(admin_token)
    create_panel_user(
        api_client, admin_token, "manager-refund-mr", "manager-strong-pass", "administrator"
    )
    manager_token = login(api_client, "manager-refund-mr", "manager-strong-pass")

    giveaway_id = _create_open_giveaway(api_client, admin_headers, prefix="RFD5")
    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": "79993330005",
            "participant_full_name": "Тест Ручной",
            "quantity": 2,
        },
        headers=admin_headers,
    )
    resp.raise_for_status()
    registration_id = resp.json()["id"]

    # Administrator — 403, даже до подтверждения.
    forbidden = api_client.post(
        f"/api/manual-registrations/{registration_id}/refund",
        json={"reason": "тест"},
        headers=auth_headers(manager_token),
    )
    assert forbidden.status_code == 403

    # Super Admin, но регистрация ещё PENDING — 409.
    too_early = api_client.post(
        f"/api/manual-registrations/{registration_id}/refund",
        json={"reason": "тест"},
        headers=admin_headers,
    )
    assert too_early.status_code == 409

    api_client.post(
        f"/api/manual-registrations/{registration_id}/confirm", headers=admin_headers
    ).raise_for_status()

    ok = api_client.post(
        f"/api/manual-registrations/{registration_id}/refund",
        json={"reason": "Ошибочная регистрация"},
        headers=admin_headers,
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["status"] == "REFUNDED"
    assert body["refunded_by_login"] == "admin"

    db = Database(get_settings())
    with db.session() as session:
        registration = session.get(ManualRegistration, registration_id)
        assert registration is not None
        assert registration.status == ManualRegistrationStatus.REFUNDED
        free_rows = list(
            session.execute(
                select(TicketPool).where(
                    TicketPool.giveaway_id == giveaway_id, TicketPool.status == "free"
                )
            ).scalars()
        )
        assert len(free_rows) == 10  # весь пул снова свободен
    db.engine.dispose()
