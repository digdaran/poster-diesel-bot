"""Экспорт «Участников» в CSV/XLSX (`GET /api/participants?export=...`):
доступ только Administrator/Super Admin, экспортируются все строки, подходящие
под текущий фильтр (не только текущая страница), с номерками, сгруппированными
по коллекции (розыгрышу)."""

from __future__ import annotations

import csv
import io

import openpyxl
from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login


def _create_open_giveaway(
    api_client: TestClient, headers: dict[str, str], *, name: str, prefix: str
) -> int:
    resp = api_client.post(
        "/api/giveaways",
        json={"name": name, "prefix": prefix, "ticket_price": 1000, "max_tickets": 30},
        headers=headers,
    )
    resp.raise_for_status()
    giveaway_id: int = resp.json()["id"]
    api_client.post(f"/api/giveaways/{giveaway_id}/open", headers=headers).raise_for_status()
    return giveaway_id


def _register_and_confirm(
    api_client: TestClient,
    headers: dict[str, str],
    *,
    giveaway_id: int,
    phone: str,
    full_name: str,
    quantity: int,
) -> int:
    resp = api_client.post(
        "/api/manual-registrations",
        json={
            "giveaway_id": giveaway_id,
            "participant_phone": phone,
            "participant_full_name": full_name,
            "quantity": quantity,
        },
        headers=headers,
    )
    resp.raise_for_status()
    body = resp.json()
    api_client.post(
        f"/api/manual-registrations/{body['id']}/confirm", headers=headers
    ).raise_for_status()
    return int(body["participant_id"])


def _expected_ticket_group(
    api_client: TestClient, headers: dict[str, str], *, giveaway_id: int, participant_id: int
) -> str:
    """Номера выдаются из перемешанного пула (см. ARCHITECTURE.md §3 —
    `shuffle_order`, не последовательно), поэтому ожидаемую строку группы
    строим из фактически выданных номеров, а не подставляем 000001, 000002…"""
    resp = api_client.get(
        "/api/tickets",
        params={"giveaway_id": giveaway_id, "participant_id": participant_id},
        headers=headers,
    )
    resp.raise_for_status()
    numbers = sorted(t["number"] for t in resp.json()["items"])
    return ", ".join(f"{n:06d}" for n in numbers)


def _rows_from_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def _rows_from_xlsx(content: bytes) -> list[dict[str, object]]:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb.active
    assert ws is not None
    values = list(ws.iter_rows(values_only=True))
    headers = [str(h) for h in values[0]]
    return [dict(zip(headers, row, strict=True)) for row in values[1:]]


