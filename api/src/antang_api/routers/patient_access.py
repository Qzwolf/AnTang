from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.database import get_session
from antang_api.behavioral import BEHAVIOR_KIND_LABELS, read_behavior_logs
from antang_api.health_profile.wearable_dashboard import read_wearable_dashboard
from antang_api.models import PatientAccessAudit, PatientAccessGrant, User, UserRole
from antang_api.patient_access import (
    check_patient_access,
    get_clinician_user,
    get_patient_user,
)
from antang_api.psychosocial import read_latest_assessment_row
from antang_api.psychosocial.report import clinician_assessment_response
from antang_api.routers.health_profile import read_health_profile
from antang_api.schemas.auth import UserResponse
from antang_api.schemas.health_profile import (
    ClinicianBehaviorLogResponse,
    ClinicianBehaviorLogsResponse,
    ClinicianPsychosocialResponse,
    HealthProfileResponse,
    WearableDashboardResponse,
)
from antang_api.schemas.patient_access import (
    GrantRequest,
    GrantResponse,
    GrantsResponse,
    PatientsResponse,
    ResolveClinicianRequest,
)

router = APIRouter(prefix="/api/v1", tags=["patient-access"])
Patient = Annotated[User, Depends(get_patient_user)]
Clinician = Annotated[User, Depends(get_clinician_user)]
Database = Annotated[AsyncSession, Depends(get_session)]


def grant_response(grant: PatientAccessGrant, clinician: User) -> GrantResponse:
    return GrantResponse(
        id=grant.id,
        clinician=UserResponse.model_validate(clinician),
        granted_at=grant.granted_at,
        revoked_at=grant.revoked_at,
        available=grant.revoked_at is None
        and clinician.is_active
        and clinician.role == UserRole.CLINICIAN,
    )


@router.post("/patient-access-grants/resolve", response_model=UserResponse)
async def resolve_clinician(
    request: ResolveClinicianRequest, user: Patient, session: Database
) -> UserResponse:
    clinician = await session.scalar(
        select(User).where(
            User.username_normalized == request.username.casefold(),
            User.role == UserRole.CLINICIAN,
            User.is_active.is_(True),
        )
    )
    if clinician is None:
        raise HTTPException(404, "未找到可授权的医护账号，请核对完整用户名")
    return UserResponse.model_validate(clinician)


@router.get("/patient-access-grants", response_model=GrantsResponse)
async def list_grants(user: Patient, session: Database) -> GrantsResponse:
    rows = (
        await session.execute(
            select(PatientAccessGrant, User)
            .join(User, User.id == PatientAccessGrant.clinician_id)
            .where(PatientAccessGrant.patient_id == user.id)
            .order_by(
                PatientAccessGrant.granted_at.desc(), PatientAccessGrant.id.desc()
            )
        )
    ).all()
    return GrantsResponse(
        grants=[grant_response(grant, clinician) for grant, clinician in rows]
    )


