import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from antang_api.agents.proactive_care import PROACTIVE_CARE_AGENT_NAME
from antang_api.database import session_factory
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    LoginSession,
    Message,
    MessageRole,
    MessageStatus,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    ProactiveCareTaskStatus,
    PushDelivery,
    PushDeliveryStatus,
    PushInstallation,
    PushPermissionState,
    PushPlatform,
    WearableImport,
)
from antang_api.proactive_care.push import (
    ExpoPushClient,
    ExpoPushRequestError,
    PushDeliveryWorker,
)
from antang_api.settings import settings


class FakeExpoClient:
    def __init__(
        self,
        *,
        send_result: tuple[str, str | None] = ("ok", "ticket-1"),
        receipt_result: tuple[str, str | None] = ("ok", None),
        wait_before_send: asyncio.Event | None = None,
    ) -> None:
        self.send_result = send_result
        self.receipt_result = receipt_result
        self.wait_before_send = wait_before_send
        self.send_started = asyncio.Event()
        self.sent: list[dict[str, object]] = []
        self.receipts: list[str] = []

    async def send(self, message: dict[str, object]) -> tuple[str, str | None]:
        self.sent.append(message)
        self.send_started.set()
        if self.wait_before_send is not None:
            await self.wait_before_send.wait()
        return self.send_result

    async def get_receipt(self, ticket_id: str) -> tuple[str, str | None]:
        self.receipts.append(ticket_id)
        return self.receipt_result


async def create_delivery(
    user_id: UUID,
    now: datetime,
    *,
    kind: ProactiveCareTaskKind = ProactiveCareTaskKind.ROUTINE_CHECK_IN,
    installation_revision: int = 1,
    show_message_preview: bool = True,
) -> tuple[UUID, UUID, UUID]:
    async with session_factory() as session:
        wearable_import: WearableImport | None = None
        if kind == ProactiveCareTaskKind.HEALTH_EVENT:
            wearable_import = WearableImport(
                user_id=user_id,
                client_sync_id=uuid4(),
                request_hash=uuid4().hex + uuid4().hex,
                records_created=1,
                records_updated=0,
                records_unchanged=0,
                records_deleted=0,
            )
            session.add(wearable_import)
            await session.flush()
        login_session = LoginSession(
            user_id=user_id,
            refresh_token_hash=uuid4().hex + uuid4().hex,
            expires_at=now + timedelta(days=30),
        )
        message = Message(
            user_id=user_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="今天感觉怎么样？",
            completed_at=now,
        )
        task = ProactiveCareTask(
            user_id=user_id,
            kind=kind,
            status=ProactiveCareTaskStatus.COMPLETED,
            due_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
            wearable_import_id=(
                wearable_import.id if wearable_import is not None else None
            ),
            finished_at=now,
        )
        session.add_all([login_session, message, task])
        await session.flush()
        installation = PushInstallation(
            id=uuid4(),
            login_session_id=login_session.id,
            expo_push_token="ExponentPushToken[worker-device]",
            registration_revision=installation_revision,
            permission=PushPermissionState.GRANTED,
            platform=PushPlatform.ANDROID,
            app_version="1.0.0",
            last_seen_at=now,
        )
        session.add(installation)
        await session.flush()
        run = AgentRun(
            user_id=user_id,
            trigger_message_id=None,
            trigger_care_task_id=task.id,
            result_message_id=message.id,
            parent_run_id=None,
            agent_name=PROACTIVE_CARE_AGENT_NAME,
            model="test-model",
            status=AgentRunStatus.COMPLETED,
            finished_at=now,
        )
        delivery = PushDelivery(
            message_id=message.id,
            installation_id=installation.id,
            installation_revision=installation_revision,
            show_message_preview=show_message_preview,
            status=PushDeliveryStatus.PENDING,
            next_attempt_at=now,
            expires_at=task.expires_at,
        )
        session.add_all([run, delivery])
        await session.commit()
        return delivery.id, installation.id, message.id