def test_participant_export_forbidden_for_operator(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    admin_headers = auth_headers(admin_token)
    api_client.post(
        "/api/panel-users",
        json={"login": "op_pexport", "password": "op-export-strong-pass", "role": "operator"},
        headers=admin_headers,
    ).raise_for_status()
    op_token = login(api_client, "op_pexport", "op-export-strong-pass")
    op_headers = auth_headers(op_token)

    resp = api_client.get("/api/participants?export=csv", headers=op_headers)
    assert resp.status_code == 403

    resp = api_client.get("/api/participants?export=xlsx", headers=op_headers)
    assert resp.status_code == 403

    # Список без экспорта операторам доступен (VIEW_PARTICIPANTS есть у всех ролей).
    resp = api_client.get("/api/participants", headers=op_headers)
    assert resp.status_code == 200


def test_participant_export_allowed_for_administrator(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(admin_token)
    api_client.post(
        "/api/panel-users",
        json={
            "login": "mgr_pexport",
            "password": "mgr-export-strong-pass",
            "role": "administrator",
        },
        headers=headers,
    ).raise_for_status()
    manager_token = login(api_client, "mgr_pexport", "mgr-export-strong-pass")
    manager_headers = auth_headers(manager_token)

    resp = api_client.get("/api/participants?export=csv", headers=manager_headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    resp = api_client.get("/api/participants?export=xlsx", headers=manager_headers)
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"  # xlsx — zip-контейнер


def test_participant_export_groups_tickets_by_collection_and_respects_filter(
    api_client: TestClient,
) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(admin_token)

    giveaway_a = _create_open_giveaway(api_client, headers, name="Giveaway A", prefix="GAX")
    giveaway_b = _create_open_giveaway(api_client, headers, name="Giveaway B", prefix="GBX")

    target_phone = "79995551111"
    target_id = _register_and_confirm(
        api_client,
        headers,
        giveaway_id=giveaway_a,
        phone=target_phone,
        full_name="Целевой Участник",
        quantity=2,
    )
    # Тот же телефон = тот же участник (идентификация по номеру телефона) —
    # второй заказ в другой коллекции должен попасть в тот же ряд экспорта.
    _register_and_confirm(
        api_client,
        headers,
        giveaway_id=giveaway_b,
        phone=target_phone,
        full_name="Целевой Участник",
        quantity=1,
    )

    other_phone = "79995552222"
    other_id = _register_and_confirm(
        api_client,
        headers,
        giveaway_id=giveaway_a,
        phone=other_phone,
        full_name="Другой Участник",
        quantity=1,
    )

    target_group_a = _expected_ticket_group(
        api_client, headers, giveaway_id=giveaway_a, participant_id=target_id
    )
    target_group_b = _expected_ticket_group(
        api_client, headers, giveaway_id=giveaway_b, participant_id=target_id
    )
    other_group_a = _expected_ticket_group(
        api_client, headers, giveaway_id=giveaway_a, participant_id=other_id
    )
    expected_target_tickets = f"GAX: {target_group_a}; GBX: {target_group_b}"
    expected_other_tickets = f"GAX: {other_group_a}"

    # Экспорт с фильтром по телефону — должен вернуть только целевого участника,
    # как и обычный список с тем же q.
    resp = api_client.get(
        "/api/participants", params={"q": target_phone, "export": "csv"}, headers=headers
    )
    assert resp.status_code == 200
    rows = _rows_from_csv(resp.content)
    assert len(rows) == 1
    row = rows[0]
    assert row["phone"] == target_phone
    assert row["total_tickets"] == "3"
    assert row["tickets"] == expected_target_tickets
    assert row["giveaways"] == "Giveaway A, Giveaway B"

    # То же самое для XLSX, но без фильтра — должны прийти обе строки, экспорт
    # не ограничен пагинацией (в отличие от обычного списка).
    resp = api_client.get(
        "/api/participants",
        params={"export": "xlsx"},
        headers=headers,
    )
    assert resp.status_code == 200
    xlsx_rows = _rows_from_xlsx(resp.content)
    assert len(xlsx_rows) == 2
    by_phone = {r["phone"]: r for r in xlsx_rows}
    assert by_phone[target_phone]["tickets"] == expected_target_tickets
    assert by_phone[target_phone]["giveaways"] == "Giveaway A, Giveaway B"
    assert by_phone[other_phone]["tickets"] == expected_other_tickets
    assert by_phone[other_phone]["giveaways"] == "Giveaway A"


def test_participant_export_ignores_pagination(api_client: TestClient) -> None:
    """Обычный список урезается `page_size`, экспорт — нет: должны выгрузиться
    все подходящие под фильтр участники, даже если их больше `page_size`."""
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    headers = auth_headers(admin_token)
    giveaway_id = _create_open_giveaway(api_client, headers, name="Giveaway P", prefix="GPX")

    phones = [f"7999555{n:04d}" for n in range(11)]  # больше, чем page_size=10
    for phone in phones:
        _register_and_confirm(
            api_client,
            headers,
            giveaway_id=giveaway_id,
            phone=phone,
            full_name="Пагинация Тест",
            quantity=1,
        )

    resp = api_client.get("/api/participants", params={"page_size": 10, "page": 1}, headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 10  # обычный список урезан page_size

    resp = api_client.get(
        "/api/participants", params={"page_size": 10, "export": "csv"}, headers=headers
    )
    assert resp.status_code == 200
    rows = _rows_from_csv(resp.content)
    assert {r["phone"] for r in rows} >= set(phones)
