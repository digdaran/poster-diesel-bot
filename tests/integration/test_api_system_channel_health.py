"""`GET /api/system/channel-health` — данные для плашки «Telegram недоступен»
(DECISIONS_LOG.md №94)."""

from __future__ import annotations

from app.models.base import utcnow
from app.models.enums import ChannelType, PendingDeliveryKind, PendingDeliveryStatus
from app.models.pending_channel_delivery import PendingChannelDelivery
from app.services.channel_health_service import ChannelHealth
from fastapi.testclient import TestClient

from tests.integration.conftest import auth_headers, login
from tests.integration.test_api_permissions import create_panel_user

URL = "/api/system/channel-health"


def test_requires_auth(api_client: TestClient) -> None:
    assert api_client.get(URL).status_code == 401


def test_telegram_not_configured(api_client: TestClient) -> None:
    token = login(api_client, "admin", "admin-strong-pass-123")
    resp = api_client.get(URL, headers=auth_headers(token))
    assert resp.status_code == 200
    assert resp.json() == {"telegram": None, "pending_telegram_deliveries": 0}


def test_operator_sees_failure_and_pending_count(api_client: TestClient) -> None:
    admin_token = login(api_client, "admin", "admin-strong-pass-123")
    create_panel_user(api_client, admin_token, "operator1", "operator-strong-pass", "operator")
    op_token = login(api_client, "operator1", "operator-strong-pass")

    app = api_client.app
    health = ChannelHealth()
    health.record_failure(utcnow(), "ProxyConnectionError")
    app.state.telegram_health = health  # type: ignore[attr-defined]

    with app.state.db.session() as session:  # type: ignore[attr-defined]
        for channel, status in [
            (ChannelType.TELEGRAM, PendingDeliveryStatus.PENDING),
            (ChannelType.TELEGRAM, PendingDeliveryStatus.PENDING),
            (ChannelType.TELEGRAM, PendingDeliveryStatus.SENT),
            (ChannelType.VK, PendingDeliveryStatus.PENDING),
        ]:
            session.add(
                PendingChannelDelivery(
                    channel=channel,
                    external_user_id="1",
                    kind=PendingDeliveryKind.TEXT_MESSAGE,
                    payload={"text": "x"},
                    status=status,
                )
            )
        session.commit()

    resp = api_client.get(URL, headers=auth_headers(op_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["telegram"]["ok"] is False
    assert body["telegram"]["failing_since"] is not None
    assert body["pending_telegram_deliveries"] == 2
