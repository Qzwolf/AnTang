"""「用图决定说什么」：挑话题的规则。

真正要守住的是「挑不出来就不挑」——从图上硬拽一条出来，主动消息就会变成
一句奇怪的考古。
"""

from __future__ import annotations

from antang_api.psychosocial.graphs import (
    GraphFocus,
    choose_focus,
    render_graph_focus,
)

_THOUGHT = {
    "id": "n1",
    "layer": "cognitive",
    "kind": "thought",
    "text": "晚上喝牛奶会胖",
    "intensity": None,
}
_FEELING = {
    "id": "n2",
    "layer": "affective",
    "kind": "emotion",
    "text": "自习室太冷，一个人有点发慌",
    "intensity": 4,
}


def _snapshot(*nodes: dict[str, object]) -> dict[str, object]:
    return {"nodes": list(nodes), "edges": []}


def test_a_feeling_beats_a_thought() -> None:
    # 强度高的情绪比一句念头更值得接着问。
    focus = choose_focus(_snapshot(_THOUGHT, _FEELING))

    assert focus is not None
    assert focus.node_id == "n2"
    assert focus.intensity == 4


def test_a_topic_already_mentioned_recently_is_skipped() -> None:
    focus = choose_focus(
        _snapshot(_THOUGHT, _FEELING),
        mentioned=("自习室冷得我有点发慌",),
    )

    assert focus is not None
    assert focus.node_id == "n1"


def test_nothing_left_to_talk_about_means_no_focus() -> None:
    assert choose_focus(_snapshot()) is None
    assert (
        choose_focus(_snapshot(_THOUGHT), mentioned=("晚上喝牛奶会胖",)) is None
    )


def test_the_focus_never_quotes_him_back() -> None:
    rendered = render_graph_focus(
        GraphFocus(
            node_id="n2", layer="affective", kind="emotion", text="发慌", intensity=4
        )
    )

    assert "[他最近提过的一件心事]" in rendered
    assert "情绪：发慌，强度 4" in rendered
    assert "不要引用他的原话" in rendered
    assert "你上次说过" in rendered
    assert render_graph_focus(None) == ""