@router.post("/patient-access-grants", response_model=GrantResponse, status_code=201)
async def create_grant(
    request: GrantRequest, user: Patient, session: Database
) -> GrantResponse:
    # 与维护者角色变更共享账户行锁，避免生成角色变更之前的遗留授权。
    accounts = list(
        await session.scalars(
            select(User)
            .where(User.id.in_([user.id, request.clinician_id]))
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    patient = next(item for item in accounts if item.id == user.id)
    clinician = next(
        (item for item in accounts if item.id == request.clinician_id), None
    )
    if patient.role != UserRole.PATIENT or not patient.is_active:
        raise HTTPException(403, "当前账号没有此操作权限")
    if (
        clinician is None
        or clinician.role != UserRole.CLINICIAN
        or not clinician.is_active
    ):
        raise HTTPException(404, "该医护账号当前不可授权")
    grant = PatientAccessGrant(patient_id=user.id, clinician_id=clinician.id)
    session.add(grant)
    try:
        await session.flush()
        session.add(
            PatientAccessAudit(
                actor_user_id=user.id,
                patient_id=user.id,
                target_user_id=clinician.id,
                grant_id=grant.id,
                action="grant",
            )
        )
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        if (
            getattr(getattr(error.orig, "diag", None), "constraint_name", None)
            != "uq_active_patient_clinician"
        ):
            raise
        raise HTTPException(409, "已向该医护授权，请刷新列表") from error
    return grant_response(grant, clinician)


@router.delete("/patient-access-grants/{grant_id}", status_code=204)
async def revoke_grant(grant_id: UUID, user: Patient, session: Database) -> Response:
    grant = await session.scalar(
        select(PatientAccessGrant)
        .where(
            PatientAccessGrant.id == grant_id,
            PatientAccessGrant.patient_id == user.id,
        )
        .with_for_update()
    )
    if grant is None:
        raise HTTPException(404, "授权记录不存在")
    if grant.revoked_at is None:
        grant.revoked_at = datetime.now(UTC)
        session.add(
            PatientAccessAudit(
                actor_user_id=user.id,
                patient_id=user.id,
                target_user_id=grant.clinician_id,
                grant_id=grant.id,
                action="revoke",
            )
        )
        await session.commit()
    return Response(status_code=204)


@router.get("/patients", response_model=PatientsResponse)
async def list_patients(
    user: Clinician,
    session: Database,
    after: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> PatientsResponse:
    query = (
        select(User)
        .join(PatientAccessGrant, PatientAccessGrant.patient_id == User.id)
        .where(
            PatientAccessGrant.clinician_id == user.id,
            PatientAccessGrant.revoked_at.is_(None),
            User.role == UserRole.PATIENT,
            User.is_active.is_(True),
        )
    )
    if after is not None:
        query = query.where(User.id > after)
    patients = list(await session.scalars(query.order_by(User.id).limit(limit + 1)))
    session.add(PatientAccessAudit(actor_user_id=user.id, action="patients:list"))
    await session.commit()
    return PatientsResponse(
        patients=[UserResponse.model_validate(item) for item in patients[:limit]],
        next_after=patients[limit - 1].id if len(patients) > limit else None,
    )


@router.get(
    "/patients/{patient_id}/health-profile", response_model=HealthProfileResponse
)
async def patient_profile(
    patient_id: UUID, user: Clinician, session: Database, response: Response
) -> HealthProfileResponse:
    response.headers["Cache-Control"] = "no-store"
    await check_patient_access(
        session, clinician=user, patient_id=patient_id, resource="health_profile"
    )
    result = await read_health_profile(session, patient_id)
    await session.commit()
    return result


@router.get(
    "/patients/{patient_id}/psychosocial-assessment",
    response_model=ClinicianPsychosocialResponse | None,
)
async def patient_assessment(
    patient_id: UUID, user: Clinician, session: Database, response: Response
) -> ClinicianPsychosocialResponse | None:
    """医护端读量表结果：分数、两个分量表、意识水平与象限。

    患者端拿不到这些数字（`/health-profile/psychosocial-assessment` 只回
    每周联系次数），所以这里必须走授权校验：没有明确授权的医护拿不到。
    """

    response.headers["Cache-Control"] = "no-store"
    await check_patient_access(
        session, clinician=user, patient_id=patient_id, resource="health_profile"
    )
    row = await read_latest_assessment_row(session, patient_id)
    await session.commit()
    if row is None:
        return None
    return clinician_assessment_response(row)


@router.get(
    "/patients/{patient_id}/wearable-dashboard",
    response_model=WearableDashboardResponse,
)
async def patient_dashboard(
    patient_id: UUID, user: Clinician, session: Database, response: Response
) -> WearableDashboardResponse:
    response.headers["Cache-Control"] = "no-store"
    await check_patient_access(
        session, clinician=user, patient_id=patient_id, resource="wearable_dashboard"
    )
    result = await read_wearable_dashboard(session, user_id=patient_id)
    await session.commit()
    return result


@router.get(
    "/patients/{patient_id}/behavior-logs",
    response_model=ClinicianBehaviorLogsResponse,
)
async def patient_behavior_logs(
    patient_id: UUID,
    user: Clinician,
    session: Database,
    response: Response,
    days: Annotated[int, Query(ge=1, le=365)] = 90,
) -> ClinicianBehaviorLogsResponse:
    """医护端读行为记录：包含患者已经删掉的那些，并标出「患者已删除」。

    患者删一条只是让它从自己的界面上消失（软删除），医护端这一份不跟着消失——
    「自行调药」这类事如果患者单方面就能抹掉，复诊核对就失去了依据。
    医护只能读：这里没有删除入口，也不能替患者改。
    """

    response.headers["Cache-Control"] = "no-store"
    await check_patient_access(
        session, clinician=user, patient_id=patient_id, resource="health_profile"
    )
    rows = await read_behavior_logs(
        session, user_id=patient_id, days=days, include_deleted=True
    )
    await session.commit()
    return ClinicianBehaviorLogsResponse(
        days=days,
        items=[
            ClinicianBehaviorLogResponse(
                id=row.id,
                kind=row.kind,
                label=BEHAVIOR_KIND_LABELS.get(row.kind, row.kind),
                count=row.count,
                note=row.note,
                occurred_at=row.occurred_at,
                deleted_by_patient=row.deleted_at is not None,
            )
            for row in rows
        ],
    )
