from collections.abc import Sequence
from typing import Any, Self, cast
from uuid import uuid4

import pytest
from langchain.agents.middleware import AgentState
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime

from antang_api.agents.core import (
    SYSTEM_PROMPT,
    build_core_agent,
    render_core_system_prompt,
    runtime_system_prompt,
)
from antang_api.agents.runtime import AgentContext


class ToolLoopFakeModel(FakeMessagesListChatModel):
    """让 create_agent 可以给测试模型绑定工具。"""

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Self:
        del tools, tool_choice, kwargs
        return self


@tool(description="返回传入的测试值。")
async def echo_tool(value: str) -> dict[str, str]:
    return {"value": value}


async def test_core_agent_executes_and_checkpoints_complete_tool_loop() -> None:
    model = ToolLoopFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo_tool",
                        "args": {"value": "ok"},
                        "id": "tool-call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="工具执行完成。"),
        ]
    )
    checkpointer = InMemorySaver()
    agent = build_core_agent(
        model,
        checkpointer,
        tools=(echo_tool,),
        middleware=(),
    )
    run_id = uuid4()
    user_id = uuid4()
    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}

    final_state = await agent.ainvoke(
        {"messages": [HumanMessage(content="执行工具")]},
        config=config,
        context=AgentContext(
            user_id=user_id,
            run_id=run_id,
            input_message_count=1,
            rendered_system_prompt=SYSTEM_PROMPT,
        ),
    )
    checkpoint = await checkpointer.aget(config)

    assert [type(message) for message in final_state["messages"]] == [
        HumanMessage,
        AIMessage,
        ToolMessage,
        AIMessage,
    ]
    assert checkpoint is not None
    checkpoint_messages = checkpoint["channel_values"]["messages"]
    assert [type(message) for message in checkpoint_messages] == [
        HumanMessage,
        AIMessage,
        ToolMessage,
        AIMessage,
    ]
    assert checkpoint_messages[2].tool_call_id == "tool-call-1"
    assert checkpoint_messages[-1].text == "工具执行完成。"


async def test_core_model_uses_the_same_dynamic_prompt_that_was_counted() -> None:
    rendered_prompt = render_core_system_prompt('{"occupation":"学生"}')
    context = AgentContext(
        user_id=uuid4(),
        run_id=uuid4(),
        input_message_count=1,
        rendered_system_prompt=rendered_prompt,
    )
    messages: list[AnyMessage] = [HumanMessage(content="你好")]
    runtime: Runtime[AgentContext | None] = Runtime(context=context)
    request: ModelRequest[AgentContext | None] = ModelRequest(
        model=ToolLoopFakeModel(responses=[AIMessage(content="你好")]),
        messages=messages,
        tools=[],
        state=cast("AgentState[Any]", {"messages": messages}),
        runtime=runtime,
    )

    async def handler(
        modified_request: ModelRequest[AgentContext | None],
    ) -> ModelResponse[Any]:
        assert modified_request.system_prompt == rendered_prompt
        return ModelResponse(result=[AIMessage(content="你好")])

    await runtime_system_prompt.awrap_model_call(request, handler)


def test_core_prompt_marks_companion_memory_as_non_authoritative() -> None:
    rendered_prompt = render_core_system_prompt(
        '{"occupation":"学生"}',
        "- 用户曾说自己害怕夜间低血糖",
    )

    assert "[相关陪伴记忆]" in rendered_prompt
    assert "可能不完整、过时或错误" in rendered_prompt
    assert "以当前表达和健康档案为准" in rendered_prompt
    assert "用户曾说自己害怕夜间低血糖" in rendered_prompt


def test_core_prompt_exposes_current_plan_ids_for_later_changes() -> None:
    plan_id = str(uuid4())
    rendered_prompt = render_core_system_prompt(
        '{"occupation":"学生"}',
        care_plan_context=(
            f'[{{"plan_id":"{plan_id}","summary":"晚饭后散步",'
            '"follow_up_at":"2026-07-20T12:00:00+00:00","revision":1}]'
        ),
    )

    assert "[当前主动关怀计划]" in rendered_prompt
    assert plan_id in rendered_prompt
    assert "晚饭后散步" in rendered_prompt


def test_core_prompt_interprets_short_replies_from_the_latest_question() -> None:
    assert "最近一个明确问题" in SYSTEM_PROMPT
    assert "不要额外猜测原因" in SYSTEM_PROMPT


def test_core_prompt_allows_one_time_reminders_with_relative_times() -> None:
    assert "一次性提醒或计划回访" in SYSTEM_PROMPT
    assert "相对时间是有效时间" in SYSTEM_PROMPT
    assert "不支持的普通倒计时或提醒" not in SYSTEM_PROMPT


