"""报告里的血糖要能留痕。

线上真发生过：患者把 CGM 报告 PDF 发给糖糖，糖糖把数字念了一遍、说得挺好，
患者回到「记录 → 血糖」却是空的——因为模型手里只有读血糖的工具，没有写血糖的。
这组用例盯的就是「念一遍」和「写进库」之间的区别。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Self, cast
from uuid import UUID, uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.tools.structured import StructuredTool
from langchain.tools import ToolRuntime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.runtime import AgentContext
from antang_api.chat import prepare_chat_run
from antang_api.health_profile.glucose import list_glucose_reports, render_glucose_reports
from antang_api.models import (
    GlucoseReading,
    GlucoseReport,
    MessageAttachment,
    MessageAttachmentKind,
    PersonalProfile,
    ProactiveCareSettings,
    User,
)
from antang_api.tools.glucose import (
    RECORD_GLUCOSE_REPORT_TOOL_NAME,
    RECORD_GLUCOSE_TOOL_NAME,
    Recheck,
    build_glucose_report_tool,
    build_glucose_write_tool,
)
from antang_api.health_profile.report_parse import ParsedReport
from antang_api.health_profile.report_verify import (
    UnreadableScreenshot,
    compare_reads,
)


class _KeepOpen:
    """工具里只用到 `async with sessions() as session`，测试给的是当前事务。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *_exc: object) -> None:
        return None


async def _create_user(session: AsyncSession, prefix: str) -> User:
    username = f"{prefix}_{uuid4().hex[:12]}"
    user = User(
        username=username,
        username_normalized=username,
        password_hash="test-only-password-hash",
    )
    session.add(user)
    await session.flush()
    session.add_all(
        [
            PersonalProfile(user_id=user.id),
            ProactiveCareSettings(user_id=user.id),
        ]
    )
    await session.flush()
    return user


def _runtime(user_id: UUID) -> ToolRuntime[AgentContext]:
    return ToolRuntime(
        state={"messages": []},
        context=AgentContext(
            user_id=user_id,
            run_id=uuid4(),
            input_message_count=1,
        ),
        config={},
        stream_writer=lambda _value: None,
        tool_call_id="glucose-call",
        store=None,
        tools=[],
    )


def _write_tool(session: AsyncSession) -> Callable[..., Awaitable[dict[str, object]]]:
    def sessions() -> _KeepOpen:
        return _KeepOpen(session)

    tool = build_glucose_write_tool(sessions=cast(Any, sessions))
    assert isinstance(tool, StructuredTool)
    assert tool.coroutine is not None
    return cast("Callable[..., Awaitable[dict[str, object]]]", tool.coroutine)


async def _rows(session: AsyncSession, user_id: UUID) -> list[GlucoseReading]:
    result = await session.execute(
        select(GlucoseReading)
        .where(GlucoseReading.user_id == user_id)
        .order_by(GlucoseReading.measured_at)
    )
    return list(result.scalars())


def test_the_write_tool_is_flat_and_named_for_the_logs() -> None:
    """平铺参数：嵌套成 `request` 的那种写法线上已经翻过车。"""

    tool = build_glucose_write_tool()

    assert tool.name == RECORD_GLUCOSE_TOOL_NAME
    assert set(tool.args) == {"readings", "source", "device"}


async def test_a_report_writes_every_point_it_read(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, "glucose_report")
    measured = datetime.now(UTC) - timedelta(hours=3)

    result = await _write_tool(db_session)(
        readings=[
            {
                "measured_at": measured.isoformat(),
                "value_mmol": 6.8,
                "context": "post_meal",
                "note": "报告日均",
            },
            {
                "measured_at": (measured + timedelta(hours=1)).isoformat(),
                "value_mmol": 3.6,
                "context": "night",
            },
        ],
        source="cgm",
        runtime=_runtime(user.id),
    )

    assert result["recorded"] is True
    assert result["count"] == 2
    rows = await _rows(db_session, user.id)
    assert [row.value_mg_dl for row in rows] == [122, 65]
    assert {row.source for row in rows} == {"cgm"}
    assert rows[0].note == "报告日均"
    assert rows[1].context == "night"


