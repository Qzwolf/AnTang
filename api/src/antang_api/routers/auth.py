from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from antang_api.database import get_session
from antang_api.models import (
    CarePlan,
    LoginSession,
    PersonalProfile,
    ProactiveCareSettings,
    ProactiveCareTask,
    PushDelivery,
    PushDeliveryStatus,
    PushInstallation,
    User,
    UserRole,
    ClinicianApplication,
    PatientAccessAudit,
    WearableObservation,
    WearableRecordTombstone,
)
from antang_api.schemas.auth import (
    AuthResponse,
    DeleteAccountRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserResponse,
    AccountNameRequest,
)
from antang_api.security import (
    access_token_expires_in,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from antang_api.settings import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )


def new_login_session(
    user_id: UUID,
    now: datetime,
) -> tuple[str, LoginSession]:
    refresh_token = create_refresh_token()

    login_session = LoginSession(
        user_id=user_id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        expires_at=now + timedelta(days=settings.refresh_token_days),
    )

    return refresh_token, login_session


def token_pair(
    user_id: UUID,
    login_session_id: UUID,
    refresh_token: str,
) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user_id, login_session_id),
        refresh_token=refresh_token,
        expires_in=access_token_expires_in(),
    )


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    user: User
    login_session: LoginSession


async def get_authenticated_session(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthenticatedSession:
    if credentials is None:
        raise unauthorized()

    try:
        user_id, login_session_id = decode_access_token(credentials.credentials)
    except InvalidTokenError:
        raise unauthorized()

    row = (
        await session.execute(
            select(User, LoginSession)
            .join(LoginSession, LoginSession.user_id == User.id)
            .where(
                User.id == user_id,
                LoginSession.id == login_session_id,
            )
        )
    ).one_or_none()

    if row is None:
        raise unauthorized()

    user, login_session = row
    now = datetime.now(timezone.utc)
    if (
        not user.is_active
        or login_session.revoked_at is not None
        or login_session.expires_at <= now
    ):
        raise unauthorized()

    return AuthenticatedSession(user=user, login_session=login_session)


async def get_current_user(
    authenticated: Annotated[
        AuthenticatedSession,
        Depends(get_authenticated_session),
    ],
) -> User:
    return authenticated.user


async def user_response(user: User, session: AsyncSession) -> UserResponse:
    application_status = await session.scalar(
        select(ClinicianApplication.status)
        .where(ClinicianApplication.user_id == user.id)
        .order_by(
            ClinicianApplication.created_at.desc(), ClinicianApplication.id.desc()
        )
        .limit(1)
    )
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        full_name=user.full_name,
        application_status=application_status,
        created_at=user.created_at,
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    request: RegisterRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthResponse:
    """注册用户，并为新用户创建第一次登录所需的凭证。"""

    now = datetime.now(timezone.utc)

    user = User(
        username=request.username,
        username_normalized=request.username.casefold(),
        full_name=request.full_name,
        role=UserRole.PATIENT,
        password_hash=await run_in_threadpool(
            hash_password,
            request.password,
        ),
    )

    session.add(user)

    try:
        # 执行用户 INSERT，让 SQLAlchemy 生成 user.id。
        # 当前事务仍未提交，后面的登录记录会和用户一起提交。
        await session.flush()

        # 每个用户必须有一行基础档案；缺失时读取流程会明确报错。
        session.add(PersonalProfile(user_id=user.id))
        session.add(ProactiveCareSettings(user_id=user.id))
        if request.registration_kind == "clinician":
            assert request.full_name is not None and request.organization is not None
            session.add(
                ClinicianApplication(
                    user_id=user.id,
                    full_name=request.full_name,
                    organization=request.organization,
                )
            )
            session.add(
                PatientAccessAudit(
                    actor_user_id=user.id,
                    target_user_id=user.id,
                    action="clinician_application:submit",
                )
            )

        refresh_token, login_session = new_login_session(user.id, now)
        session.add(login_session)

        # 提交事务
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="注册信息与已有记录冲突",
        ) from error

    tokens = token_pair(user.id, login_session.id, refresh_token)

    return AuthResponse(
        user=await user_response(user, session),
        **tokens.model_dump(),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthResponse:
    user = await session.scalar(
        select(User).where(User.username_normalized == request.username.casefold())
    )

    if user is None or not user.is_active:
        raise unauthorized()

    password_matches = await run_in_threadpool(
        verify_password,
        request.password,
        user.password_hash,
    )

    if not password_matches:
        raise unauthorized()

    now = datetime.now(timezone.utc)
    refresh_token, login_session = new_login_session(user.id, now)

    session.add(login_session)
    await session.commit()

    tokens = token_pair(user.id, login_session.id, refresh_token)

    return AuthResponse(
        user=await user_response(user, session),
        **tokens.model_dump(),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    request: RefreshRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenPair:
    """用refresh token换取新的access token和refresh token。旧的refresh token会被废弃。"""

    now = datetime.now(timezone.utc)
    token_hash = hash_refresh_token(request.refresh_token)

    old_session = await session.scalar(
        select(LoginSession)
        .where(LoginSession.refresh_token_hash == token_hash)
        .with_for_update()
    )

    if (
        old_session is None
        or old_session.revoked_at is not None
        or old_session.expires_at <= now
    ):
        raise unauthorized()

    user = await session.get(User, old_session.user_id)

    if user is None or not user.is_active:
        raise unauthorized()

    refresh_token, login_session = new_login_session(user.id, now)
    session.add(login_session)
    await session.flush()

    await session.execute(
        update(PushInstallation)
        .where(PushInstallation.login_session_id == old_session.id)
        .values(login_session_id=login_session.id)
    )
    old_session.replaced_by_session_id = login_session.id
    old_session.revoked_at = now
    await session.commit()

    return token_pair(user.id, login_session.id, refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: LogoutRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    token_hash = hash_refresh_token(request.refresh_token)

    login_session_id = await session.scalar(
        select(LoginSession.id).where(LoginSession.refresh_token_hash == token_hash)
    )

    if login_session_id is None:
        raise unauthorized()

    now = datetime.now(timezone.utc)
    session_ids: list[UUID] = []
    while True:
        login_session = await session.scalar(
            select(LoginSession)
            .where(LoginSession.id == login_session_id)
            .with_for_update()
        )
        if login_session is None:
            raise RuntimeError("登录会话轮换链不完整")

        session_ids.append(login_session.id)
        if login_session.replaced_by_session_id is None:
            login_session.revoked_at = login_session.revoked_at or now
            break
        login_session_id = login_session.replaced_by_session_id

    installations = list(
        await session.scalars(
            select(PushInstallation)
            .where(
                PushInstallation.login_session_id.in_(session_ids),
                PushInstallation.disabled_at.is_(None),
            )
            .with_for_update()
        )
    )
    for installation in installations:
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
        installation.disabled_reason = "logout"
    await session.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def me(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserResponse:
    return await user_response(user, session)


@router.patch("/me", response_model=UserResponse)
async def update_account_name(
    request: AccountNameRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserResponse:
    user.full_name = request.full_name
    session.add(
        PatientAccessAudit(
            actor_user_id=user.id, target_user_id=user.id, action="account:name:update"
        )
    )
    await session.commit()
    return await user_response(user, session)


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    payload: DeleteAccountRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """注销账号，并删除这个人的全部数据。

    多数子表靠 users 的外键级联删除，但护理计划引用消息、主动关怀任务与手环观测引用
    手环导入时用的是 ON DELETE RESTRICT，所以必须先删这些行，否则删除用户会被数据库
    直接拒绝。陪伴记忆不在这个库里，单独按 bank 清理；清理失败就整体失败，不留下
    「账号没了但记忆还在」的状态。
    """

    if not await run_in_threadpool(
        verify_password,
        payload.password,
        user.password_hash,
    ):
        raise unauthorized()

    if settings.hindsight_mode != "disabled":
        hindsight = getattr(request.app.state, "hindsight_client", None)
        if hindsight is not None:
            try:
                await hindsight.adelete_bank(bank_id=f"antang-user-{user.id}")
            except Exception as error:  # noqa: BLE001 - 明确失败，不静默跳过
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="陪伴记忆清理失败，账号未注销，请稍后重试",
                ) from error

    for model in (
        CarePlan,
        ProactiveCareTask,
        WearableObservation,
        WearableRecordTombstone,
    ):
        await session.execute(delete(model).where(model.user_id == user.id))

    await session.execute(delete(User).where(User.id == user.id))
    await session.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