async def test_expo_client_sends_one_payload_and_reads_its_ticket() -> None:
    seen_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(
            200,
            json={"data": [{"status": "ok", "id": "ticket-1"}]},
        )

    async with httpx.AsyncClient(
        base_url="https://exp.host",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = ExpoPushClient(http_client, access_token="test-access-token")
        result = await client.send(
            {
                "to": "ExponentPushToken[test-device]",
                "title": "安糖心语",
                "body": "今天感觉怎么样？",
                "data": {"message_id": "message-1"},
            }
        )

    assert result == ("ok", "ticket-1")
    assert seen_request is not None
    assert seen_request.url.path == "/--/api/v2/push/send"
    assert seen_request.headers["authorization"] == "Bearer test-access-token"
    assert (await seen_request.aread())


async def test_expo_client_reads_receipts_without_exposing_provider_message() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/--/api/v2/push/getReceipts"
        return httpx.Response(
            200,
            json={
                "data": {
                    "ticket-1": {
                        "status": "error",
                        "message": "raw message containing a token",
                        "details": {"error": "DeviceNotRegistered"},
                    }
                }
            },
        )

    async with httpx.AsyncClient(
        base_url="https://exp.host",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        result = await ExpoPushClient(http_client).get_receipt("ticket-1")

    assert result == ("error", "DeviceNotRegistered")


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_expo_client_marks_temporary_http_failures_retryable(
    status: int,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"errors": [{"code": "temporary"}]})

    async with httpx.AsyncClient(
        base_url="https://exp.host",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        with pytest.raises(ExpoPushRequestError) as caught:
            await ExpoPushClient(http_client).send({"to": "token"})

    assert caught.value.retryable is True
    assert caught.value.http_status == status


async def test_expo_client_rejects_permanent_http_and_malformed_success() -> None:
    responses: list[tuple[int, Any]] = [
        (400, {"errors": [{"code": "INVALID_PAYLOAD"}]}),
        (200, {"data": []}),
    ]

    async def handler(_request: httpx.Request) -> httpx.Response:
        status, body = responses.pop(0)
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(
        base_url="https://exp.host",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = ExpoPushClient(http_client)
        with pytest.raises(ExpoPushRequestError) as permanent:
            await client.send({"to": "token"})
        with pytest.raises(ExpoPushRequestError) as malformed:
            await client.send({"to": "token"})

    assert permanent.value.retryable is False
    assert permanent.value.code == "INVALID_PAYLOAD"
    assert malformed.value.retryable is False
    assert malformed.value.code == "invalid_response"


async def test_expo_client_retries_non_json_rate_limit_response() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    async with httpx.AsyncClient(
        base_url="https://exp.host",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        with pytest.raises(ExpoPushRequestError) as caught:
            await ExpoPushClient(http_client).send({"to": "token"})

    assert caught.value.retryable is True
    assert caught.value.http_status == 429


async def test_push_worker_sends_then_checks_the_same_ticket(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    delivery_id, _installation_id, message_id = await create_delivery(
        committed_user_id,
        now,
    )
    client = FakeExpoClient()
    worker = PushDeliveryWorker(client=cast(Any, client))

    assert await worker.run_once(now=now) is True
    assert len(client.sent) == 1
    payload = client.sent[0]
    # 锁屏文案只有称呼和问候，不带消息正文，也不带任何健康信息。
    body = cast(str, payload["body"])
    assert "糖糖想跟你聊两句" in body
    assert "今天感觉怎么样" not in body
    assert payload["channelId"] == "care-checkins-v2"
    assert payload["sound"] == "default"
    assert payload["collapseId"] == str(delivery_id)
    assert payload["tag"] == str(delivery_id)
    assert payload["data"] == {
        "kind": "routine_check_in",
        "message_id": str(message_id),
        "delivery_id": str(delivery_id),
    }

    async with session_factory() as session:
        accepted = await session.get(PushDelivery, delivery_id)
        assert accepted is not None
        assert accepted.status == PushDeliveryStatus.TICKET_ACCEPTED
        assert accepted.provider_ticket_id == "ticket-1"
        assert accepted.attempt_count == 1
        receipt_due_at = accepted.receipt_due_at
        assert receipt_due_at is not None

    assert await worker.run_once(now=receipt_due_at) is True
    assert client.receipts == ["ticket-1"]
    assert len(client.sent) == 1
    async with session_factory() as session:
        completed = await session.get(PushDelivery, delivery_id)
        assert completed is not None
        assert completed.status == PushDeliveryStatus.RECEIPT_ACCEPTED
        assert completed.attempt_count == 1
        assert completed.receipt_attempt_count == 1


async def test_two_push_workers_cannot_send_the_same_delivery_together(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    delivery_id, _installation_id, _message_id = await create_delivery(
        committed_user_id,
        now,
    )
    allow_send = asyncio.Event()
    client = FakeExpoClient(wait_before_send=allow_send)
    first = PushDeliveryWorker(client=cast(Any, client))
    second = PushDeliveryWorker(client=cast(Any, client))

    first_run = asyncio.create_task(first.run_once(now=now))
    await asyncio.wait_for(client.send_started.wait(), timeout=2)
    assert await second.run_once(now=now) is False
    allow_send.set()
    assert await asyncio.wait_for(first_run, timeout=2) is True
    assert len(client.sent) == 1

    async with session_factory() as session:
        delivery = await session.get(PushDelivery, delivery_id)
        assert delivery is not None
        assert delivery.status == PushDeliveryStatus.TICKET_ACCEPTED


async def test_device_not_registered_fences_only_the_current_registration(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    delivery_id, installation_id, _message_id = await create_delivery(
        committed_user_id,
        now,
        installation_revision=4,
    )
    client = FakeExpoClient(send_result=("error", "DeviceNotRegistered"))

    assert (
        await PushDeliveryWorker(client=cast(Any, client)).run_once(now=now)
        is True
    )

    async with session_factory() as session:
        delivery = await session.get(PushDelivery, delivery_id)
        installation = await session.get(PushInstallation, installation_id)
        assert delivery is not None
        assert installation is not None
        assert delivery.status == PushDeliveryStatus.FAILED
        assert delivery.provider_error_code == "DeviceNotRegistered"
        assert installation.disabled_reason == "DeviceNotRegistered"
        assert installation.expo_push_token is None
        assert installation.registration_revision == 5


async def test_old_installation_revision_is_never_sent(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    delivery_id, installation_id, _message_id = await create_delivery(
        committed_user_id,
        now,
    )
    async with session_factory() as session:
        installation = await session.get(PushInstallation, installation_id)
        assert installation is not None
        installation.registration_revision = 2
        installation.expo_push_token = "ExponentPushToken[new-registration]"
        await session.commit()

    client = FakeExpoClient()
    assert (
        await PushDeliveryWorker(client=cast(Any, client)).run_once(now=now)
        is True
    )
    assert client.sent == []
    async with session_factory() as session:
        delivery = await session.get(PushDelivery, delivery_id)
        assert delivery is not None
        assert delivery.status == PushDeliveryStatus.FAILED
        assert delivery.provider_error_code == "installation_changed"


async def test_missing_receipt_retries_receipt_without_resending(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    delivery_id, _installation_id, _message_id = await create_delivery(
        committed_user_id,
        now,
    )
    client = FakeExpoClient(receipt_result=("missing", None))
    worker = PushDeliveryWorker(client=cast(Any, client))
    assert await worker.run_once(now=now) is True

    async with session_factory() as session:
        ticketed = await session.get(PushDelivery, delivery_id)
        assert ticketed is not None
        first_receipt_at = ticketed.next_attempt_at
        assert first_receipt_at is not None

    assert await worker.run_once(now=first_receipt_at) is True
    async with session_factory() as session:
        waiting = await session.get(PushDelivery, delivery_id)
        assert waiting is not None
        assert waiting.status == PushDeliveryStatus.RETRY_WAIT
        assert waiting.provider_ticket_id == "ticket-1"
        assert waiting.next_attempt_at is not None
    assert len(client.sent) == 1
    assert client.receipts == ["ticket-1"]


async def test_receipt_polling_does_not_spend_the_send_retry_budget(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    delivery_id, _installation_id, _message_id = await create_delivery(
        committed_user_id,
        now,
    )
    async with session_factory() as session:
        delivery = await session.get(PushDelivery, delivery_id)
        assert delivery is not None
        delivery.attempt_count = settings.push_delivery_max_attempts - 1
        await session.commit()

    client = FakeExpoClient(receipt_result=("missing", None))
    worker = PushDeliveryWorker(client=cast(Any, client))
    assert await worker.run_once(now=now) is True
    async with session_factory() as session:
        ticketed = await session.get(PushDelivery, delivery_id)
        assert ticketed is not None
        receipt_at = ticketed.next_attempt_at
        assert receipt_at is not None
        assert ticketed.attempt_count == settings.push_delivery_max_attempts

    assert await worker.run_once(now=receipt_at) is True
    async with session_factory() as session:
        waiting = await session.get(PushDelivery, delivery_id)
        assert waiting is not None
        assert waiting.status == PushDeliveryStatus.RETRY_WAIT
        assert waiting.attempt_count == settings.push_delivery_max_attempts
        assert waiting.receipt_attempt_count == 1


async def test_device_not_registered_disables_token_after_lease_takeover(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    delivery_id, installation_id, _message_id = await create_delivery(
        committed_user_id,
        now,
    )
    allow_result = asyncio.Event()
    client = FakeExpoClient(
        send_result=("error", "DeviceNotRegistered"),
        wait_before_send=allow_result,
    )
    worker_run = asyncio.create_task(
        PushDeliveryWorker(client=cast(Any, client)).run_once(now=now)
    )
    await asyncio.wait_for(client.send_started.wait(), timeout=2)
    async with session_factory() as session:
        delivery = await session.get(PushDelivery, delivery_id)
        assert delivery is not None
        delivery.lease_token = uuid4()
        delivery.lease_expires_at = now + timedelta(minutes=1)
        await session.commit()

    allow_result.set()
    assert await asyncio.wait_for(worker_run, timeout=2) is True
    async with session_factory() as session:
        installation = await session.get(PushInstallation, installation_id)
        delivery = await session.get(PushDelivery, delivery_id)
        assert installation is not None
        assert delivery is not None
        assert installation.disabled_reason == "DeviceNotRegistered"
        assert installation.expo_push_token is None
        assert installation.registration_revision == 2
        assert delivery.status == PushDeliveryStatus.FAILED
        assert delivery.lease_token is None


async def test_health_preview_is_rechecked_before_sending(
    committed_user_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    delivery_id, _installation_id, _message_id = await create_delivery(
        committed_user_id,
        now,
        kind=ProactiveCareTaskKind.HEALTH_EVENT,
        show_message_preview=True,
    )
    client = FakeExpoClient()

    assert (
        await PushDeliveryWorker(client=cast(Any, client)).run_once(now=now)
        is True
    )

    assert client.sent[0]["body"] == "有一条新的健康关怀消息"
    assert client.sent[0]["channelId"] == "health-care-v1"
    async with session_factory() as session:
        delivery = await session.scalar(
            select(PushDelivery).where(PushDelivery.id == delivery_id)
        )
        assert delivery is not None
        assert delivery.status == PushDeliveryStatus.TICKET_ACCEPTED