async def test_a_point_without_a_timezone_is_dropped(
    db_session: AsyncSession,
) -> None:
    """没有时区的时间读不出「是哪一刻」，宁可少记，不要记错。"""

    user = await _create_user(db_session, "glucose_naive")
    now = datetime.now(UTC)

    result = await _write_tool(db_session)(
        readings=[
            {"measured_at": now.replace(tzinfo=None).isoformat(), "value_mmol": 7.1},
            {"measured_at": now.isoformat(), "value_mmol": 5.4},
        ],
        source="cgm",
        runtime=_runtime(user.id),
    )

    assert result["count"] == 1
    assert result["skipped"] == 1
    assert [row.value_mg_dl for row in await _rows(db_session, user.id)] == [97]


async def test_the_same_point_read_twice_is_written_once(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, "glucose_dupe")
    measured = datetime.now(UTC) - timedelta(hours=2)
    point = {"measured_at": measured.isoformat(), "value_mmol": 8.2}

    result = await _write_tool(db_session)(
        readings=[point, dict(point)],
        source="cgm",
        runtime=_runtime(user.id),
    )

    assert result["count"] == 1
    assert result["skipped"] == 1
    assert len(await _rows(db_session, user.id)) == 1


async def test_a_point_that_has_not_happened_yet_is_dropped(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, "glucose_future")

    result = await _write_tool(db_session)(
        readings=[
            {
                "measured_at": (datetime.now(UTC) + timedelta(hours=5)).isoformat(),
                "value_mmol": 9.0,
            }
        ],
        source="cgm",
        runtime=_runtime(user.id),
    )

    assert result["recorded"] is False
    assert await _rows(db_session, user.id) == []


async def test_a_lab_report_stays_a_lab_reading(db_session: AsyncSession) -> None:
    user = await _create_user(db_session, "glucose_lab")

    await _write_tool(db_session)(
        readings=[{"measured_at": datetime.now(UTC).isoformat(), "value_mmol": 5.9}],
        source="lab",
        device="roche",
        runtime=_runtime(user.id),
    )

    rows = await _rows(db_session, user.id)
    assert rows[0].source == "lab"
    # 化验单没有「哪台血糖仪」，存了会变成自相矛盾的记录。
    assert rows[0].source_device == ""


def test_a_point_needs_a_plausible_value() -> None:
    from antang_api.tools.glucose import GlucosePoint

    with pytest.raises(ValueError):
        GlucosePoint(measured_at=datetime.now(UTC).isoformat(), value_mmol=900)


class _ToolLoopFakeModel(FakeMessagesListChatModel):
    """让 create_agent 能给它绑定工具。"""

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Self:
        del tools, tool_choice, kwargs
        return self


async def test_the_agent_loop_can_write_a_report(db_session: AsyncSession) -> None:
    """走一遍 Agent 的图：模型按 schema 传的 nested 列表要真的落到 glucose_readings。

    记录工具就是在这一步翻的车（`**kwargs` 吃掉注入的 runtime），
    列表参数比平铺参数更容易被压坏，所以这条必须走 ToolNode 而不是直接调函数。
    """

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from antang_api.agents.core import SYSTEM_PROMPT, build_core_agent

    user = await _create_user(db_session, "glucose_agent")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "这是我 10 月的报告")
    await db_session.commit()

    tool = build_glucose_write_tool(
        sessions=cast(Any, lambda: _KeepOpen(db_session))
    )
    measured = datetime.now(UTC) - timedelta(hours=6)
    model = _ToolLoopFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": RECORD_GLUCOSE_TOOL_NAME,
                        "args": {
                            "readings": [
                                {
                                    "measured_at": measured.isoformat(),
                                    "value_mmol": 4.1,
                                    "context": "fasting",
                                    "note": "报告均值",
                                }
                            ],
                            "source": "cgm",
                        },
                        "id": "glucose-call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="已经记进血糖记录，记录页能看到。"),
        ]
    )
    agent = build_core_agent(model, InMemorySaver(), tools=(tool,), middleware=())
    state = await agent.ainvoke(
        {"messages": [HumanMessage(content="这是我 10 月的报告")]},
        config={"configurable": {"thread_id": str(prepared_run.run_id)}},
        context=AgentContext(
            user_id=user.id,
            run_id=prepared_run.run_id,
            input_message_count=1,
            rendered_system_prompt=SYSTEM_PROMPT,
        ),
    )

    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].status != "error", str(tool_messages[0].content)

    rows = await _rows(db_session, user.id)
    assert [row.value_mg_dl for row in rows] == [74]
    assert rows[0].source == "cgm"


async def _no_risk_read(_attachment: MessageAttachment) -> dict[str, object]:
    """只读「低血糖风险」的第二遍读数：这次什么也没读到。"""

    return {}


