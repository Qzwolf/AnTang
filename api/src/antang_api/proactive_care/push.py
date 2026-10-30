import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, TypeAlias
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from loguru import logger
from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from antang_api.agents.proactive_care import PROACTIVE_CARE_AGENT_NAME
from antang_api.database import session_factory
from antang_api.models import (
    AgentRun,
    AgentRunStatus,
    LoginSession,
    Message,
    MessageStatus,
    ProactiveCareSettings,
    ProactiveCareTask,
    ProactiveCareTaskKind,
    PushDelivery,
    PushDeliveryStatus,
    PushInstallation,
    PushPermissionState,
    User,
)
from antang_api.settings import settings

ExpoResult: TypeAlias = tuple[
    Literal["ok", "error", "missing"],
    str | None,
]

# 打扰预算：同一用户 24 小时内最多收到一条系统通知。消息层已经有同样的上限
# （processor.ORDINARY_CONTACT_INTERVAL），这里再守一道，避免回访计划与健康事件
# 在同一天叠出第二条通知——通知比消息更吵，一旦多起来用户只会关掉整个开关。
INTERRUPTION_BUDGET = timedelta(hours=24)

# 健康事件的通知一律不带数值与病名：锁屏上的一句话可能被旁人看到，
# 也可能把「低血糖」这三个字直接塞进用户最焦虑的时候。
GENERIC_HEALTH_BODY = "有一条新的健康关怀消息"


def _greeting_for(hour: int) -> str:
    if 5 <= hour < 11:
        return "早上"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 18:
        return "下午"
    return "晚上"


def _care_notification_body(*, name: str, local_now: datetime) -> str:
    """问候型通知的文案：只有称呼与问候，没有任何健康信息。"""

    greeting = _greeting_for(local_now.hour)
    who = name.strip() or "你"
    if who == "你":
        return f"{greeting}好。糖糖想跟你聊两句，可以吗？"
    return f"{who}，{greeting}好。糖糖想跟你聊两句，可以吗？"


class ExpoPushRequestError(RuntimeError):
    """一次没有得到可用 ticket/receipt 的 Expo HTTP 请求。"""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        http_status: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status


