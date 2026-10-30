from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.database import get_session, lock_user_conversation
from antang_api.models import (
    Conversation,
    LoginSession,
    Message,
    MessageRole,
    ProactiveCareSettings,
    PushDelivery,
    PushDeliveryStatus,
    PushInstallation,
    PushPermissionState,
    RoutineCareCadence,
)
from antang_api.psychosocial.store import read_latest_assessment
from antang_api.routers.auth import (
    AuthenticatedSession,
    get_authenticated_session,
    unauthorized,
)
from antang_api.proactive_care.service import schedule_routine_check_in
from antang_api.schemas.proactive_care import (
    ProactiveCareSettingsPayload,
    PushInstallationRequest,
)

router = APIRouter(prefix="/api/v1/proactive-care", tags=["proactive-care"])


async def _with_budget(
    session: AsyncSession,
    *,
    care_settings: ProactiveCareSettings,
    user_id: UUID,
) -> ProactiveCareSettingsPayload:
    """把设置连同「本周联系了几次 / 上限 / 是否自动暂停」一起给出去。

    用户看不到次数才会觉得被打扰是随机的；看见了才觉得可控。
    """

    payload = ProactiveCareSettingsPayload.model_validate(care_settings)
    profile = await read_latest_assessment(session, user_id)
    payload.weekly_contact_limit = (
        profile.weekly_contact_limit if profile is not None else None
    )
    count = await session.scalar(
        select(func.count())
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.user_id == user_id,
            Message.role == MessageRole.ASSISTANT,
            Conversation.kind == "care",
            Message.created_at >= datetime.now(timezone.utc) - timedelta(days=7),
        )
    )
    payload.weekly_contact_count = int(count or 0)
    payload.auto_paused = (
        care_settings.routine_cadence == RoutineCareCadence.DISABLED
        and care_settings.auto_paused_at is not None
    )
    payload.auto_downgraded_at = care_settings.auto_downgraded_at
    return payload



@router.get("/settings", response_model=ProactiveCareSettingsPayload)
async def get_care_settings(
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(get_authenticated_session),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProactiveCareSettingsPayload:
    care_settings = await session.get(
        ProactiveCareSettings,
        authenticated.user.id,
    )
    if care_settings is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="主动关怀设置不存在",
        )
    return await _with_budget(
        session,
        care_settings=care_settings,
        user_id=authenticated.user.id,
    )


@router.put("/settings", response_model=ProactiveCareSettingsPayload)
async def put_care_settings(
    request: ProactiveCareSettingsPayload,
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(get_authenticated_session),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProactiveCareSettingsPayload:
    await lock_user_conversation(session, authenticated.user.id)
    care_settings = await session.scalar(
        select(ProactiveCareSettings)
        .where(ProactiveCareSettings.user_id == authenticated.user.id)
        .with_for_update()
    )
    if care_settings is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="主动关怀设置不存在",
        )

    previous_cadence = care_settings.routine_cadence
    care_settings.routine_cadence = request.routine_cadence
    if (
        request.routine_cadence != RoutineCareCadence.DISABLED
        and previous_cadence == RoutineCareCadence.DISABLED
    ):
        # 用户自己把关怀重新打开：之前是我们替他暂停的，这个标记要清掉。
        care_settings.auto_paused_at = None
        care_settings.auto_downgraded_at = None
        care_settings.unanswered_contacts = 0
    care_settings.plan_follow_up_enabled = request.plan_follow_up_enabled
    care_settings.health_events_enabled = request.health_events_enabled
    care_settings.timezone = request.timezone
    care_settings.quiet_hours_start = request.quiet_hours_start
    care_settings.quiet_hours_end = request.quiet_hours_end
    care_settings.health_notification_preview_enabled = (
        request.health_notification_preview_enabled
    )
    await schedule_routine_check_in(
        session,
        user_id=authenticated.user.id,
        after=datetime.now(timezone.utc),
        reset_existing=previous_cadence != request.routine_cadence,
    )
    await session.commit()
    return await _with_budget(
        session,
        care_settings=care_settings,
        user_id=authenticated.user.id,
    )