async def _no_second_read(_attachment: MessageAttachment) -> dict[str, object]:
    """默认的第二遍读数：这些用例里不该被调用（没有附件，或附件是模板认得的 PDF）。"""

    raise AssertionError("这条用例不该走第二遍读数")


def _report_tool(
    session: AsyncSession,
    *,
    recheck: Recheck | None = None,
) -> Callable[..., Awaitable[dict[str, object]]]:
    def sessions() -> _KeepOpen:
        return _KeepOpen(session)

    tool = build_glucose_report_tool(
        sessions=cast(Any, sessions),
        recheck=recheck or _no_second_read,
    )
    assert isinstance(tool, StructuredTool)
    assert tool.coroutine is not None
    return cast("Callable[..., Awaitable[dict[str, object]]]", tool.coroutine)


def test_the_report_tool_is_flat_too() -> None:
    tool = build_glucose_report_tool()

    assert tool.name == RECORD_GLUCOSE_REPORT_TOOL_NAME
    assert set(tool.args) == {
        "period_start",
        "period_end",
        "device",
        "device_serial",
        "report_generated_at",
        "mean_mmol",
        "sd_mmol",
        "cv_percent",
        "ehba1c_percent",
        "tir_percent",
        "tar_percent",
        "tbr_percent",
        "normal_percent",
        "low_percent",
        "very_low_percent",
        "high_percent",
        "very_high_percent",
        "lbgi_level",
        "note",
    }


async def test_a_report_summary_is_kept_whole(db_session: AsyncSession) -> None:
    """汇总指标要整份留着：这是报告自己的数字，不是我们算的曲线。"""

    user = await _create_user(db_session, "glucose_summary")

    result = await _report_tool(db_session)(
        period_start="2024-05-30",
        period_end="2024-06-11",
        device="硅基动感Pro",
        mean_mmol=6.29,
        sd_mmol=1.46,
        cv_percent=23.27,
        ehba1c_percent=5.73,
        tir_percent=84.5,
        tar_percent=15.3,
        tbr_percent=0.2,
        note="天津医科大学总医院",
        runtime=_runtime(user.id),
    )

    assert result["recorded"] is True
    assert result["period"] == "2024-05-30~2024-06-11"

    reports = await list_glucose_reports(db_session, user_id=user.id)
    assert len(reports) == 1
    assert reports[0].device == "硅基动感Pro"
    assert float(cast("float", reports[0].tir_percent)) == 84.5
    assert float(cast("float", reports[0].ehba1c_percent)) == 5.73
    assert reports[0].note == "天津医科大学总医院"


async def test_a_report_without_any_metric_is_refused(
    db_session: AsyncSession,
) -> None:
    """读不出指标就别存空壳：空表比假数据更让人误会。"""

    user = await _create_user(db_session, "glucose_empty")

    with pytest.raises(ValueError, match="一个指标都没给"):
        await _report_tool(db_session)(
            period_start="2024-05-30",
            period_end="2024-06-11",
            runtime=_runtime(user.id),
        )

    assert await list_glucose_reports(db_session, user_id=user.id) == []


async def test_a_report_ending_before_it_starts_is_refused(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, "glucose_order")

    with pytest.raises(ValueError, match="结束日不能早于开始日"):
        await _report_tool(db_session)(
            period_start="2024-06-11",
            period_end="2024-05-30",
            mean_mmol=6.3,
            runtime=_runtime(user.id),
        )


def test_a_report_summary_reaches_the_model_as_context() -> None:
    """报告存下来之后，提示词里要看得见，否则每轮都得让患者再报一遍。"""

    report = GlucoseReport(
        user_id=uuid4(),
        period_start=date(2024, 5, 30),
        period_end=date(2024, 6, 11),
        device="硅基动感Pro",
        mean_mmol=6.29,
        tir_percent=84.5,
        ehba1c_percent=5.73,
    )

    text = render_glucose_reports([report])

    assert "2024-05-30 至 2024-06-11" in text
    assert "TIR 84.5%" in text
    assert "不是我们算的" in text