def _provider_code(value: object, default: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        return default
    return value


class ExpoPushClient:
    """调用 Expo Push HTTP API，并只返回安全的规范状态。"""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        access_token: str | None = None,
    ) -> None:
        self._http_client = http_client
        self._headers = (
            {"Authorization": f"Bearer {access_token}"}
            if access_token is not None
            else None
        )

    async def _post(self, path: str, body: object) -> dict[str, Any]:
        try:
            response = await self._http_client.post(
                path,
                json=body,
                headers=self._headers,
            )
        except httpx.HTTPError as error:
            raise ExpoPushRequestError("network_error", retryable=True) from error

        try:
            payload = response.json()
        except ValueError as error:
            raise ExpoPushRequestError(
                "invalid_response",
                retryable=(
                    response.status_code == 429 or response.status_code >= 500
                ),
                http_status=response.status_code,
            ) from error

        code = f"http_{response.status_code}"
        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                code = _provider_code(errors[0].get("code"), code)

        if response.status_code == 429 or response.status_code >= 500:
            raise ExpoPushRequestError(
                code,
                retryable=True,
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            raise ExpoPushRequestError(
                code,
                retryable=False,
                http_status=response.status_code,
            )
        if not isinstance(payload, dict):
            raise ExpoPushRequestError("invalid_response", retryable=False)

        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            code = (
                _provider_code(first.get("code"), "provider_error")
                if isinstance(first, dict)
                else "provider_error"
            )
            raise ExpoPushRequestError(
                code,
                retryable=code == "TOO_MANY_REQUESTS",
                http_status=response.status_code,
            )
        return payload

    async def send(self, message: dict[str, object]) -> ExpoResult:
        payload = await self._post("/--/api/v2/push/send", [message])
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise ExpoPushRequestError("invalid_response", retryable=False)

        ticket = data[0]
        status = ticket.get("status")
        if status == "ok":
            ticket_id = ticket.get("id")
            if not isinstance(ticket_id, str) or not ticket_id:
                raise ExpoPushRequestError("invalid_response", retryable=False)
            return "ok", ticket_id
        if status == "error":
            details = ticket.get("details")
            code = (
                _provider_code(details.get("error"), "provider_error")
                if isinstance(details, dict)
                else "provider_error"
            )
            return "error", code
        raise ExpoPushRequestError("invalid_response", retryable=False)

    async def get_receipt(self, ticket_id: str) -> ExpoResult:
        payload = await self._post(
            "/--/api/v2/push/getReceipts",
            {"ids": [ticket_id]},
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ExpoPushRequestError("invalid_response", retryable=False)
        receipt = data.get(ticket_id)
        if receipt is None:
            return "missing", None
        if not isinstance(receipt, dict):
            raise ExpoPushRequestError("invalid_response", retryable=False)
        status = receipt.get("status")
        if status == "ok":
            return "ok", None
        if status == "error":
            details = receipt.get("details")
            code = (
                _provider_code(details.get("error"), "provider_error")
                if isinstance(details, dict)
                else "provider_error"
            )
            return "error", code
        raise ExpoPushRequestError("invalid_response", retryable=False)


ACTIVE_DELIVERY_STATUSES = (
    PushDeliveryStatus.PENDING,
    PushDeliveryStatus.SENDING,
    PushDeliveryStatus.TICKET_ACCEPTED,
    PushDeliveryStatus.RETRY_WAIT,
)
RECEIPT_LIFETIME = timedelta(hours=24)


def _end_delivery(
    delivery: PushDelivery,
    *,
    status: PushDeliveryStatus,
    error_code: str | None,
    next_attempt_at: datetime | None = None,
) -> None:
    delivery.status = status
    delivery.provider_error_code = error_code
    delivery.next_attempt_at = next_attempt_at
    delivery.lease_token = None
    delivery.lease_expires_at = None


class PushDeliveryWorker:
    """租约领取已提交的主动消息，并完成 Expo ticket/receipt 流程。"""

    def __init__(
        self,
        *,
        client: ExpoPushClient,
        sessions: async_sessionmaker[AsyncSession] = session_factory,
    ) -> None:
        self._client = client
        self._sessions = sessions

    async def run_once(self, *, now: datetime | None = None) -> bool:
        claimed_at = now or datetime.now(timezone.utc)
        async with self._sessions() as session:
            delivery = await session.scalar(
                select(PushDelivery)
                .where(
                    or_(
                        and_(
                            PushDelivery.status == PushDeliveryStatus.PENDING,
                            or_(
                                PushDelivery.next_attempt_at.is_(None),
                                PushDelivery.next_attempt_at <= claimed_at,
                            ),
                        ),
                        and_(
                            PushDelivery.status
                            == PushDeliveryStatus.TICKET_ACCEPTED,
                            PushDelivery.next_attempt_at <= claimed_at,
                        ),
                        and_(
                            PushDelivery.status == PushDeliveryStatus.RETRY_WAIT,
                            PushDelivery.next_attempt_at <= claimed_at,
                        ),
                        and_(
                            PushDelivery.status == PushDeliveryStatus.SENDING,
                            PushDelivery.lease_expires_at <= claimed_at,
                        ),
                    )
                )
                .order_by(
                    case(
                        (PushDelivery.status == PushDeliveryStatus.SENDING, 0),
                        (PushDelivery.status == PushDeliveryStatus.PENDING, 1),
                        else_=2,
                    ),
                    PushDelivery.next_attempt_at,
                    PushDelivery.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if delivery is None:
                return False

            if delivery.provider_ticket_id is None:
                if delivery.expires_at <= claimed_at:
                    _end_delivery(
                        delivery,
                        status=PushDeliveryStatus.EXPIRED,
                        error_code="send_expired",
                    )
                    await session.commit()
                    return True
                remote_deadline = delivery.expires_at
            else:
                if delivery.receipt_due_at is None:
                    _end_delivery(
                        delivery,
                        status=PushDeliveryStatus.FAILED,
                        error_code="invalid_receipt_state",
                    )
                    await session.commit()
                    return True
                remote_deadline = delivery.receipt_due_at + RECEIPT_LIFETIME
                if remote_deadline <= claimed_at:
                    _end_delivery(
                        delivery,
                        status=PushDeliveryStatus.FAILED,
                        error_code="receipt_unavailable",
                    )
                    await session.commit()
                    return True

            if (
                delivery.provider_ticket_id is None
                and
                delivery.attempt_count >= settings.push_delivery_max_attempts
            ):
                _end_delivery(
                    delivery,
                    status=PushDeliveryStatus.FAILED,
                    error_code="attempts_exhausted",
                )
                await session.commit()
                return True

            message = await session.get(Message, delivery.message_id)
            installation = await session.get(
                PushInstallation,
                delivery.installation_id,
            )
            login_session = (
                await session.get(LoginSession, installation.login_session_id)
                if installation is not None
                else None
            )
            root_run = await session.scalar(
                select(AgentRun)
                .where(
                    AgentRun.result_message_id == delivery.message_id,
                    AgentRun.parent_run_id.is_(None),
                    AgentRun.agent_name == PROACTIVE_CARE_AGENT_NAME,
                    AgentRun.status == AgentRunStatus.COMPLETED,
                )
                .limit(1)
            )
            task = (
                await session.get(ProactiveCareTask, root_run.trigger_care_task_id)
                if root_run is not None
                and root_run.trigger_care_task_id is not None
                else None
            )
            valid_target = (
                message is not None
                and message.status == MessageStatus.COMPLETED
                and installation is not None
                and installation.registration_revision
                == delivery.installation_revision
                and installation.disabled_at is None
                and installation.permission == PushPermissionState.GRANTED
                and installation.expo_push_token is not None
                and login_session is not None
                and login_session.user_id == message.user_id
                and login_session.revoked_at is None
                and login_session.expires_at > claimed_at
                and task is not None
                and task.user_id == message.user_id
            )
            if not valid_target:
                error_code = (
                    "installation_changed"
                    if installation is not None
                    and installation.registration_revision
                    != delivery.installation_revision
                    else "installation_unavailable"
                )
                _end_delivery(
                    delivery,
                    status=PushDeliveryStatus.FAILED,
                    error_code=error_code,
                )
                await session.commit()
                return True

            assert message is not None
            assert installation is not None
            assert installation.expo_push_token is not None
            assert task is not None

            budget_used_at = await session.scalar(
                select(PushDelivery.created_at)
                .join(
                    PushInstallation,
                    PushInstallation.id == PushDelivery.installation_id,
                )
                .join(
                    LoginSession,
                    LoginSession.id == PushInstallation.login_session_id,
                )
                .where(
                    LoginSession.user_id == message.user_id,
                    PushDelivery.id != delivery.id,
                    PushDelivery.created_at >= claimed_at - INTERRUPTION_BUDGET,
                    PushDelivery.status.in_(
                        (
                            PushDeliveryStatus.TICKET_ACCEPTED,
                            PushDeliveryStatus.RECEIPT_ACCEPTED,
                            PushDeliveryStatus.SENDING,
                        )
                    ),
                )
                .order_by(PushDelivery.created_at.desc())
                .limit(1)
            )
            if budget_used_at is not None:
                # 24 小时内已经推过一条：这条不再发，消息仍留在对话页里。
                _end_delivery(
                    delivery,
                    status=PushDeliveryStatus.FAILED,
                    error_code="interruption_budget",
                )
                await session.commit()
                return True

            care_settings = await session.get(
                ProactiveCareSettings,
                message.user_id,
            )
            if care_settings is None:
                raise RuntimeError("用户缺少主动关怀设置")

            user = await session.get(User, message.user_id)
            display_name = ""
            if user is not None:
                display_name = (user.full_name or user.username or "").strip()
            try:
                local_zone = ZoneInfo(care_settings.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                local_zone = timezone.utc
            local_now = claimed_at.astimezone(local_zone)

            lease_token = uuid4()
            delivery.status = PushDeliveryStatus.SENDING
            delivery.lease_token = lease_token
            delivery.lease_expires_at = claimed_at + timedelta(
                seconds=settings.push_delivery_lease_seconds
            )
            delivery.next_attempt_at = None
            delivery.provider_error_code = None
            if delivery.provider_ticket_id is None:
                delivery.attempt_count += 1
                operation_attempt_count = delivery.attempt_count
            else:
                delivery.receipt_attempt_count += 1
                operation_attempt_count = delivery.receipt_attempt_count

            delivery_id = delivery.id
            installation_id = installation.id
            installation_revision = delivery.installation_revision
            provider_ticket_id = delivery.provider_ticket_id
            payload = {
                "to": installation.expo_push_token,
                "title": "安糖心语",
                "body": (
                    GENERIC_HEALTH_BODY
                    if task.kind == ProactiveCareTaskKind.HEALTH_EVENT
                    else _care_notification_body(
                        name=display_name,
                        local_now=local_now,
                    )
                ),
                "data": {
                    "kind": task.kind.value,
                    "message_id": str(message.id),
                    "delivery_id": str(delivery.id),
                },
                "channelId": (
                    "health-care-v1"
                    if task.kind == ProactiveCareTaskKind.HEALTH_EVENT
                    else "care-checkins-v2"
                ),
                "sound": "default",
                "collapseId": str(delivery.id),
                "tag": str(delivery.id),
                "ttl": max(
                    1,
                    int((delivery.expires_at - claimed_at).total_seconds()),
                ),
            }
            await session.commit()

        try:
            result = (
                await self._client.send(payload)
                if provider_ticket_id is None
                else await self._client.get_receipt(provider_ticket_id)
            )
        except ExpoPushRequestError as error:
            failed_at = datetime.now(timezone.utc)
            async with self._sessions() as session:
                delivery = await session.scalar(
                    select(PushDelivery)
                    .where(
                        PushDelivery.id == delivery_id,
                        PushDelivery.status == PushDeliveryStatus.SENDING,
                        PushDelivery.lease_token == lease_token,
                    )
                    .with_for_update()
                )
                if delivery is None:
                    return True

                delay = min(
                    settings.push_delivery_retry_seconds
                    * (2 ** max(0, operation_attempt_count - 1)),
                    900,
                )
                retry_at = failed_at + timedelta(seconds=delay)
                if (
                    error.retryable
                    and (
                        provider_ticket_id is not None
                        or delivery.attempt_count
                        < settings.push_delivery_max_attempts
                    )
                    and retry_at < remote_deadline
                ):
                    _end_delivery(
                        delivery,
                        status=PushDeliveryStatus.RETRY_WAIT,
                        error_code=error.code,
                        next_attempt_at=retry_at,
                    )
                else:
                    _end_delivery(
                        delivery,
                        status=(
                            PushDeliveryStatus.EXPIRED
                            if provider_ticket_id is None
                            and failed_at >= remote_deadline
                            else PushDeliveryStatus.FAILED
                        ),
                        error_code=error.code,
                    )
                await session.commit()
            logger.bind(
                delivery_id=str(delivery_id),
                provider_error_code=error.code,
                http_status=error.http_status,
                attempt_count=operation_attempt_count,
            ).warning("push_delivery_request_failed")
            return True

        finished_at = datetime.now(timezone.utc)
        result_status, result_value = result
        if result_status == "error" and result_value == "DeviceNotRegistered":
            async with self._sessions() as session:
                installation = await session.scalar(
                    select(PushInstallation)
                    .where(
                        PushInstallation.id == installation_id,
                        PushInstallation.registration_revision
                        == installation_revision,
                    )
                    .with_for_update()
                )
                if installation is None:
                    return True

                installation.expo_push_token = None
                installation.disabled_at = finished_at
                installation.disabled_reason = "DeviceNotRegistered"
                installation.registration_revision += 1
                await session.execute(
                    update(PushDelivery)
                    .where(
                        PushDelivery.installation_id == installation_id,
                        PushDelivery.installation_revision
                        == installation_revision,
                        PushDelivery.status.in_(ACTIVE_DELIVERY_STATUSES),
                    )
                    .values(
                        status=PushDeliveryStatus.FAILED,
                        provider_error_code="DeviceNotRegistered",
                        next_attempt_at=None,
                        lease_token=None,
                        lease_expires_at=None,
                    )
                )
                await session.commit()
            return True

        async with self._sessions() as session:
            delivery = await session.scalar(
                select(PushDelivery)
                .where(
                    PushDelivery.id == delivery_id,
                    PushDelivery.status == PushDeliveryStatus.SENDING,
                    PushDelivery.lease_token == lease_token,
                )
                .with_for_update()
            )
            if delivery is None:
                return True
            current_revision = await session.scalar(
                select(PushInstallation.registration_revision).where(
                    PushInstallation.id == installation_id
                )
            )
            if current_revision != installation_revision:
                _end_delivery(
                    delivery,
                    status=PushDeliveryStatus.FAILED,
                    error_code="installation_changed",
                )
            elif provider_ticket_id is None and result_status == "ok":
                if result_value is None:
                    raise RuntimeError("Expo ticket 成功但缺少 ticket id")
                delivery.provider_ticket_id = result_value
                delivery.receipt_due_at = finished_at + timedelta(
                    seconds=settings.push_receipt_delay_seconds
                )
                _end_delivery(
                    delivery,
                    status=PushDeliveryStatus.TICKET_ACCEPTED,
                    error_code=None,
                    next_attempt_at=delivery.receipt_due_at,
                )
            elif provider_ticket_id is not None and result_status == "ok":
                _end_delivery(
                    delivery,
                    status=PushDeliveryStatus.RECEIPT_ACCEPTED,
                    error_code=None,
                )
            elif result_status == "missing":
                retry_at = finished_at + timedelta(
                    seconds=settings.push_receipt_delay_seconds
                )
                if retry_at < remote_deadline:
                    _end_delivery(
                        delivery,
                        status=PushDeliveryStatus.RETRY_WAIT,
                        error_code="receipt_pending",
                        next_attempt_at=retry_at,
                    )
                else:
                    _end_delivery(
                        delivery,
                        status=PushDeliveryStatus.FAILED,
                        error_code="receipt_unavailable",
                    )
            elif result_status == "error" and result_value == "MessageRateExceeded":
                retry_at = finished_at + timedelta(
                    seconds=settings.push_delivery_retry_seconds
                )
                delivery.provider_ticket_id = None
                delivery.receipt_due_at = None
                if (
                    delivery.attempt_count < settings.push_delivery_max_attempts
                    and retry_at < delivery.expires_at
                ):
                    _end_delivery(
                        delivery,
                        status=PushDeliveryStatus.RETRY_WAIT,
                        error_code="MessageRateExceeded",
                        next_attempt_at=retry_at,
                    )
                else:
                    _end_delivery(
                        delivery,
                        status=PushDeliveryStatus.FAILED,
                        error_code="MessageRateExceeded",
                    )
            else:
                _end_delivery(
                    delivery,
                    status=PushDeliveryStatus.FAILED,
                    error_code=result_value or "provider_error",
                )
            await session.commit()

        return True

    async def run_forever(self) -> None:
        while True:
            if not await self.run_once():
                await asyncio.sleep(settings.push_delivery_worker_poll_seconds)