def test_core_prompt_handles_over_correction_without_blame() -> None:
    assert "低血糖后的强烈饥饿是升糖激素在推" in SYSTEM_PROMPT
    assert "“低→猛补→反弹高”这个循环" in SYSTEM_PROMPT
    assert "不要夸“补得对”" in SYSTEM_PROMPT


def test_core_prompt_checks_denial_instead_of_agreeing() -> None:
    assert "不要附和这句话就直接收尾" in SYSTEM_PROMPT
    assert "这是核对，不是揭穿" in SYSTEM_PROMPT
    assert "说明你心态好" in SYSTEM_PROMPT
    assert "不要用“大家”“很多人”这种泛指" in SYSTEM_PROMPT


def test_core_prompt_does_not_turn_a_reassurance_check_into_a_reason() -> None:
    assert "不要用“距离上次测已经有一阵子”当理由松口" in SYSTEM_PROMPT
    assert "“睡前再确认一次就踏实了”" in SYSTEM_PROMPT


def test_core_prompt_records_beliefs_and_fear_behaviors_instead_of_only_noticing_them() -> None:
    # 线上 akaa 说「我怕我会像他们一样」，回复接住了情绪，但心理那张表一条都没落。
    # 根因是提示词从头到尾没有要求调这两个工具——模型把它当成可选的背景判断。
    assert "心理这一侧要落库" in SYSTEM_PROMPT
    assert "记这条信念" in SYSTEM_PROMPT
    assert "记这次行为" in SYSTEM_PROMPT
    assert "不要从语气里推断" in SYSTEM_PROMPT
    assert "不要报次数" in SYSTEM_PROMPT


def test_core_prompt_keeps_calls_and_repetition_out_of_the_reply() -> None:
    assert "调用语法绝对不能出现在正文里" in SYSTEM_PROMPT
    assert "`to=`" in SYSTEM_PROMPT
    assert "一次回复只讲一遍" in SYSTEM_PROMPT
    assert "不要把同一段内容重复两三次" in SYSTEM_PROMPT
    assert "不要写给自己看的批注、推演或草稿" in SYSTEM_PROMPT
    assert "不要用 Markdown 记号" in SYSTEM_PROMPT
    assert "必须至少原样复制一个" in SYSTEM_PROMPT


async def test_core_model_rejects_missing_dynamic_prompt() -> None:
    messages: list[AnyMessage] = [HumanMessage(content="你好")]
    request: ModelRequest[AgentContext | None] = ModelRequest(
        model=ToolLoopFakeModel(responses=[AIMessage(content="你好")]),
        messages=messages,
        tools=[],
        state=cast("AgentState[Any]", {"messages": messages}),
        runtime=Runtime(
            context=AgentContext(
                user_id=uuid4(),
                run_id=uuid4(),
                input_message_count=1,
            )
        ),
    )

    async def handler(
        _request: ModelRequest[AgentContext | None],
    ) -> ModelResponse[Any]:
        raise AssertionError("缺少完整提示词时不能调用模型")

    with pytest.raises(RuntimeError, match="缺少完整系统提示词"):
        await runtime_system_prompt.awrap_model_call(request, handler)


def test_core_prompt_includes_a_live_heart_rate_reading_when_given() -> None:
    rendered_prompt = render_core_system_prompt(
        '{"occupation":"学生"}',
        care_plan_context="[]",
        live_heart_rate_context=(
            '{"beats_per_minute": 89, "measured_at": "2026-07-26T22:00:00+08:00",'
            ' "device_name": "HUAWEI Band 9-598"}'
        ),
    )

    assert "[实时心率读数]" in rendered_prompt
    assert "只在本次回复有效" in rendered_prompt
    assert '"beats_per_minute": 89' in rendered_prompt
    assert rendered_prompt.index("[当前健康档案]") < rendered_prompt.index("[实时心率读数]")
    assert rendered_prompt.index("[实时心率读数]") < rendered_prompt.index("[当前主动关怀计划]")


def test_core_prompt_omits_the_live_heart_rate_section_without_a_reading() -> None:
    rendered_prompt = render_core_system_prompt('{"occupation":"学生"}')

    assert "[实时心率读数]" not in rendered_prompt


def test_core_prompt_only_calls_a_provided_reading_real_time() -> None:
    assert "只有当本轮上下文明确给出了「实时心率读数」时，才可以把它称为实时" in SYSTEM_PROMPT
    assert "不能当成趋势、平均值或一段时间的心率" in SYSTEM_PROMPT
    assert "设备结果都是服务器已经同步的历史观测" in SYSTEM_PROMPT
