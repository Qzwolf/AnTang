"""血糖读数进模型上下文的那一段。

只用纯函数与数据库读出两条路径验证：模型拿到什么、拿到的时候带了什么口径。
不测模型怎么回答——那属于红队。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.core import render_core_system_prompt
from antang_api.health_profile.glucose import (
    GlucoseSummary,
    list_glucose_readings,
    render_glucose_context,
    summarize_glucose,
    to_mmol,
)
from antang_api.models import GlucoseReading, User
from antang_api.tools.glucose import READ_GLUCOSE_TOOL_NAME, build_glucose_read_tool


async def create_user(db_session: AsyncSession, prefix: str) -> User:
    username = f"{prefix}_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def add_reading(
    db_session: AsyncSession,
    user_id,
    *,
    value_mg_dl: int,
    hours_ago: int,
    context: str = "other",
) -> GlucoseReading:
    reading = GlucoseReading(
        user_id=user_id,
        value_mg_dl=value_mg_dl,
        measured_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        context=context,
        source="manual",
    )
    db_session.add(reading)
    await db_session.flush()
    return reading


def test_mmol_conversion_keeps_one_decimal() -> None:
    assert to_mmol(90) == "5.0"
    assert to_mmol(58) == "3.2"


def test_render_says_what_was_measured_and_where_it_came_from() -> None:
    reading = GlucoseReading(
        user_id=uuid4(),
        value_mg_dl=58,
        measured_at=datetime(2026, 10, 21, 3, 0, tzinfo=UTC),
        context="night",
        source="manual",
    )
    rendered = render_glucose_context(
        days=7,
        summary=GlucoseSummary(
            days=7,
            count=1,
            average_mg_dl=58.0,
            minimum_mg_dl=58,
            maximum_mg_dl=58,
            below_70_count=1,
        ),
        recent=[reading],
    )

    assert "近 7 天 1 次" in rendered
    assert "10-21 03:00" in rendered
    assert "3.2" in rendered
    assert "夜间" in rendered
    assert "低于 3.9 的 1 次" in rendered
    # 口径必须跟着数据一起进提示词，否则模型会把它当成连续监测。
    assert "不是连续监测" in rendered


def test_no_readings_means_no_context_block() -> None:
    rendered = render_glucose_context(
        days=7,
        summary=GlucoseSummary(
            days=7,
            count=0,
            average_mg_dl=None,
            minimum_mg_dl=None,
            maximum_mg_dl=None,
            below_70_count=0,
        ),
        recent=[],
    )

    assert rendered == ""


def test_core_prompt_carries_the_glucose_block() -> None:
    without = render_core_system_prompt("档案")
    with_glucose = render_core_system_prompt(
        "档案", glucose_context="[血糖记录：患者自己记的]"
    )

    assert "[血糖记录：" not in without
    assert with_glucose.endswith("[血糖记录：患者自己记的]")


async def test_a_patient_reading_reaches_the_summary(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "glucose_ctx")
    await add_reading(db_session, user.id, value_mg_dl=58, hours_ago=5, context="night")
    await add_reading(
        db_session, user.id, value_mg_dl=162, hours_ago=2, context="post_meal"
    )

    summary = await summarize_glucose(db_session, user_id=user.id, days=7)
    recent = await list_glucose_readings(db_session, user_id=user.id, days=7, limit=5)
    rendered = render_glucose_context(days=7, summary=summary, recent=recent)

    assert summary["count"] == 2
    assert summary["below_70_count"] == 1
    assert [reading.value_mg_dl for reading in recent] == [162, 58]
    assert "近 7 天 2 次" in rendered
    assert "餐后" in rendered


async def test_a_revoked_reading_stays_out_of_the_context(
    db_session: AsyncSession,
) -> None:
    user = await create_user(db_session, "glucose_revoked")
    reading = await add_reading(db_session, user.id, value_mg_dl=58, hours_ago=3)
    reading.revoked_at = datetime.now(UTC)
    await db_session.flush()

    summary = await summarize_glucose(db_session, user_id=user.id, days=7)

    assert summary["count"] == 0


def test_the_read_tool_is_named_for_the_activity_map() -> None:
    tool = build_glucose_read_tool()

    assert tool.name == READ_GLUCOSE_TOOL_NAME
