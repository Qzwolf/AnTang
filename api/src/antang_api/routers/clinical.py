"""医护浏览器工作台：个人授权与统一密码解锁的全体患者视图。"""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.clinical_access import assign_unlock_token, clinical_session, require_all_patients, rotate_clinical_password, unlock_all_patients
from antang_api.database import get_session
from antang_api.health_profile.wearable_dashboard import read_wearable_dashboard
from antang_api.models import GlucoseReading, PatientAccessAudit, PatientAccessGrant, User, UserRole
from antang_api.models.clinical_access import ClinicalAccessSession, ClinicalAccessSettings
from antang_api.patient_access import check_patient_access, get_researcher_user, require_role
from antang_api.routers.auth import AuthenticatedSession, get_authenticated_session
from antang_api.routers.health_profile import read_health_profile
from antang_api.schemas.auth import UserResponse
from antang_api.schemas.clinical import ClinicalAccessStatus, ClinicalGlucose, ClinicalGlucoseSummary, ClinicalOverview, ClinicalPasswordRequest, ClinicalPatientPage, ClinicalScope, ClinicalUnlockRequest, ClinicalUnlockResponse
from antang_api.schemas.glucose import GlucoseReadingResponse
from antang_api.study import read_study

router = APIRouter(tags=["clinical-workbench"])
Auth = Annotated[AuthenticatedSession, Depends(get_authenticated_session)]
Database = Annotated[AsyncSession, Depends(get_session)]
Unlock = Annotated[str | None, Header(alias="X-Clinical-Unlock", max_length=256)]
ASSETS = Path(__file__).resolve().parents[1] / "clinical_web"
PRIVATE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@router.get("/clinician", include_in_schema=False)
async def workbench() -> FileResponse:
    return FileResponse(ASSETS / "index.html", headers={
        **PRIVATE_HEADERS,
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        "Referrer-Policy": "no-referrer",
    })


@router.get("/api/v1/clinical/access")
async def access_status(auth: Auth, session: Database, response: Response, unlock: Unlock = None) -> ClinicalAccessStatus:
    if auth.user.role not in (UserRole.CLINICIAN, UserRole.RESEARCHER):
        raise HTTPException(403, "请使用已审核的医护账号登录")
    response.headers["Cache-Control"] = "no-store"
    settings = await session.get(ClinicalAccessSettings, 1)
    access = await clinical_session(session, auth, unlock) if auth.user.role == UserRole.CLINICIAN else None
    return ClinicalAccessStatus(configured=settings is not None, unlocked=access is not None, expires_at=access.expires_at if access else None)


@router.put("/api/v1/admin/clinical-access-password", status_code=204)
async def change_shared_password(payload: ClinicalPasswordRequest, session: Database, user: Annotated[User, Depends(get_researcher_user)]) -> Response:
    await rotate_clinical_password(session, user, payload.account_password, payload.new_password)
    return Response(status_code=204, headers=PRIVATE_HEADERS)


@router.post("/api/v1/clinical/access/unlock")
async def unlock_all(payload: ClinicalUnlockRequest, auth: Auth, session: Database, response: Response) -> ClinicalUnlockResponse:
    access = await unlock_all_patients(session, auth, payload.password)
    token = assign_unlock_token(access)
    await session.execute(delete(ClinicalAccessSession).where(ClinicalAccessSession.user_id == auth.user.id, ClinicalAccessSession.login_session_id == auth.login_session.id))
    session.add(access)
    session.add(PatientAccessAudit(actor_user_id=auth.user.id, action="clinical:unlock:allowed"))
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return ClinicalUnlockResponse(token=token, expires_at=access.expires_at)


@router.post("/api/v1/clinical/access/lock", status_code=204)
async def lock_all(auth: Auth, session: Database) -> Response:
    require_role(auth.user, UserRole.CLINICIAN)
    await session.execute(delete(ClinicalAccessSession).where(ClinicalAccessSession.user_id == auth.user.id, ClinicalAccessSession.login_session_id == auth.login_session.id))
    session.add(PatientAccessAudit(actor_user_id=auth.user.id, action="clinical:lock"))
    await session.commit()
    return Response(status_code=204, headers=PRIVATE_HEADERS)


