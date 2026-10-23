from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.behavioral import (
    BEHAVIOR_KIND_LABELS,
    delete_behavior_log,
    delete_belief_event,
    read_behavior_logs,
    read_belief_events,
    read_fear_summary,
)
from antang_api.database import get_session
from antang_api.health_profile.glucose import (
    list_glucose_readings,
    record_glucose_reading,
    revoke_glucose_reading,
    summarize_glucose,
)
from antang_api.schemas.glucose import (
    GlucoseReadingCreate,
    GlucoseReadingResponse,
    GlucoseSummaryResponse,
)
from antang_api.health_profile.errors import (
    HealthProfileChangedError,
    HealthProfileInvariantError,
    InvalidProfileCardDecisionError,
    InvalidProfileProposalError,
    ProfileClientActionConflictError,
    ProfileCardDecisionConflictError,
    ProfileCardNotFoundError,
    WearableImportConflictError,
)
from antang_api.health_profile.service import (
    answer_profile_card,
    apply_manual_health_profile_change,
    decide_profile_card,
    load_health_profile_snapshot,
    profile_card_options,
)
from antang_api.health_profile.wearable_dashboard import read_wearable_dashboard
from antang_api.health_profile.wearable_service import (
    list_health_connect_record_ids,
    process_wearable_import,
    read_latest_wearable_observations,
    read_wearable_observations,
)
from antang_api.proactive_care.service import record_health_import
from antang_api.psychosocial import (
    Instrument,
    current_instrument,
    read_latest_assessment_row,
    record_assessment,
    score_answers,
)
from antang_api.psychosocial.report import patient_assessment_response
from antang_api.models import (
    HYPOGLYCEMIA_BELIEFS,
    HealthProfileChange,
    ProfileChangeMode,
    ProfileChangeStatus,
    PsychosocialAssessment,
    User,
    WearableRecordType,
)
from antang_api.patient_access import get_patient_user
from antang_api.schemas.health_profile import (
    BehaviorLogResponse,
    BeliefEventResponse,
    BeliefNodeResponse,
    FearSummaryResponse,
    HealthProfileChangeRequest,
    HealthProfileChangeResponse,
    HealthProfileResponse,
    HealthConnectRecordIdsResponse,
    HeartRateData,
    HeartRateTrendPointResponse,
    InstrumentOptionResponse,
    InstrumentQuestionResponse,
    ProfileCardAnswerRequest,
    ProfileCardDecisionRequest,
    ProfileCardDecisionResponse,
    ProfileCardResponse,
    ProfileCardsResponse,
    PsychosocialAssessmentRequest,
    PsychosocialInstrumentResponse,
    PsychosocialProfileResponse,
    WearableImportRequest,
    WearableImportResponse,
    WearableDashboardResponse,
    WearableLatestResponse,
)

router = APIRouter(prefix="/api/v1/health-profile", tags=["health-profile"])

# 信念枚举 → 中文标签。与行为词表同理，只在服务端留一份，客户端不再各写一套。
_BELIEF_LABELS = dict(HYPOGLYCEMIA_BELIEFS)


