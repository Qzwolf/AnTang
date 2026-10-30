from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from langchain_core.tools import StructuredTool
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.behavioral import (
    BeliefNode,
    FearSummary,
    delete_behavior_log,
    read_behavior_logs,
    read_fear_summary,
    record_behavior,
    record_belief,
    render_behavior_context,
)
from antang_api.models import PsychosocialAssessment, User
from antang_api.tools.behavior import (
    BeliefRequest,
    FearBehaviorRequest,
    build_behavior_tools,
)

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


async def _user(session: AsyncSession, prefix: str) -> User:
    suffix = uuid4().hex[:12]
    user = User(
        username=f"{prefix}_{suffix}",
        username_normalized=f"{prefix}_{suffix}",
        password_hash="test-only",
    )
    session.add(user)
    await session.flush()
    return user


async def _assessment(
    session: AsyncSession,
    *,
    user: User,
    fear_behavior_percent: int,
    fear_worry_percent: int = 30,
) -> None:
    session.add(
        PsychosocialAssessment(
            user_id=user.id,
            fear_behavior_percent=fear_behavior_percent,
            fear_worry_percent=fear_worry_percent,
            gold_score=2,
            instrument="antang-behavior-v1",
            assessed_at=NOW - timedelta(days=3),
        )
    )
    await session.flush()


async def test_behaviors_are_split_into_avoidance_and_monitoring(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "behavior")
    for _ in range(4):
        record_behavior(
            db_session,
            user_id=user.id,
            kind="extra_check",
            occurred_at=NOW - timedelta(days=2),
        )
    record_behavior(
        db_session,
        user_id=user.id,
        kind="exercise_avoidance",
        count=2,
        occurred_at=NOW - timedelta(days=4),
    )
    await db_session.flush()

    summary = await read_fear_summary(db_session, user_id=user.id, days=28, now=NOW)

    assert summary.behavior_counts["extra_check"] == 4
    assert summary.behavior_counts["exercise_avoidance"] == 2
    assert summary.behavior_total == 6
    # 4 周窗口：4 次监测 / 4 周 = 1.0 次每周，2 次回避 / 4 周 = 0.5 次每周。
    assert summary.monitoring_weekly == pytest.approx(1.0)
    assert summary.avoidance_weekly == pytest.approx(0.5)


async def test_low_explicit_score_with_avoidance_is_flagged_as_implicit(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "implicit")
    await _assessment(db_session, user=user, fear_behavior_percent=20)
    for _ in range(12):
        record_behavior(
            db_session,
            user_id=user.id,
            kind="exercise_avoidance",
            occurred_at=NOW - timedelta(days=7),
        )
    await db_session.flush()

    summary = await read_fear_summary(db_session, user_id=user.id, days=28, now=NOW)

    assert summary.discrepancy == "implicit_higher"
    assert summary.assessment is not None
    assert summary.assessment_row is not None
    assert "嘴上说不怕" in summary.discrepancy_note


async def test_high_explicit_score_without_records_is_flagged_as_explicit(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "explicit")
    await _assessment(db_session, user=user, fear_behavior_percent=85)
    await db_session.flush()

    summary = await read_fear_summary(db_session, user_id=user.id, days=28, now=NOW)

    assert summary.discrepancy == "explicit_higher"
    assert summary.behavior_total == 0


async def test_no_assessment_and_no_behaviors_is_unknown(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "empty")
    await db_session.flush()

    summary = await read_fear_summary(db_session, user_id=user.id, days=28, now=NOW)

    assert summary.discrepancy == "unknown"
    assert summary.beliefs == ()


async def test_belief_events_become_a_node_and_track_resolution(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "belief")
    record_belief(
        db_session,
        user_id=user.id,
        belief_code="hypo_causes_coma",
        quote="我一想到夜里低血糖昏迷没人知道就睡不着",
        trigger_context="今晚一个人睡",
        fear_intensity=80,
        detected_at=NOW - timedelta(days=5),
    )
    await db_session.flush()
    first = await read_fear_summary(db_session, user_id=user.id, days=28, now=NOW)
    assert first.beliefs[0].mentions == 1
    assert first.beliefs[0].resolved is False

    record_belief(
        db_session,
        user_id=user.id,
        belief_code="hypo_causes_coma",
        quote="其实我现在会先测一次再睡，好像没那么容易出事",
        occurrence="reframed",
        detected_at=NOW - timedelta(days=1),
    )
    await db_session.flush()
    second = await read_fear_summary(db_session, user_id=user.id, days=28, now=NOW)

    assert second.beliefs[0].reframes == 1
    assert second.beliefs[0].resolved is True
    assert second.beliefs[0].label == "低血糖会让我昏迷、没人发现"


async def test_behavior_context_is_empty_without_records(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "empty-context")
    summary = await read_fear_summary(db_session, user_id=user.id, days=28, now=NOW)
    assert render_behavior_context(summary) == ""