@router.get("/api/v1/clinical/patients", response_model=ClinicalPatientPage)
async def patients(auth: Auth, session: Database, response: Response, unlock: Unlock = None, scope: ClinicalScope = "granted", after: UUID | None = None, search: Annotated[str, Query(max_length=100)] = "", limit: Annotated[int, Query(ge=1, le=100)] = 50) -> ClinicalPatientPage:
    require_role(auth.user, UserRole.CLINICIAN)
    response.headers["Cache-Control"] = "no-store"
    if scope == "all":
        await require_all_patients(session, auth, unlock)
    query = select(User).where(User.role == UserRole.PATIENT, User.is_active.is_(True))
    if scope == "granted":
        query = query.join(PatientAccessGrant, PatientAccessGrant.patient_id == User.id).where(PatientAccessGrant.clinician_id == auth.user.id, PatientAccessGrant.revoked_at.is_(None))
    if search.strip():
        literal = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = "%" + literal + "%"
        query = query.where(or_(User.full_name.ilike(pattern, escape="\\"), User.username.ilike(pattern, escape="\\"), cast(User.id, String).ilike(pattern, escape="\\")))
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    if after is not None:
        query = query.where(User.id > after)
    rows = list(await session.scalars(query.order_by(User.id).limit(limit + 1)))
    result = ClinicalPatientPage(patients=[UserResponse.model_validate(row) for row in rows[:limit]], next_after=str(rows[limit - 1].id) if len(rows) > limit else None, total=total or 0)
    session.add(PatientAccessAudit(actor_user_id=auth.user.id, action=f"clinical:{scope}:list"))
    await session.commit()
    return result


def period(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    finish = end or datetime.now(UTC)
    begin = start or finish - timedelta(days=14)
    if begin.tzinfo is None or finish.tzinfo is None:
        raise HTTPException(422, "时间范围必须包含时区")
    if begin >= finish or finish - begin > timedelta(days=90):
        raise HTTPException(422, "请选择正向且不超过 90 天的时间范围")
    return begin, finish


@router.get("/api/v1/clinical/patients/{patient_id}/overview", response_model=ClinicalOverview)
async def overview(patient_id: UUID, auth: Auth, session: Database, response: Response, unlock: Unlock = None, scope: ClinicalScope = "granted", start: datetime | None = None, end: datetime | None = None) -> ClinicalOverview:
    require_role(auth.user, UserRole.CLINICIAN)
    response.headers["Cache-Control"] = "no-store"
    begin, finish = period(start, end)
    if scope == "all":
        await require_all_patients(session, auth, unlock)
        session.add(PatientAccessAudit(actor_user_id=auth.user.id, patient_id=patient_id, action="clinical:all:overview"))
    else:
        await check_patient_access(session, clinician=auth.user, patient_id=patient_id, resource="clinical_overview")
    patient = await session.scalar(select(User).where(User.id == patient_id, User.role == UserRole.PATIENT, User.is_active.is_(True)))
    if patient is None:
        raise HTTPException(404, "患者账号当前不可访问")
    condition = (GlucoseReading.user_id == patient_id, GlucoseReading.revoked_at.is_(None), GlucoseReading.measured_at >= begin, GlucoseReading.measured_at < finish)
    count, average, minimum, maximum = (await session.execute(select(func.count(GlucoseReading.id), func.avg(GlucoseReading.value_mg_dl), func.min(GlucoseReading.value_mg_dl), func.max(GlucoseReading.value_mg_dl)).where(*condition))).one()
    points = list(await session.scalars(select(GlucoseReading).where(*condition).order_by(GlucoseReading.measured_at.desc(), GlucoseReading.id.desc()).limit(5000)))
    points.reverse()
    result = ClinicalOverview(
        patient=UserResponse.model_validate(patient), period_start=begin, period_end=finish,
        glucose=ClinicalGlucose(summary=ClinicalGlucoseSummary(count=count, average_mg_dl=round(float(average), 1) if average is not None else None, minimum_mg_dl=minimum, maximum_mg_dl=maximum), points=[GlucoseReadingResponse.model_validate(point) for point in points], truncated=count > len(points), limit=5000),
        study=await read_study(session, patient_id, begin, finish),
        profile=await read_health_profile(session, patient_id),
        wearable=await read_wearable_dashboard(session, user_id=patient_id, period_start=begin, period_end=finish),
    )
    await session.commit()
    return result