async def test_five_bands_fill_in_the_three_totals(
    db_session: AsyncSession,
) -> None:
    """报告按五档给占比时，三档的 TAR/TBR 由我们自己加，别让模型算。"""

    user = await _create_user(db_session, "glucose_bands")

    result = await _report_tool(db_session)(
        period_start="2025-07-28",
        period_end="2025-07-31",
        device="硅基动感Pro",
        device_serial="LT2410FE3P",
        report_generated_at="2025-07-31",
        normal_percent=99.6,
        low_percent=0.4,
        very_low_percent=0.0,
        high_percent=0.0,
        very_high_percent=0.0,
        lbgi_level="high",
        runtime=_runtime(user.id),
    )

    assert result["recorded"] is True

    reports = await list_glucose_reports(db_session, user_id=user.id)
    assert len(reports) == 1
    report = reports[0]
    assert float(cast("float", report.tir_percent)) == 99.6
    assert float(cast("float", report.tar_percent)) == 0.0
    assert float(cast("float", report.tbr_percent)) == 0.4
    assert float(cast("float", report.very_low_percent)) == 0.0
    assert report.lbgi_level == "high"
    assert report.device_serial == "LT2410FE3P"
    assert report.report_generated_at == date(2025, 7, 31)

    text = render_glucose_reports(reports)
    assert "分档 正常 99.6%" in text
    assert "偏低血糖风险高" in text
    assert "序列号 LT2410FE3P" in text


async def test_a_percentage_is_bounded_in_the_schema() -> None:
    """占比超过 100% 就是读错了：schema 里要把上下界写死，模型才不容易传错。"""

    tool = build_glucose_report_tool()

    properties = json.dumps(tool.args["tir_percent"], ensure_ascii=False)
    assert "maximum" in properties
    assert "100" in properties


def _runtime_of(user_id: UUID, run_id: UUID) -> ToolRuntime[AgentContext]:
    return ToolRuntime(
        state={"messages": []},
        context=AgentContext(
            user_id=user_id,
            run_id=run_id,
            input_message_count=1,
        ),
        config={},
        stream_writer=lambda _value: None,
        tool_call_id="report-call",
        store=None,
        tools=[],
    )


async def test_the_report_remembers_which_pdf_it_came_from(
    db_session: AsyncSession,
) -> None:
    """患者发的那份 PDF 要记下来：详情页的「打开原报告」靠这一列才有得开。"""

    async def agreeing(_attachment: MessageAttachment) -> dict[str, object]:
        return {
            "period_start": "2024-05-30",
            "period_end": "2024-06-11",
            "mean_mmol": 6.29,
            "tir_percent": 84.5,
        }

    user = await _create_user(db_session, "glucose_source")
    attachment = MessageAttachment(
        user_id=user.id,
        kind=MessageAttachmentKind.REPORT,
        filename="cgm-report.pdf",
        mime_type="application/pdf",
        size_bytes=8,
        data=b"%PDF-1.4",
    )
    db_session.add(attachment)
    await db_session.flush()
    prepared_run = await prepare_chat_run(
        db_session,
        user.id,
        uuid4(),
        "帮我分析这份 cgm 报告",
        attachment_id=attachment.id,
    )
    await db_session.commit()

    result = await _report_tool(db_session, recheck=agreeing)(
        period_start="2024-05-30",
        period_end="2024-06-11",
        mean_mmol=6.29,
        tir_percent=84.5,
        runtime=_runtime_of(user.id, prepared_run.run_id),
    )

    assert result["recorded"] is True
    reports = await list_glucose_reports(db_session, user_id=user.id)
    assert [report.source_attachment_id for report in reports] == [attachment.id]


async def test_a_report_typed_in_by_hand_has_no_source_file(
    db_session: AsyncSession,
) -> None:
    """患者自己口述数字、手里没有文件时，别指一个不存在的附件。"""

    user = await _create_user(db_session, "glucose_typing")
    prepared_run = await prepare_chat_run(
        db_session, user.id, uuid4(), "我的报告 TIR 是 84.5%"
    )
    await db_session.commit()

    await _report_tool(db_session)(
        period_start="2024-05-30",
        period_end="2024-06-11",
        tir_percent=84.5,
        runtime=_runtime_of(user.id, prepared_run.run_id),
    )

    reports = await list_glucose_reports(db_session, user_id=user.id)
    assert [report.source_attachment_id for report in reports] == [None]


