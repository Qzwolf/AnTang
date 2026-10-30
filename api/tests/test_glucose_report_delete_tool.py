"""删报告速览的工具：患者同意是前提，删到什么程度要有边界。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from langchain.tools import ToolRuntime
from langchain_core.tools.structured import StructuredTool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from antang_api.agents.runtime import AgentContext
from antang_api.chat import prepare_chat_run
from antang_api.models import (
    GlucoseReport,
    MessageAttachment,
    MessageAttachmentKind,
    PersonalProfile,
    ProactiveCareSettings,
    User,
)
from antang_api.tools.glucose import build_glucose_report_delete_tool


class _Sessions:
    """工具只拿到 session 工厂，这里让它一直用测试里的那个 session。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _Sessions:
        return self

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
        tool_call_id="delete-call",
        store=None,
        tools=[],
    )


def _delete_tool(
    session: AsyncSession,
) -> Callable[..., Awaitable[dict[str, object]]]:
    tool = build_glucose_report_delete_tool(cast(Any, _Sessions(session)))
    assert isinstance(tool, StructuredTool)
    return cast(Callable[..., Awaitable[dict[str, object]]], tool.coroutine)


async def _report_of(
    session: AsyncSession,
    user: User,
    *,
    attachment: MessageAttachment | None = None,
    start: date = date(2025, 7, 28),
    end: date = date(2025, 7, 31),
) -> GlucoseReport:
    report = GlucoseReport(
        user_id=user.id,
        period_start=start,
        period_end=end,
        device="硅基动感Pro",
        source_attachment_id=None if attachment is None else attachment.id,
    )
    session.add(report)
    await session.flush()
    return report


async def _reports(session: AsyncSession, user_id: UUID) -> list[GlucoseReport]:
    return list(
        (
            await session.scalars(
                select(GlucoseReport).where(GlucoseReport.user_id == user_id)
            )
        ).all()
    )


async def test_a_report_the_patient_said_to_delete_is_deleted(
    db_session: AsyncSession,
) -> None:
    """「别人的，删了吧」这句话本身就是同意：记录和那份导入的 PDF 一起删。"""

    user = await _create_user(db_session, "delete_ok")
    attachment = MessageAttachment(
        user_id=user.id,
        kind=MessageAttachmentKind.REPORT,
        filename="13.pdf",
        mime_type="application/pdf",
        size_bytes=8,
        data=b"%PDF-1.4",
    )
    db_session.add(attachment)
    await db_session.flush()
    await _report_of(db_session, user, attachment=attachment)
    prepared_run = await prepare_chat_run(
        db_session, user.id, uuid4(), "别人的，删了吧"
    )
    await db_session.commit()

    result = await _delete_tool(db_session)(
        runtime=_runtime_of(user.id, prepared_run.run_id),
        period_start="2025-07-28",
        period_end="2025-07-31",
        consent_quote="别人的，删了吧",
    )

    assert result["deleted"] is True
    assert result["file_removed"] is True
    assert await _reports(db_session, user.id) == []
    gone = await db_session.get(MessageAttachment, attachment.id)
    assert gone is None


async def test_without_the_patients_own_words_nothing_is_deleted(
    db_session: AsyncSession,
) -> None:
    """模型不能替患者点头：原话对不上就不删，并让他先去问。"""

    user = await _create_user(db_session, "delete_no")
    await _report_of(db_session, user)
    prepared_run = await prepare_chat_run(
        db_session, user.id, uuid4(), "这份报告是别人的吗"
    )
    await db_session.commit()

    with pytest.raises(ValueError, match="患者自己点头"):
        await _delete_tool(db_session)(
            runtime=_runtime_of(user.id, prepared_run.run_id),
            period_start="2025-07-28",
            period_end="2025-07-31",
            consent_quote="好，删吧",
        )

    assert len(await _reports(db_session, user.id)) == 1


async def test_the_wrong_period_is_refused_instead_of_guessed(
    db_session: AsyncSession,
) -> None:
    """区间对不上就问清楚，不能顺手删掉别的报告。"""

    user = await _create_user(db_session, "delete_period")
    await _report_of(db_session, user)
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "删了吧")
    await db_session.commit()

    with pytest.raises(ValueError, match="记录里没有"):
        await _delete_tool(db_session)(
            runtime=_runtime_of(user.id, prepared_run.run_id),
            period_start="2025-06-01",
            period_end="2025-06-14",
            consent_quote="删了吧",
        )

    assert len(await _reports(db_session, user.id)) == 1


async def test_a_file_shared_in_the_chat_stays_after_the_summary_goes(
    db_session: AsyncSession,
) -> None:
    """聊天里发的那份是患者自己说的话，不替他抹掉；只删我们存的摘要。"""

    user = await _create_user(db_session, "delete_chat")
    prepared_run = await prepare_chat_run(
        db_session, user.id, uuid4(), "这份是别人的，删了吧"
    )
    attachment = MessageAttachment(
        user_id=user.id,
        message_id=prepared_run.user_message_id,
        kind=MessageAttachmentKind.REPORT,
        filename="13.pdf",
        mime_type="application/pdf",
        size_bytes=8,
        data=b"%PDF-1.4",
    )
    db_session.add(attachment)
    await db_session.flush()
    await _report_of(db_session, user, attachment=attachment)
    await db_session.commit()

    result = await _delete_tool(db_session)(
        runtime=_runtime_of(user.id, prepared_run.run_id),
        period_start="2025-07-28",
        period_end="2025-07-31",
        consent_quote="这份是别人的，删了吧",
    )

    assert result["deleted"] is True
    assert result["file_removed"] is False
    assert await db_session.get(MessageAttachment, attachment.id) is not None


async def test_someone_elses_report_is_out_of_reach(db_session: AsyncSession) -> None:
    """区间撞上了也不行：删除范围只能是自己名下的记录。"""

    other = await _create_user(db_session, "delete_other")
    await _report_of(db_session, other)
    user = await _create_user(db_session, "delete_self")
    prepared_run = await prepare_chat_run(db_session, user.id, uuid4(), "删了吧")
    await db_session.commit()

    with pytest.raises(ValueError, match="记录里没有"):
        await _delete_tool(db_session)(
            runtime=_runtime_of(user.id, prepared_run.run_id),
            period_start="2025-07-28",
            period_end="2025-07-31",
            consent_quote="删了吧",
        )

    assert len(await _reports(db_session, other.id)) == 1


def test_the_delete_tool_takes_flat_arguments() -> None:
    """嵌套 request 那一层模型会绕开；删报告的参数也要平铺。"""

    tool = build_glucose_report_delete_tool()
    assert set(tool.args) == {"period_start", "period_end", "consent_quote"}
