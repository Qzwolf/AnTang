"""每个工具都必须有活动阶段配置，否则聊天流会在工具开始执行时整条失败。"""

from antang_api.chat import TOOL_ACTIVITY_PHASES
from antang_api.tools import (
    build_behavior_tools,
    build_care_plan_tool,
    build_glucose_read_tool,
    build_glucose_report_tool,
    build_glucose_write_tool,
    build_knowledge_search_tool,
    build_record_tools,
    build_wearable_read_tool,
)


def test_every_registered_tool_has_an_activity_phase() -> None:
    """线上真的挂过：加了写血糖的工具、忘了配阶段，患者一发血糖就「回复失败」。"""

    tools = [
        *build_behavior_tools(),
        build_care_plan_tool(),
        build_glucose_read_tool(),
        build_glucose_report_tool(),
        build_glucose_write_tool(),
        build_knowledge_search_tool(),
        *build_record_tools(),
        build_wearable_read_tool(),
    ]

    missing = sorted(
        tool.name for tool in tools if tool.name not in TOOL_ACTIVITY_PHASES
    )
    assert missing == []


def test_the_tools_that_need_a_model_or_network_are_covered_too() -> None:
    """联网、委派健康档案这两个工具没法在这里构造，名字固定，单独核对。"""

    for name in ("web_search", "web_fetch", "delegate_health_profile"):
        assert TOOL_ACTIVITY_PHASES[name] in ("searching", "reading", "organizing")
