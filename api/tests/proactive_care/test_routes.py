from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    Message,
    MessageRole,
    MessageStatus,
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    PushDelivery,
    PushDeliveryStatus,
    PushInstallation,
    PushPermissionState,
    PushPlatform,
)
from antang_api.security import decode_access_token


async def register(client: AsyncClient, prefix: str) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "consent_accepted": True,
            "username": f"{prefix[:19]}_{uuid4().hex[:12]}",
            "password": "correct-password",
        },
    )
    assert response.status_code == 201
    return response.json()


def auth_headers(auth: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['access_token']}"}


async def test_installation_put_registers_the_current_login_session(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    auth = await register(client, "push_installation")
    installation_id = uuid4()

    response = await client.put(
        f"/api/v1/proactive-care/installations/{installation_id}",
        headers=auth_headers(auth),
        json={
            "expo_push_token": "ExponentPushToken[route-test-token]",
            "permission": "granted",
            "platform": "android",
            "app_version": "1.0.0",
        },
    )

    assert response.status_code == 204
    installation = await db_session.get(PushInstallation, installation_id)
    assert installation is not None
    assert installation.login_session_id == decode_access_token(
        auth["access_token"]
    )[1]
    assert installation.permission == PushPermissionState.GRANTED
    assert installation.platform == PushPlatform.ANDROID
    assert installation.registration_revision == 1
    assert installation.disabled_at is None


async def test_care_settings_get_returns_the_registered_users_defaults(
    client: AsyncClient,
) -> None:
    auth = await register(client, "care_settings_get")

    response = await client.get(
        "/api/v1/proactive-care/settings",
        headers=auth_headers(auth),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["routine_cadence"] == "disabled"
    assert payload["plan_follow_up_enabled"] is True
    assert payload["health_events_enabled"] is False
    assert payload["timezone"] == "Asia/Shanghai"
    assert payload["quiet_hours_start"] == "22:00"
    assert payload["quiet_hours_end"] == "08:00"
    assert payload["health_notification_preview_enabled"] is False
    # 没有做过量表：上限还未知，本周 0 次；关着但不是我们替它停的。
    assert payload["weekly_contact_count"] == 0
    assert payload["weekly_contact_limit"] is None
    assert payload["auto_paused"] is False
    assert payload["auto_downgraded_at"] is None


async def test_care_settings_put_replaces_every_setting_atomically(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    auth = await register(client, "care_settings_put")
    body = {
        "routine_cadence": "every_3_days",
        "plan_follow_up_enabled": False,
        "health_events_enabled": True,
        "timezone": "America/New_York",
        "quiet_hours_start": "21:30",
        "quiet_hours_end": "07:15",
        "health_notification_preview_enabled": True,
    }

    response = await client.put(
        "/api/v1/proactive-care/settings",
        headers=auth_headers(auth),
        json=body,
    )

    assert response.status_code == 200
    assert {key: response.json()[key] for key in body} == body
    persisted = await client.get(
        "/api/v1/proactive-care/settings",
        headers=auth_headers(auth),
    )
    assert persisted.status_code == 200
    assert {key: persisted.json()[key] for key in body} == body

    user_id = UUID(auth["user"]["id"])
    scheduled = await db_session.scalar(
        select(ProactiveCareTask).where(
            ProactiveCareTask.user_id == user_id,
            ProactiveCareTask.kind == ProactiveCareTaskKind.ROUTINE_CHECK_IN,
            ProactiveCareTask.status == ProactiveCareTaskStatus.SCHEDULED,
        )
    )
    assert scheduled is not None
    assert scheduled.due_at > datetime.now(timezone.utc) + timedelta(days=2)
    assert scheduled.expires_at == scheduled.due_at + timedelta(hours=12)

    disabled = {**body, "routine_cadence": "disabled"}
    assert (
        await client.put(
            "/api/v1/proactive-care/settings",
            headers=auth_headers(auth),
            json=disabled,
        )
    ).status_code == 200
    await db_session.refresh(scheduled)
    assert scheduled.status == ProactiveCareTaskStatus.CANCELLED
    assert scheduled.outcome_reason == "routine_disabled"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timezone", "GMT+08:00"),
        ("quiet_hours_start", "9:00"),
        ("quiet_hours_start", "24:00"),
        ("quiet_hours_end", "08:00:00"),
    ],
)
async def test_care_settings_put_rejects_non_iana_timezone_and_non_hhmm_times(
    client: AsyncClient,
    field: str,
    value: str,
) -> None:
    auth = await register(client, f"care_invalid_{field}")
    body = {
        "routine_cadence": "weekly",
        "plan_follow_up_enabled": False,
        "health_events_enabled": True,
        "timezone": "Asia/Shanghai",
        "quiet_hours_start": "21:30",
        "quiet_hours_end": "07:15",
        "health_notification_preview_enabled": True,
    }
    body[field] = value

    response = await client.put(
        "/api/v1/proactive-care/settings",
        headers=auth_headers(auth),
        json=body,
    )

    assert response.status_code == 422
    unchanged = await client.get(
        "/api/v1/proactive-care/settings",
        headers=auth_headers(auth),
    )
    assert unchanged.json()["routine_cadence"] == "disabled"


async def test_care_settings_put_requires_the_complete_document(
    client: AsyncClient,
) -> None:
    auth = await register(client, "care_settings_complete")

    response = await client.put(
        "/api/v1/proactive-care/settings",
        headers=auth_headers(auth),
        json={
            "routine_cadence": "daily",
            "plan_follow_up_enabled": True,
            "health_events_enabled": False,
            "timezone": "Asia/Shanghai",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "08:00",
        },
    )

    assert response.status_code == 422


async def test_care_settings_missing_row_fails_instead_of_recreating_it(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    auth = await register(client, "care_settings_missing")
    user_id = UUID(auth["user"]["id"])
    settings = await db_session.get(ProactiveCareSettings, user_id)
    assert settings is not None
    await db_session.delete(settings)
    await db_session.commit()

    get_response = await client.get(
        "/api/v1/proactive-care/settings",
        headers=auth_headers(auth),
    )
    put_response = await client.put(
        "/api/v1/proactive-care/settings",
        headers=auth_headers(auth),
        json={
            "routine_cadence": "daily",
            "plan_follow_up_enabled": True,
            "health_events_enabled": False,
            "timezone": "Asia/Shanghai",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "08:00",
            "health_notification_preview_enabled": False,
        },
    )

    assert get_response.status_code == 500
    assert get_response.json() == {"detail": "主动关怀设置不存在"}
    assert put_response.status_code == 500
    assert put_response.json() == {"detail": "主动关怀设置不存在"}


async def test_installation_rebind_invalidates_the_previous_accounts_pending_push(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    first_auth = await register(client, "push_rebind_old")
    installation_id = uuid4()
    token = "ExponentPushToken[route-rebind-token]"
    assert (
        await client.put(
            f"/api/v1/proactive-care/installations/{installation_id}",
            headers=auth_headers(first_auth),
            json={
                "expo_push_token": token,
                "permission": "granted",
                "platform": "android",
                "app_version": "1.0.0",
            },
        )
    ).status_code == 204

    now = datetime.now(timezone.utc)
    old_message = Message(
        user_id=UUID(first_auth["user"]["id"]),
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="旧账号的关怀消息",
        completed_at=now,
    )
    db_session.add(old_message)
    await db_session.flush()
    delivery = PushDelivery(
        message_id=old_message.id,
        installation_id=installation_id,
        installation_revision=1,
        show_message_preview=True,
        status=PushDeliveryStatus.SENDING,
        next_attempt_at=now,
        expires_at=now + timedelta(hours=24),
        lease_token=uuid4(),
        lease_expires_at=now + timedelta(minutes=1),
    )
    db_session.add(delivery)
    await db_session.commit()

    second_auth = await register(client, "push_rebind_new")
    response = await client.put(
        f"/api/v1/proactive-care/installations/{installation_id}",
        headers=auth_headers(second_auth),
        json={
            "expo_push_token": token,
            "permission": "granted",
            "platform": "android",
            "app_version": "1.0.0",
        },
    )

    assert response.status_code == 204
    await db_session.refresh(delivery)
    installation = await db_session.get(PushInstallation, installation_id)
    assert installation is not None
    assert installation.login_session_id == decode_access_token(
        second_auth["access_token"]
    )[1]
    assert installation.registration_revision == 2
    assert delivery.status == PushDeliveryStatus.FAILED
    assert delivery.provider_error_code == "installation_rebound"
    assert delivery.lease_token is None
    assert delivery.lease_expires_at is None


async def test_installation_revision_tracks_token_change_and_reactivation(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    auth = await register(client, "push_revision")
    installation_id = uuid4()
    url = f"/api/v1/proactive-care/installations/{installation_id}"
    first_registration = {
        "expo_push_token": "ExponentPushToken[revision-one]",
        "permission": "granted",
        "platform": "android",
        "app_version": "1.0.0",
    }

    assert (
        await client.put(url, headers=auth_headers(auth), json=first_registration)
    ).status_code == 204
    assert (
        await client.put(url, headers=auth_headers(auth), json=first_registration)
    ).status_code == 204
    installation = await db_session.get(PushInstallation, installation_id)
    assert installation is not None
    assert installation.registration_revision == 1

    changed_token = {
        **first_registration,
        "expo_push_token": "ExponentPushToken[revision-two]",
    }
    assert (
        await client.put(url, headers=auth_headers(auth), json=changed_token)
    ).status_code == 204
    await db_session.refresh(installation)
    assert installation.registration_revision == 2

    now = datetime.now(timezone.utc)
    message = Message(
        user_id=UUID(auth["user"]["id"]),
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="不应在关闭通知后继续发送",
        completed_at=now,
    )
    db_session.add(message)
    await db_session.flush()
    delivery = PushDelivery(
        message_id=message.id,
        installation_id=installation_id,
        installation_revision=2,
        show_message_preview=True,
        status=PushDeliveryStatus.PENDING,
        next_attempt_at=now,
        expires_at=now + timedelta(hours=24),
    )
    db_session.add(delivery)
    await db_session.commit()

    assert (
        await client.delete(url, headers=auth_headers(auth))
    ).status_code == 204
    assert (
        await client.delete(url, headers=auth_headers(auth))
    ).status_code == 204
    await db_session.refresh(installation)
    assert installation.disabled_at is not None
    assert installation.disabled_reason == "client_disabled"
    assert installation.registration_revision == 3
    await db_session.refresh(delivery)
    assert delivery.status == PushDeliveryStatus.FAILED
    assert delivery.provider_error_code == "installation_changed"

    assert (
        await client.put(url, headers=auth_headers(auth), json=changed_token)
    ).status_code == 204
    await db_session.refresh(installation)
    assert installation.disabled_at is None
    assert installation.disabled_reason is None
    assert installation.registration_revision == 4


async def test_delivery_opened_is_idempotent_and_private_to_its_installation(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    auth = await register(client, "push_opened")
    installation_id = uuid4()
    assert (
        await client.put(
            f"/api/v1/proactive-care/installations/{installation_id}",
            headers=auth_headers(auth),
            json={
                "expo_push_token": "ExponentPushToken[opened-token]",
                "permission": "granted",
                "platform": "android",
                "app_version": "1.0.0",
            },
        )
    ).status_code == 204

    now = datetime.now(timezone.utc)
    message = Message(
        user_id=UUID(auth["user"]["id"]),
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETED,
        content="来看看今天的关怀消息",
        completed_at=now,
    )
    db_session.add(message)
    await db_session.flush()
    delivery = PushDelivery(
        message_id=message.id,
        installation_id=installation_id,
        installation_revision=1,
        show_message_preview=True,
        status=PushDeliveryStatus.RECEIPT_ACCEPTED,
        expires_at=now + timedelta(hours=24),
    )
    db_session.add(delivery)
    await db_session.commit()
    url = f"/api/v1/proactive-care/deliveries/{delivery.id}/opened"

    first = await client.post(url, headers=auth_headers(auth))
    assert first.status_code == 204
    await db_session.refresh(delivery)
    first_opened_at = delivery.opened_at
    assert first_opened_at is not None

    second = await client.post(url, headers=auth_headers(auth))
    assert second.status_code == 204
    await db_session.refresh(delivery)
    assert delivery.opened_at == first_opened_at

    other_auth = await register(client, "push_opened_other")
    forbidden = await client.post(url, headers=auth_headers(other_auth))
    assert forbidden.status_code == 404


async def test_message_window_returns_ordered_context_without_cross_user_access(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    auth = await register(client, "message_window")
    user_id = UUID(auth["user"]["id"])
    messages: list[Message] = []
    for index, content in enumerate(["之前一", "之前二", "目标", "之后一", "之后二"]):
        message = Message(
            client_message_id=uuid4() if index % 2 == 0 else None,
            user_id=user_id,
            role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content=content,
            completed_at=datetime.now(timezone.utc),
        )
        db_session.add(message)
        await db_session.flush()
        messages.append(message)
    await db_session.commit()

    response = await client.get(
        f"/api/v1/chat/messages/{messages[2].id}/window",
        headers=auth_headers(auth),
    )

    assert response.status_code == 200
    assert response.json()["next_before"] is None
    assert [item["content"] for item in response.json()["messages"]] == [
        "之前一",
        "之前二",
        "目标",
        "之后一",
        "之后二",
    ]

    other_auth = await register(client, "message_window_other")
    forbidden = await client.get(
        f"/api/v1/chat/messages/{messages[2].id}/window",
        headers=auth_headers(other_auth),
    )
    assert forbidden.status_code == 404