@router.get("", response_model=HealthProfileResponse)
async def get_health_profile(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthProfileResponse:
    return await read_health_profile(session, user.id)


async def read_health_profile(
    session: AsyncSession, user_id: UUID
) -> HealthProfileResponse:
    snapshot = await load_health_profile_snapshot(session, user_id)
    latest = await read_latest_wearable_observations(
        session,
        user_id=user_id,
    )
    trend_end = datetime.now(UTC)
    trend_start = trend_end - timedelta(hours=6)
    latest_heart_source = next(
        (
            item.source_package
            for item in latest
            if item.record_type is WearableRecordType.HEART_RATE
        ),
        None,
    )
    heart_rate_observations = (
        await read_wearable_observations(
            session,
            user_id=user_id,
            record_types=[WearableRecordType.HEART_RATE],
            start=trend_start,
            end=trend_end,
            source_package=latest_heart_source,
            limit=5000,
        )
        if latest_heart_source is not None
        else []
    )
    heart_rate_points = {
        sample.time: sample.beats_per_minute
        for observation in heart_rate_observations
        for sample in HeartRateData.model_validate(observation.data).samples
        if trend_start <= sample.time <= trend_end
    }
    return HealthProfileResponse(
        personal_profile=snapshot.personal_profile,
        health_facts=snapshot.health_facts,
        wearable_latest=[
            WearableLatestResponse(
                record_type=item.record_type,
                observed_at=item.end_time,
                data=item.data,
                source_package=item.source_package,
            )
            for item in latest
        ],
        heart_rate_trend=[
            HeartRateTrendPointResponse(
                observed_at=observed_at,
                beats_per_minute=beats_per_minute,
            )
            for observed_at, beats_per_minute in sorted(heart_rate_points.items())
        ],
    )


@router.get("/cards", response_model=ProfileCardsResponse)
async def get_profile_cards(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> ProfileCardsResponse:
    changes = list(
        await session.scalars(
            select(HealthProfileChange)
            .where(
                HealthProfileChange.user_id == user.id,
                HealthProfileChange.status == ProfileChangeStatus.PENDING,
            )
            .order_by(HealthProfileChange.created_at, HealthProfileChange.id)
            .limit(limit)
        )
    )
    cards: list[ProfileCardResponse] = []
    for change in changes:
        if change.question is None:
            raise HealthProfileInvariantError("pending profile change has no question")
        kind = cast(
            Literal[
                ProfileChangeMode.CONFIRMATION,
                ProfileChangeMode.CLARIFICATION,
            ],
            change.mode,
        )
        options, allow_custom_input, placeholder = profile_card_options(change)
        cards.append(
            # 选项与输入方式由服务端固定规则生成。
            ProfileCardResponse(
                id=change.id,
                kind=kind,
                target_type=change.target_type,
                field_name=change.field_name,
                operation=change.operation,
                question=change.question,
                proposed_value=change.proposed_value,
                options=options,
                allow_custom_input=allow_custom_input,
                custom_input_placeholder=placeholder,
                created_at=change.created_at,
            )
        )
    return ProfileCardsResponse(cards=cards)


@router.post(
    "/changes",
    response_model=HealthProfileChangeResponse,
)
async def change_health_profile(
    request: HealthProfileChangeRequest,
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HealthProfileChangeResponse:
    try:
        change = await apply_manual_health_profile_change(
            session,
            user_id=user.id,
            request=request,
        )
        await session.commit()
    except HealthProfileChangedError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "health_profile_changed"},
        ) from error
    except ProfileClientActionConflictError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "client_action_id_reused"},
        ) from error
    except InvalidProfileProposalError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_health_profile_change"},
        ) from error

    if change.result_revision is None:
        raise HealthProfileInvariantError("applied profile change has no revision")
    return HealthProfileChangeResponse(
        change_id=change.id,
        status=ProfileChangeStatus.APPLIED,
        target_type=change.target_type,
        field_name=change.field_name,
        operation=change.operation,
        target_id=change.target_id,
        result_revision=change.result_revision,
    )


@router.post(
    "/cards/{card_id}/answer",
    response_model=ProfileCardDecisionResponse,
    responses={409: {"model": ProfileCardDecisionResponse}},
)
async def answer_card(
    card_id: UUID,
    request: ProfileCardAnswerRequest,
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileCardDecisionResponse | JSONResponse:
    try:
        card_status = await answer_profile_card(
            session,
            user_id=user.id,
            card_id=card_id,
            request=request,
        )
        await session.commit()
    except ProfileCardNotFoundError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="档案卡片不存在",
        ) from error
    except ProfileCardDecisionConflictError:
        await session.rollback()
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ProfileCardDecisionResponse(
                card_id=card_id,
                status=ProfileChangeStatus.CONFLICTED,
            ).model_dump(mode="json"),
        )
    except (InvalidProfileCardDecisionError, InvalidProfileProposalError) as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_profile_card_answer"},
        ) from error

    response_status = cast(
        Literal[
            ProfileChangeStatus.APPLIED,
            ProfileChangeStatus.REJECTED,
            ProfileChangeStatus.CONFLICTED,
        ],
        card_status,
    )
    response = ProfileCardDecisionResponse(card_id=card_id, status=response_status)
    if card_status is ProfileChangeStatus.CONFLICTED:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/cards/{card_id}/decision",
    response_model=ProfileCardDecisionResponse,
    responses={409: {"model": ProfileCardDecisionResponse}},
)
async def decide_card(
    card_id: UUID,
    request: ProfileCardDecisionRequest,
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileCardDecisionResponse | JSONResponse:
    try:
        card_status = await decide_profile_card(
            session,
            user_id=user.id,
            card_id=card_id,
            client_action_id=request.client_action_id,
            decision=request.decision,
        )
        await session.commit()
    except ProfileCardNotFoundError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="档案卡片不存在",
        ) from error
    except (
        ProfileCardDecisionConflictError,
        InvalidProfileCardDecisionError,
    ):
        await session.rollback()
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ProfileCardDecisionResponse(
                card_id=card_id,
                status=ProfileChangeStatus.CONFLICTED,
            ).model_dump(mode="json"),
        )

    response_status = cast(
        Literal[
            ProfileChangeStatus.APPLIED,
            ProfileChangeStatus.REJECTED,
            ProfileChangeStatus.CONFLICTED,
        ],
        card_status,
    )
    response = ProfileCardDecisionResponse(card_id=card_id, status=response_status)
    if card_status is ProfileChangeStatus.CONFLICTED:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=response.model_dump(mode="json"),
        )
    return response