@router.put(
    "/installations/{installation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def put_installation(
    installation_id: UUID,
    request: PushInstallationRequest,
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(get_authenticated_session),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """把当前 App 安装幂等绑定到当前仍有效的登录会话。"""

    now = datetime.now(timezone.utc)
    current_login_session = await session.scalar(
        select(LoginSession)
        .where(
            LoginSession.id == authenticated.login_session.id,
            LoginSession.user_id == authenticated.user.id,
        )
        .with_for_update()
    )
    if (
        current_login_session is None
        or current_login_session.revoked_at is not None
        or current_login_session.expires_at <= now
    ):
        await session.rollback()
        raise unauthorized()

    installation = await session.scalar(
        select(PushInstallation)
        .where(PushInstallation.id == installation_id)
        .with_for_update()
    )
    active = (
        request.permission == PushPermissionState.GRANTED
        and request.expo_push_token is not None
    )

    if installation is None:
        installation = PushInstallation(
            id=installation_id,
            login_session_id=current_login_session.id,
            expo_push_token=request.expo_push_token,
            permission=request.permission,
            platform=request.platform,
            app_version=request.app_version,
            last_seen_at=now,
            disabled_at=None if active else now,
            disabled_reason=None if active else "notifications_unavailable",
        )
        session.add(installation)
    else:
        old_login_session = await session.get(
            LoginSession,
            installation.login_session_id,
        )
        if old_login_session is None:
            raise RuntimeError("推送安装绑定的登录会话不存在")

        cross_account_rebind = old_login_session.user_id != authenticated.user.id
        was_active = installation.disabled_at is None
        should_increment_revision = (
            installation.expo_push_token != request.expo_push_token
            or cross_account_rebind
            or was_active != active
        )
        if should_increment_revision:
            await session.execute(
                update(PushDelivery)
                .where(
                    PushDelivery.installation_id == installation.id,
                    PushDelivery.installation_revision
                    == installation.registration_revision,
                    PushDelivery.status.in_(
                        [
                            PushDeliveryStatus.PENDING,
                            PushDeliveryStatus.SENDING,
                            PushDeliveryStatus.TICKET_ACCEPTED,
                            PushDeliveryStatus.RETRY_WAIT,
                        ]
                    ),
                )
                .values(
                    status=PushDeliveryStatus.FAILED,
                    provider_error_code=(
                        "installation_rebound"
                        if cross_account_rebind
                        else "installation_changed"
                    ),
                    next_attempt_at=None,
                    lease_token=None,
                    lease_expires_at=None,
                )
            )
            installation.registration_revision += 1

        installation.login_session_id = current_login_session.id
        installation.expo_push_token = request.expo_push_token
        installation.permission = request.permission
        installation.platform = request.platform
        installation.app_version = request.app_version
        installation.last_seen_at = now
        installation.disabled_at = None if active else now
        installation.disabled_reason = (
            None if active else "notifications_unavailable"
        )

    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Expo Push Token 已绑定到其他安装",
        ) from error

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/deliveries/{delivery_id}/opened",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def mark_delivery_opened(
    delivery_id: UUID,
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(get_authenticated_session),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """幂等记录当前安装打开了属于当前用户的推送消息。"""

    delivery = await session.scalar(
        select(PushDelivery)
        .join(
            PushInstallation,
            PushInstallation.id == PushDelivery.installation_id,
        )
        .join(Message, Message.id == PushDelivery.message_id)
        .where(
            PushDelivery.id == delivery_id,
            PushInstallation.login_session_id == authenticated.login_session.id,
            Message.user_id == authenticated.user.id,
        )
        .with_for_update(of=PushDelivery)
    )
    if delivery is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到推送发送记录",
        )

    if delivery.opened_at is None:
        delivery.opened_at = datetime.now(timezone.utc)
        await session.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/installations/{installation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_installation(
    installation_id: UUID,
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(get_authenticated_session),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """幂等停用当前登录会话绑定的 App 安装。"""

    now = datetime.now(timezone.utc)
    current_login_session = await session.scalar(
        select(LoginSession)
        .where(
            LoginSession.id == authenticated.login_session.id,
            LoginSession.user_id == authenticated.user.id,
        )
        .with_for_update()
    )
    if (
        current_login_session is None
        or current_login_session.revoked_at is not None
        or current_login_session.expires_at <= now
    ):
        await session.rollback()
        raise unauthorized()

    installation = await session.scalar(
        select(PushInstallation)
        .where(
            PushInstallation.id == installation_id,
            PushInstallation.login_session_id == current_login_session.id,
        )
        .with_for_update()
    )
    if installation is not None and installation.disabled_at is None:
        await session.execute(
            update(PushDelivery)
            .where(
                PushDelivery.installation_id == installation.id,
                PushDelivery.installation_revision
                == installation.registration_revision,
                PushDelivery.status.in_(
                    [
                        PushDeliveryStatus.PENDING,
                        PushDeliveryStatus.SENDING,
                        PushDeliveryStatus.TICKET_ACCEPTED,
                        PushDeliveryStatus.RETRY_WAIT,
                    ]
                ),
            )
            .values(
                status=PushDeliveryStatus.FAILED,
                provider_error_code="installation_changed",
                next_attempt_at=None,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        installation.registration_revision += 1
        installation.disabled_at = now
        installation.disabled_reason = "client_disabled"

    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
