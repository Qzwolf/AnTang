"""患者自己的数据导出。

为什么要有这个模块：`DELETE /auth/account` 能让患者把账号删干净，但「拿走」和
「删掉」是两件事——前者是知情同意的另一半（可携权）。这里只做一件事：把该患者
名下所有由他产生、或与他相关的记录整理成一个 JSON，不夹带任何临床推断、不夹带
其他患者的数据。

刻意不导出：
- 陪伴记忆（Hindsight 的 bank）里的自然语言摘要——它不是患者写的，是系统推断，
  导出后脱离了上下文容易被误读；患者要删它走的是注销账号（会连带删除）。
- 医护访问审计（`patient_access_audit`）——那是给患者「看谁看过我」的界面用的，
  属于审计而非本人数据，且导出它会把其他医护的姓名带出去。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.models import (
    CgmSample,
    Conversation,
    FearBehaviorLog,
    GlucoseImport,
    GlucoseReading,
    HypoglycemiaBeliefEvent,
    MealLog,
    MedicationLog,
    Message,
    MessageRole,
    PersonalProfile,
    PsychosocialAssessment,
    User,
)

EXPORT_FORMAT = "antang-export-v1"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _text(value: Any) -> Any:
    """Decimal / Enum 之类的值统一转成 JSON 能吃的形态。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return _iso(value)
    return str(value)


