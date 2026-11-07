"""图怎么进上下文：只放最近几条，并且写清「不是给用户看的」。"""

from antang_api.psychosocial.graphs import (
    CASE_GRAPH_CONTEXT_LIMIT,
    render_case_graph_context,
)


def _snapshot(count: int) -> dict[str, object]:
    return {
        "nodes": [
            {
                "id": f"node-{index}",
                "layer": "cognitive" if index % 2 == 0 else "affective",
                "kind": "thought" if index % 2 == 0 else "emotion",
                "text": f"第{index}条",
                "intensity": None if index % 2 == 0 else 3,
            }
            for index in range(count)
        ],
        "edges": [],
    }


def test_nothing_recorded_means_no_block_at_all() -> None:
    assert render_case_graph_context({"nodes": [], "edges": []}) == ""


def test_only_the_recent_lines_go_into_the_prompt() -> None:
    text = render_case_graph_context(_snapshot(CASE_GRAPH_CONTEXT_LIMIT + 4))

    assert "第0条" not in text
    assert "第15条" in text
    assert text.count("\n") == CASE_GRAPH_CONTEXT_LIMIT + 1


def test_the_block_says_it_is_not_for_the_patient_to_hear() -> None:
    text = render_case_graph_context(_snapshot(2))

    assert text.startswith("[他之前说过的念头和情绪]")
    assert "不要念给他听" in text
    assert "不要当成诊断结论" in text