@router.post(
    "/wearable-imports",
    response_model=WearableImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_wearable_records(
    request: WearableImportRequest,
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WearableImportResponse:
    try:
        result = await process_wearable_import(
            session,
            user_id=user.id,
            request=request,
        )
        await record_health_import(
            session,
            user_id=user.id,
            import_id=result.import_id,
        )
        await session.commit()
        return result
    except WearableImportConflictError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code},
        ) from error


@router.get(
    "/wearable-health-connect-ids",
    response_model=HealthConnectRecordIdsResponse,
)
async def get_health_connect_record_ids(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    record_type: WearableRecordType,
    after: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
) -> HealthConnectRecordIdsResponse:
    ids, next_after = await list_health_connect_record_ids(
        session,
        user_id=user.id,
        record_type=record_type,
        after=after,
        limit=limit,
    )
    return HealthConnectRecordIdsResponse(ids=ids, next_after=next_after)


@router.get("/wearable-dashboard", response_model=WearableDashboardResponse)
async def get_wearable_dashboard(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WearableDashboardResponse:
    return await read_wearable_dashboard(session, user_id=user.id)


def _instrument_response(instrument: Instrument) -> PsychosocialInstrumentResponse:
    return PsychosocialInstrumentResponse(
        id=instrument.id,
        title=instrument.title,
        instructions=instrument.instructions,
        disclaimer=instrument.disclaimer,
        options=[
            InstrumentOptionResponse(value=option.value, label=option.label)
            for option in instrument.options
        ],
        questions=[
            InstrumentQuestionResponse(id=question.id, kind=question.kind, text=question.text)
            for question in instrument.questions
        ],
        gold_label=instrument.gold_label,
        gold_hint=instrument.gold_hint,
        gold_options=[
            InstrumentOptionResponse(value=option.value, label=option.label)
            for option in instrument.gold_options
        ],
    )


@router.get(
    "/psychosocial-instrument",
    response_model=PsychosocialInstrumentResponse,
)
async def get_psychosocial_instrument(
    user: Annotated[User, Depends(get_patient_user)],
) -> PsychosocialInstrumentResponse:
    """App 每次打开这一页时拉题库。

    题目由服务端给，换量表（比如 HFS-II 授权到位）只发后端，不用重打安装包。
    """

    del user  # 登录即可读，题目内容与具体患者无关。
    return _instrument_response(current_instrument())


def _psychosocial_response(row: PsychosocialAssessment) -> PsychosocialProfileResponse:
    # 患者端只回行为结果（每周几次）；分数与象限走医护端，见 psychosocial/report.py。
    return patient_assessment_response(row)


@router.get(
    "/psychosocial-assessment",
    response_model=PsychosocialProfileResponse | None,
)
async def get_psychosocial_assessment(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PsychosocialProfileResponse | None:
    row = await read_latest_assessment_row(session, user.id)
    if row is None:
        return None
    return _psychosocial_response(row)


@router.post(
    "/psychosocial-assessment",
    response_model=PsychosocialProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_psychosocial_assessment(
    request: PsychosocialAssessmentRequest,
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PsychosocialProfileResponse:
    if request.answers is not None:
        # 百分比由服务端按当前题库算：换量表只改服务端，装过的包不用动。
        answer_ids = [answer.id for answer in request.answers]
        if len(set(answer_ids)) != len(answer_ids):
            raise HTTPException(status_code=422, detail="同一道题提交了两次")
        try:
            behavior_percent, worry_percent = score_answers(
                {answer.id: answer.value for answer in request.answers}
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    else:
        behavior_percent = request.fear_behavior_percent
        worry_percent = request.fear_worry_percent
        if behavior_percent is None or worry_percent is None:
            raise HTTPException(status_code=422, detail="要么带逐题答案，要么带两个百分比")
    row = await record_assessment(
        session,
        user_id=user.id,
        fear_behavior_percent=behavior_percent,
        fear_worry_percent=worry_percent,
        gold_score=request.gold_score,
        instrument=request.instrument,
        assessed_at=request.assessed_at,
    )
    response = _psychosocial_response(row)
    await session.commit()
    return response


@router.get("/fear-summary", response_model=FearSummaryResponse)
async def get_fear_summary(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=7, le=90)] = 28,
) -> FearSummaryResponse:
    """显性分数与行为代理的对照，供 App 在心理社会卡片下展示。"""

    summary = await read_fear_summary(session, user_id=user.id, days=days)
    assessment = (
        _psychosocial_response(summary.assessment_row)
        if summary.assessment_row is not None
        else None
    )
    return FearSummaryResponse(
        window_start=summary.window_start,
        window_end=summary.window_end,
        behavior_counts=summary.behavior_counts,
        behavior_total=summary.behavior_total,
        monitoring_weekly=summary.monitoring_weekly,
        avoidance_weekly=summary.avoidance_weekly,
        beliefs=[
            BeliefNodeResponse(
                belief_code=node.belief_code,
                label=node.label,
                mentions=node.mentions,
                reframes=node.reframes,
                denials=node.denials,
                latest_quote=node.latest_quote,
                latest_detected_at=node.latest_detected_at,
                resolved=node.resolved,
            )
            for node in summary.beliefs
        ],
        assessment=assessment,
        discrepancy=summary.discrepancy,
        discrepancy_note=summary.discrepancy_note,
    )


@router.post(
    "/glucose-readings",
    response_model=GlucoseReadingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_glucose_reading(
    payload: GlucoseReadingCreate,
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GlucoseReadingResponse:
    """录入一条血糖读数。患者手工填写，报告解析将来也走这里。"""

    reading = await record_glucose_reading(
        session,
        user_id=user.id,
        value_mg_dl=payload.value_mg_dl,
        measured_at=payload.measured_at,
        context=payload.context,
        note=payload.note,
    )
    await session.commit()
    return GlucoseReadingResponse.model_validate(reading)


@router.get("/glucose-readings", response_model=list[GlucoseReadingResponse])
async def get_glucose_readings(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> list[GlucoseReadingResponse]:
    """按时间倒序返回最近的读数，已撤回的不返回。"""

    readings = await list_glucose_readings(session, user_id=user.id, days=days)
    return [GlucoseReadingResponse.model_validate(item) for item in readings]


@router.get("/glucose-readings/summary", response_model=GlucoseSummaryResponse)
async def get_glucose_summary(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> GlucoseSummaryResponse:
    """窗口内的读数统计，供界面展示条数与区间。"""

    summary = await summarize_glucose(session, user_id=user.id, days=days)
    return GlucoseSummaryResponse.model_validate(summary)


@router.delete("/glucose-readings/{reading_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_glucose_reading(
    reading_id: UUID,
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """撤回一条读数：数据不再被读取，但保留"记录过又撤回"这个事实。"""

    revoked = await revoke_glucose_reading(
        session, user_id=user.id, reading_id=reading_id
    )
    if revoked is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="没有这条血糖记录"
        )
    await session.commit()


@router.get("/fear-behavior", response_model=list[BehaviorLogResponse])
async def list_fear_behavior_logs(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=90)] = 28,
) -> list[BehaviorLogResponse]:
    """逐条列出行为记录，供 App 做「我的记录」并逐条撤回。

    汇总卡片只给次数，删不了；要让人自己删，得先看到是哪一条、哪一天。
    """

    rows = await read_behavior_logs(session, user_id=user.id, days=days)
    return [
        BehaviorLogResponse(
            id=row.id,
            kind=row.kind,
            label=BEHAVIOR_KIND_LABELS.get(row.kind, row.kind),
            count=row.count,
            note=row.note,
            occurred_at=row.occurred_at,
        )
        for row in rows
    ]


@router.get("/belief-events", response_model=list[BeliefEventResponse])
async def list_hypoglycemia_belief_events(
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=365)] = 90,
) -> list[BeliefEventResponse]:
    """逐条列出信念观测，带患者原话；删掉一条就是连那句原话一起删。"""

    rows = await read_belief_events(session, user_id=user.id, days=days)
    return [
        BeliefEventResponse(
            id=row.id,
            belief_code=row.belief_code,
            label=_BELIEF_LABELS.get(row.belief_code, row.belief_code),
            quote=row.quote,
            occurrence=cast(Literal["mentioned", "reframed", "denied"], row.occurrence),
            detected_at=row.detected_at,
        )
        for row in rows
    ]


@router.delete("/fear-behavior/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fear_behavior_log(
    log_id: UUID,
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """患者撤回一条「因为怕低血糖而做的事」记录：真删，不留痕。"""

    deleted = await delete_behavior_log(session, user_id=user.id, log_id=log_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="没有这条记录"
        )
    await session.commit()


@router.delete("/belief-events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hypoglycemia_belief_event(
    event_id: UUID,
    user: Annotated[User, Depends(get_patient_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """患者撤回一次信念观测：连同那句原话一起真删。"""

    deleted = await delete_belief_event(session, user_id=user.id, event_id=event_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="没有这条记录"
        )
    await session.commit()