async def build_patient_export(session: AsyncSession, user: User) -> dict[str, Any]:
    user_id: UUID = user.id

    profile = await session.scalar(
        select(PersonalProfile).where(PersonalProfile.user_id == user_id)
    )

    readings = (
        await session.scalars(
            select(GlucoseReading)
            .where(GlucoseReading.user_id == user_id, GlucoseReading.revoked_at.is_(None))
            .order_by(GlucoseReading.measured_at)
        )
    ).all()
    samples = (
        await session.scalars(
            select(CgmSample).where(CgmSample.user_id == user_id).order_by(CgmSample.recorded_at)
        )
    ).all()
    imports = (
        await session.scalars(
            select(GlucoseImport)
            .where(GlucoseImport.user_id == user_id)
            .order_by(GlucoseImport.created_at)
        )
    ).all()
    meals = (
        await session.scalars(
            select(MealLog).where(MealLog.user_id == user_id).order_by(MealLog.eaten_at)
        )
    ).all()
    medications = (
        await session.scalars(
            select(MedicationLog)
            .where(MedicationLog.user_id == user_id)
            .order_by(MedicationLog.taken_at)
        )
    ).all()
    assessments = (
        await session.scalars(
            select(PsychosocialAssessment)
            .where(PsychosocialAssessment.user_id == user_id)
            .order_by(PsychosocialAssessment.assessed_at)
        )
    ).all()
    behaviors = (
        await session.scalars(
            select(FearBehaviorLog)
            .where(FearBehaviorLog.user_id == user_id)
            .order_by(FearBehaviorLog.occurred_at)
        )
    ).all()
    beliefs = (
        await session.scalars(
            select(HypoglycemiaBeliefEvent)
            .where(HypoglycemiaBeliefEvent.user_id == user_id)
            .order_by(HypoglycemiaBeliefEvent.detected_at)
        )
    ).all()

    conversations = (
        await session.scalars(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.created_at)
        )
    ).all()
    messages = (
        await session.scalars(
            select(Message).where(Message.user_id == user_id).order_by(Message.created_at)
        )
    ).all()

    by_conversation: dict[UUID | None, list[dict[str, Any]]] = {}
    for message in messages:
        by_conversation.setdefault(message.conversation_id, []).append(
            {
                "id": str(message.id),
                "role": message.role.value if isinstance(message.role, MessageRole) else message.role,
                "content": message.content,
                "created_at": _iso(message.created_at),
                "status": message.status.value
                if hasattr(message.status, "value")
                else message.status,
                "client_message_id": str(message.client_message_id)
                if message.client_message_id
                else None,
            }
        )

    conversation_rows = [
        {
            "id": str(conversation.id),
            "kind": conversation.kind,
            "title": conversation.title,
            "created_at": _iso(conversation.created_at),
            "last_message_at": _iso(conversation.last_message_at),
            "messages": by_conversation.get(conversation.id, []),
        }
        for conversation in conversations
    ]
    # 会话层上线前的老消息没有 conversation_id，单独放一段，不丢。
    orphans = by_conversation.get(None, [])

    return {
        "format": EXPORT_FORMAT,
        "exported_at": _iso(datetime.now(UTC)),
        "account": {
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role.value if hasattr(user.role, "value") else user.role,
            "created_at": _iso(user.created_at),
        },
        "profile": None
        if profile is None
        else {
            "sex": profile.sex,
            "age_years": profile.age_years,
            "age_as_of_date": _text(profile.age_as_of_date),
            "height_cm": _text(profile.height_cm),
            "weight_kg": _text(profile.weight_kg),
            "resident_area": profile.resident_area,
            "schedule_type": profile.schedule_type,
            "occupation": profile.occupation,
            "updated_at": _iso(profile.updated_at),
        },
        "glucose_readings": [
            {
                "id": str(reading.id),
                "measured_at": _iso(reading.measured_at),
                "value_mg_dl": reading.value_mg_dl,
                "context": reading.context,
                "source": reading.source,
                "note": reading.note,
                "created_at": _iso(reading.created_at),
            }
            for reading in readings
        ],
        "cgm_samples": [
            {
                "recorded_at": _iso(sample.recorded_at),
                "value_mg_dl": sample.value_mg_dl,
                "trend": sample.trend,
                "source_system": sample.source_system,
                "device_id": sample.device_id,
            }
            for sample in samples
        ],
        "cgm_imports": [
            {
                "file_name": record.file_name,
                "source_system": record.source_system,
                "parsed_count": record.parsed_count,
                "inserted_count": record.inserted_count,
                "skipped_count": record.skipped_count,
                "created_at": _iso(record.created_at),
            }
            for record in imports
        ],
        "meals": [
            {
                "id": str(meal.id),
                "eaten_at": _iso(meal.eaten_at),
                "meal_type": meal.meal_type,
                "description": meal.description,
                "carbs_grams": meal.carbs_grams,
                "created_at": _iso(meal.created_at),
            }
            for meal in meals
        ],
        "medications": [
            {
                "id": str(medication.id),
                "taken_at": _iso(medication.taken_at),
                "name": medication.name,
                "dose_text": medication.dose_text,
                "kind": medication.kind,
                "note": medication.note,
                "created_at": _iso(medication.created_at),
            }
            for medication in medications
        ],
        "psychosocial_assessments": [
            {
                "assessed_at": _iso(row.assessed_at),
                "instrument": row.instrument,
                "fear_behavior_percent": row.fear_behavior_percent,
                "fear_worry_percent": row.fear_worry_percent,
                "gold_score": row.gold_score,
            }
            for row in assessments
        ],
        "fear_behavior_logs": [
            {
                "id": str(row.id),
                "occurred_at": _iso(row.occurred_at),
                "kind": row.kind,
                "count": row.count,
                "note": row.note,
            }
            for row in behaviors
        ],
        "belief_events": [
            {
                "id": str(row.id),
                "detected_at": _iso(row.detected_at),
                "belief_code": row.belief_code,
                "quote": row.quote,
                "occurrence": row.occurrence,
            }
            for row in beliefs
        ],
        "conversations": conversation_rows,
        "conversationless_messages": orphans,
        "counts": {
            "glucose_readings": len(readings),
            "cgm_samples": len(samples),
            "meals": len(meals),
            "medications": len(medications),
            "psychosocial_assessments": len(assessments),
            "fear_behavior_logs": len(behaviors),
            "belief_events": len(beliefs),
            "conversations": len(conversation_rows),
            "messages": len(messages),
        },
    }