async def _screenshot_run(
    session: AsyncSession,
    prefix: str,
    *,
    mime_type: str = "image/jpeg",
) -> tuple[User, MessageAttachment, UUID]:
    user = await _create_user(session, prefix)
    attachment = MessageAttachment(
        user_id=user.id,
        kind=MessageAttachmentKind.REPORT,
        filename="report.jpg",
        mime_type=mime_type,
        size_bytes=8,
        data=b"\xff\xd8\xff\xe0",
    )
    session.add(attachment)
    await session.flush()
    prepared_run = await prepare_chat_run(
        session,
        user.id,
        uuid4(),
        "帮我看看这张报告",
        attachment_id=attachment.id,
    )
    await session.commit()
    return user, attachment, prepared_run.run_id


_PARSED = ParsedReport(
    period_start=date(2025, 7, 28),
    period_end=date(2025, 7, 31),
    generated_at=date(2025, 7, 31),
    device="硅基动感Pro",
    device_serial="LT2410FE3P",
    hospital="示例医院",
    mean_mmol=5.81,
    sd_mmol=0.9,
    cv_percent=15.53,
    ehba1c_percent=5.34,
    normal_percent=99.6,
    low_percent=0.4,
)


async def test_the_pdf_template_wins_over_what_the_model_read(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    """模板认得的报告，存哪几个数由程序决定，模型读的那遍不参与。"""

    monkeypatch.setattr(
        "antang_api.tools.glucose.parse_report_pdf", lambda _data: _PARSED
    )
    user, attachment, run_id = await _screenshot_run(
        db_session, "glucose_template", mime_type="application/pdf"
    )

    result = await _report_tool(db_session, recheck=_no_risk_read)(
        # 模型读的是另一组数（连区间都不一样），不该被采用。
        period_start="2024-05-30",
        period_end="2024-06-11",
        mean_mmol=6.29,
        tir_percent=84.5,
        runtime=_runtime_of(user.id, run_id),
    )

    assert result["verified"] == "pdf-template"
    reports = await list_glucose_reports(db_session, user_id=user.id)
    assert len(reports) == 1
    kept = reports[0]
    assert kept.period_start == date(2025, 7, 28)
    assert kept.period_end == date(2025, 7, 31)
    assert float(cast(Any, kept.mean_mmol)) == 5.81
    assert float(cast(Any, kept.normal_percent)) == 99.6
    # 三档由五档相加得出（系统算，不靠模型），所以 TIR 跟着 normal 走；
    # 「低血糖风险」这类文字层看不出选中项的东西一律留空。
    assert float(cast(Any, kept.tir_percent)) == 99.6
    assert kept.lbgi_level == ""
    assert kept.source_attachment_id == attachment.id


async def test_a_screenshot_read_the_same_way_twice_is_kept(
    db_session: AsyncSession,
) -> None:
    """截图没有文字层：模型读一遍、服务端再读一遍，两遍一致才存。"""

    async def agreeing(_attachment: MessageAttachment) -> dict[str, object]:
        return {
            "period_start": "2025-07-28",
            "period_end": "2025-07-31",
            "device": "硅基动感Pro",
            "mean_mmol": 5.81,
            "tir_percent": 99.6,
        }

    user, _attachment, run_id = await _screenshot_run(db_session, "glucose_agree")

    result = await _report_tool(db_session, recheck=agreeing)(
        period_start="2025-07-28",
        period_end="2025-07-31",
        device="硅基动感Pro",
        mean_mmol=5.81,
        tir_percent=99.6,
        runtime=_runtime_of(user.id, run_id),
    )

    assert result["verified"] == "cross-checked"
    reports = await list_glucose_reports(db_session, user_id=user.id)
    assert [float(cast(Any, report.mean_mmol)) for report in reports] == [5.81]


async def test_a_screenshot_that_reads_differently_is_not_kept(
    db_session: AsyncSession,
) -> None:
    """两遍读出来的数不一样，宁可这条不存——记录里不能有错的数。"""

    async def disagreeing(_attachment: MessageAttachment) -> dict[str, object]:
        return {
            "period_start": "2025-07-28",
            "period_end": "2025-07-31",
            "mean_mmol": 5.18,
        }

    user, _attachment, run_id = await _screenshot_run(db_session, "glucose_disagree")

    with pytest.raises(ValueError, match="读了两遍"):
        await _report_tool(db_session, recheck=disagreeing)(
            period_start="2025-07-28",
            period_end="2025-07-31",
            mean_mmol=5.81,
            runtime=_runtime_of(user.id, run_id),
        )

    assert await list_glucose_reports(db_session, user_id=user.id) == []


async def test_a_screenshot_that_cannot_be_read_twice_is_not_kept(
    db_session: AsyncSession,
) -> None:
    async def broken(_attachment: MessageAttachment) -> dict[str, object]:
        raise UnreadableScreenshot("第二遍没读到")

    user, _attachment, run_id = await _screenshot_run(db_session, "glucose_unreadable")

    with pytest.raises(ValueError, match="没读准"):
        await _report_tool(db_session, recheck=broken)(
            period_start="2025-07-28",
            period_end="2025-07-31",
            mean_mmol=5.81,
            runtime=_runtime_of(user.id, run_id),
        )

    assert await list_glucose_reports(db_session, user_id=user.id) == []


def test_two_reads_only_disagree_about_what_the_first_one_claimed() -> None:
    first = {"mean_mmol": 5.81, "device": "硅基动感Pro", "tir_percent": None}
    assert compare_reads(first, {"mean_mmol": 5.82, "device": "硅基动感Pro"}) == []
    mismatched = compare_reads(first, {"mean_mmol": 5.81, "device": "雅培"})
    assert [item.field_name for item in mismatched] == ["device"]
    # 第二遍没读出来的项也算对不上：只有一遍支持的数字不写进记录。
    assert [item.field_name for item in compare_reads(first, {})] == [
        "device",
        "mean_mmol",
    ]


def _risk_reader(*levels: object) -> Recheck:
    """按顺序返回每一遍读到的风险档：第一次给第一个，第二次给第二个。"""

    remaining = list(levels)

    async def read(_attachment: MessageAttachment) -> dict[str, object]:
        return {"lbgi_level": remaining.pop(0) if remaining else None}

    return read


async def _template_run(
    session: AsyncSession, prefix: str
) -> tuple[User, UUID]:
    """一份模板认得的 PDF 报告 + 一次运行，给「低血糖风险」这几条用例共用。"""

    user, _attachment, run_id = await _screenshot_run(
        session, prefix, mime_type="application/pdf"
    )
    return user, run_id


async def test_a_risk_level_is_kept_when_two_reads_agree(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    """这一档文字层读不出，只能读两遍；两遍说同一档才留。"""

    monkeypatch.setattr(
        "antang_api.tools.glucose.parse_report_pdf", lambda _data: _PARSED
    )
    user, run_id = await _template_run(db_session, "g_risk_agree")

    result = await _report_tool(db_session, recheck=_risk_reader("高", "高"))(
        period_start="2025-07-28",
        period_end="2025-07-31",
        runtime=_runtime_of(user.id, run_id),
    )

    assert result["verified"] == "pdf-template"
    reports = await list_glucose_reports(db_session, user_id=user.id)
    assert [report.lbgi_level for report in reports] == ["high"]
    # 模板解析出来的数照旧是程序给的，风险档是读两遍补上去的。
    assert float(cast(Any, reports[0].mean_mmol)) == 5.81
    assert "两遍一致" in str(result["note"])


async def test_a_risk_level_that_reads_differently_is_left_blank(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    """两遍读成不同的档位，就留空——标错风险比不标更糟。"""

    monkeypatch.setattr(
        "antang_api.tools.glucose.parse_report_pdf", lambda _data: _PARSED
    )
    user, run_id = await _template_run(db_session, "g_risk_diff")

    result = await _report_tool(db_session, recheck=_risk_reader("高", "最低"))(
        period_start="2025-07-28",
        period_end="2025-07-31",
        runtime=_runtime_of(user.id, run_id),
    )

    assert result["verified"] == "pdf-template"
    reports = await list_glucose_reports(db_session, user_id=user.id)
    assert [report.lbgi_level for report in reports] == [""]
    assert "两遍一致" not in str(result["note"])


async def test_a_risk_level_nobody_can_read_is_left_blank(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    """读不出风险档不该把整份报告退回去：报告本身是模板读的，仍然可信。"""

    monkeypatch.setattr(
        "antang_api.tools.glucose.parse_report_pdf", lambda _data: _PARSED
    )
    user, run_id = await _template_run(db_session, "g_risk_unread")

    async def broken(_attachment: MessageAttachment) -> dict[str, object]:
        raise UnreadableScreenshot("第二遍没读到")

    result = await _report_tool(db_session, recheck=broken)(
        period_start="2025-07-28",
        period_end="2025-07-31",
        runtime=_runtime_of(user.id, run_id),
    )

    assert result["recorded"] is True
    reports = await list_glucose_reports(db_session, user_id=user.id)
    assert [report.lbgi_level for report in reports] == [""]