async def test_behavior_context_names_beliefs_and_forbids_reporting_counts(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "context")
    await _assessment(
        db_session,
        user=user,
        fear_behavior_percent=10,
        fear_worry_percent=12,
    )
    record_behavior(
        db_session,
        user_id=user.id,
        kind="exercise_avoidance",
        occurred_at=NOW - timedelta(days=2),
    )
    record_behavior(
        db_session,
        user_id=user.id,
        kind="exercise_avoidance",
        occurred_at=NOW - timedelta(days=1),
    )
    record_belief(
        db_session,
        user_id=user.id,
        belief_code="hypo_causes_coma",
        quote="我怕运动完半夜低血糖昏迷",
        detected_at=NOW - timedelta(days=3),
    )
    await db_session.flush()

    context = render_behavior_context(
        await read_fear_summary(db_session, user_id=user.id, days=7, now=NOW),
    )

    assert "运动回避 2 次" in context
    assert "低血糖会让我昏迷、没人发现" in context
    # 两路证据不一致时要提示，但不能让模型把次数复述给用户。
    assert "两路证据不一致" in context
    assert "不要在回复里报这些次数" in context


async def test_beliefs_and_behaviors_do_not_leak_between_users(
    db_session: AsyncSession,
) -> None:
    owner = await _user(db_session, "owner")
    other = await _user(db_session, "other")
    record_belief(
        db_session,
        user_id=other.id,
        belief_code="hypo_causes_death",
        quote="我怕会死",
        detected_at=NOW - timedelta(days=1),
    )
    record_behavior(
        db_session,
        user_id=other.id,
        kind="comfort_meal",
        occurred_at=NOW - timedelta(days=1),
    )
    await db_session.flush()

    summary = await read_fear_summary(db_session, user_id=owner.id, days=28, now=NOW)

    assert summary.beliefs == ()
    assert summary.behavior_total == 0


async def test_deleted_behavior_disappears_for_the_patient(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "deletes")
    log = record_behavior(
        db_session,
        user_id=user.id,
        kind="night_check",
        occurred_at=NOW - timedelta(hours=6),
    )
    await db_session.flush()

    assert await delete_behavior_log(db_session, user_id=user.id, log_id=log.id)
    await db_session.flush()

    assert await read_behavior_logs(db_session, user_id=user.id, now=NOW) == []
    summary = await read_fear_summary(db_session, user_id=user.id, now=NOW)
    assert summary.behavior_total == 0


async def test_deleted_behavior_stays_visible_to_the_clinician(
    db_session: AsyncSession,
) -> None:
    # 患者删掉只影响自己的界面；医护端要留着，并标出「患者已删除」。
    user = await _user(db_session, "clinic_keeps")
    log = record_behavior(
        db_session,
        user_id=user.id,
        kind="insulin_change",
        occurred_at=NOW - timedelta(hours=2),
        note="自己从 8 加到 12",
    )
    await db_session.flush()
    assert await delete_behavior_log(db_session, user_id=user.id, log_id=log.id)
    await db_session.flush()

    rows = await read_behavior_logs(
        db_session, user_id=user.id, now=NOW, include_deleted=True
    )

    assert [row.id for row in rows] == [log.id]
    assert rows[0].deleted_at is not None


async def test_deleting_twice_is_a_not_found(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "twice")
    log = record_behavior(
        db_session, user_id=user.id, kind="extra_check", occurred_at=NOW
    )
    await db_session.flush()

    assert await delete_behavior_log(db_session, user_id=user.id, log_id=log.id)
    await db_session.flush()
    assert not await delete_behavior_log(db_session, user_id=user.id, log_id=log.id)


def test_behavior_request_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        FearBehaviorRequest.model_validate({"kind": "because_i_worry"})


def test_behavior_request_rejects_zero_count() -> None:
    with pytest.raises(ValidationError):
        FearBehaviorRequest.model_validate({"kind": "extra_check", "count": 0})


def test_belief_request_rejects_unknown_code() -> None:
    with pytest.raises(ValidationError):
        BeliefRequest.model_validate(
            {"belief_code": "hypo_is_bad", "quote": "我怕低血糖"}
        )


def test_belief_request_rejects_empty_quote() -> None:
    with pytest.raises(ValidationError):
        BeliefRequest.model_validate({"belief_code": "hypo_is_fault", "quote": ""})


def test_the_belief_tool_takes_flat_arguments() -> None:
    """模型在嵌套 request 上犹豫过，改成平铺参数后它才愿意调。"""

    behavior_tool, belief_tool = build_behavior_tools()

    assert isinstance(belief_tool, StructuredTool)
    assert set(belief_tool.args) == {
        "belief_code",
        "quote",
        "occurrence",
        "trigger_context",
        "fear_intensity",
    }
    assert isinstance(behavior_tool, StructuredTool)
    assert set(behavior_tool.args) == {"kind", "count", "occurred_at", "note"}

def _summary_with(mentions: int) -> FearSummary:
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    return FearSummary(
        window_start=now - timedelta(days=28),
        window_end=now,
        behavior_counts={"extra_check": 3},
        behavior_total=3,
        monitoring_weekly=0.75,
        avoidance_weekly=0.0,
        beliefs=(
            BeliefNode(
                belief_code="hypo_causes_coma",
                label="低血糖会让我昏迷、没人发现",
                mentions=mentions,
                reframes=0,
                denials=0,
                latest_quote="总怕半夜低血糖昏过去没人知道",
                latest_detected_at=now,
            ),
        ),
        discrepancy="unknown",
        discrepancy_note="",
        assessment=None,
        assessment_row=None,
    )


def test_repeated_belief_comes_with_one_question() -> None:
    rendered = render_behavior_context(_summary_with(3))

    assert "[可以试着这样问]" in rendered
    assert "夜里一个人睡" in rendered
    assert rendered.count("先只问一句") == 1


def test_single_mention_does_not_trigger_a_question() -> None:
    rendered = render_behavior_context(_summary_with(1))

    assert "[可以试着这样问]" not in rendered
