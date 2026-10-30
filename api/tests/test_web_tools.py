import json
from collections.abc import Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.tools.structured import StructuredTool
from pydantic import BaseModel
from tavily import AsyncTavilyClient

from antang_api.agents.runtime import AgentContext
from antang_api.tools.web import WebToolError, build_web_tools


def make_runtime(
    *,
    messages: list[BaseMessage] | None = None,
    tool_call_id: str = "tool-call-id",
    input_message_count: int = 1,
) -> ToolRuntime[AgentContext]:
    return ToolRuntime(
        state={"messages": messages or []},
        context=AgentContext(
            user_id=uuid4(),
            run_id=uuid4(),
            input_message_count=input_message_count,
        ),
        config={},
        stream_writer=lambda _value: None,
        tool_call_id=tool_call_id,
        store=None,
        tools=[],
    )


def tool_coroutine(tool: BaseTool) -> Callable[..., Awaitable[object]]:
    assert isinstance(tool, StructuredTool)
    assert tool.coroutine is not None
    return cast("Callable[..., Awaitable[object]]", tool.coroutine)


def mock_client() -> AsyncMock:
    return AsyncMock(spec=AsyncTavilyClient)


def build_tools(client: AsyncMock) -> tuple[BaseTool, BaseTool]:
    return build_web_tools(cast("AsyncTavilyClient", client))


def model_properties(tool: BaseTool) -> dict[str, object]:
    schema = tool.tool_call_schema
    json_schema = (
        schema
        if isinstance(schema, dict)
        else cast("type[BaseModel]", schema).model_json_schema()
    )
    properties = json_schema.get("properties")
    assert isinstance(properties, dict)
    return properties


def provider_response(
    response: dict[str, object],
    *,
    request_id: str = "request-123",
    response_time: int | float | str = 0.25,
    credits: int | float = 2,
) -> dict[str, object]:
    return {
        **response,
        "request_id": request_id,
        "response_time": response_time,
        "usage": {"credits": credits},
    }


def search_tool_message(call_id: str, url: str, title: str) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(
            {
                "results": [
                    {
                        "title": title,
                        "url": url,
                        "snippet": "搜索摘要",
                        "score": 0.9,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        tool_call_id=call_id,
        name="web_search",
    )


def fetch_runtime(
    *,
    url: str,
    fetch_call_id: str = "fetch-call",
) -> ToolRuntime[AgentContext]:
    search_call_id = "search-call"
    return make_runtime(
        messages=[
            HumanMessage(content="请帮我查一下"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "测试"},
                        "id": search_call_id,
                        "type": "tool_call",
                    }
                ],
            ),
            search_tool_message(search_call_id, url, "来源标题"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_fetch",
                        "args": {"url": url, "query": "研究结论"},
                        "id": fetch_call_id,
                        "type": "tool_call",
                    }
                ],
            ),
        ],
        tool_call_id=fetch_call_id,
    )


def test_build_web_tools_exposes_only_model_arguments() -> None:
    search_tool, fetch_tool = build_tools(mock_client())

    assert search_tool.name == "web_search"
    assert fetch_tool.name == "web_fetch"
    search_properties = model_properties(search_tool)
    fetch_properties = model_properties(fetch_tool)

    assert set(search_properties) == {"query"}
    assert set(fetch_properties) == {"url", "query"}
    assert cast(dict[str, object], search_properties["query"])["maxLength"] == 400
    assert cast(dict[str, object], fetch_properties["query"])["maxLength"] == 400
    assert "原样复制" in str(
        cast(dict[str, object], fetch_properties["url"])["description"]
    )


async def test_web_search_uses_fixed_parameters_and_normalizes_results() -> None:
    client = mock_client()
    client.search.return_value = provider_response(
        {
            "results": [
                {
                    "title": "Tavily 文档",
                    "url": "https://docs.tavily.com/",
                    "content": "搜索接口说明",
                    "score": 0.91,
                    "raw_content": "不会进入规范化结果",
                }
            ]
        },
        response_time="1.67",
    )
    search_tool, _ = build_tools(client)
    runtime = make_runtime()

    content, artifact = cast(
        tuple[dict[str, object], dict[str, object]],
        await tool_coroutine(search_tool)(query="  Tavily API  ", runtime=runtime),
    )

    assert content == {
        "results": [
            {
                "title": "Tavily 文档",
                "url": "https://docs.tavily.com/",
                "snippet": "搜索接口说明",
                "score": 0.91,
            }
        ],
    }
    assert artifact == {
        "provider_metadata": {
            "request_id": "request-123",
            "response_time": 1.67,
            "usage": {"credits": 2},
        }
    }
    assert "request-123" not in json.dumps(content, ensure_ascii=False)
    client.search.assert_awaited_once_with(
        query="Tavily API",
        search_depth="advanced",
        topic="general",
        max_results=5,
        chunks_per_source=1,
        include_answer=False,
        include_raw_content=False,
        include_images=False,
        auto_parameters=False,
        include_usage=True,
        timeout=60,
        session_id=str(runtime.context.run_id),
    )


async def test_web_fetch_uses_fixed_parameters_and_normalizes_result() -> None:
    client = mock_client()
    client.extract.return_value = provider_response(
        {
            "results": [
                {
                    "url": "https://example.com/article",
                    "raw_content": "## 研究结论\n\n正文内容",
                    "images": [],
                }
            ],
            "failed_results": [],
        },
        credits=0,
    )
    _, fetch_tool = build_tools(client)
    runtime = fetch_runtime(url="https://example.com/article")

    content, artifact = cast(
        tuple[dict[str, object], dict[str, object]],
        await tool_coroutine(fetch_tool)(
            url=" https://example.com/article ",
            query=" 研究结论 ",
            runtime=runtime,
        ),
    )

    assert content == {
        "source_id": "S1",
        "title": "来源标题",
        "url": "https://example.com/article",
        "content": "## 研究结论\n\n正文内容",
    }
    assert artifact["provider_metadata"] == {
        "request_id": "request-123",
        "response_time": 0.25,
        "usage": {"credits": 0},
    }
    client.extract.assert_awaited_once_with(
        urls="https://example.com/article",
        query="研究结论",
        extract_depth="advanced",
        format="markdown",
        chunks_per_source=3,
        include_images=False,
        include_usage=True,
        timeout=30,
        session_id=str(runtime.context.run_id),
    )


@pytest.mark.parametrize(
    ("response", "error_text"),
    [
        (provider_response({"results": []}), "没有返回结果"),
        (
            provider_response(
                {
                    "results": [
                        {
                            "title": "标题",
                            "url": "https://example.com",
                            "content": "内容",
                        }
                    ]
                }
            ),
            "score",
        ),
    ],
)
async def test_web_search_rejects_empty_or_invalid_response(
    response: dict[str, object],
    error_text: str,
) -> None:
    client = mock_client()
    client.search.return_value = response
    search_tool, _ = build_tools(client)

    with pytest.raises(WebToolError, match=error_text):
        await tool_coroutine(search_tool)(query="测试", runtime=make_runtime())

    assert client.search.await_count == 1


async def test_web_search_rejects_snippet_over_project_limit() -> None:
    client = mock_client()
    client.search.return_value = provider_response(
        {
            "results": [
                {
                    "title": "标题",
                    "url": "https://example.com",
                    "content": "字" * 11,
                    "score": 0.8,
                }
            ]
        }
    )
    search_tool, _ = build_web_tools(
        cast("AsyncTavilyClient", client),
        search_max_snippet_chars=10,
    )

    with pytest.raises(WebToolError, match="项目上限 10") as caught:
        await tool_coroutine(search_tool)(query="测试", runtime=make_runtime())

    assert caught.value.artifact is not None
    assert client.search.await_count == 1


async def test_web_fetch_rejects_failed_or_non_http_result() -> None:
    client = mock_client()
    client.extract.return_value = provider_response(
        {
            "results": [],
            "failed_results": [{"url": "https://example.com"}],
        }
    )
    _, fetch_tool = build_tools(client)

    # 网页读不出来是网站的常态，不是内部错误：交回一句话，别让整条回复失败。
    result = await tool_coroutine(fetch_tool)(
        url="https://example.com",
        query="测试",
        runtime=fetch_runtime(url="https://example.com"),
    )
    assert isinstance(result, tuple)
    content, artifact = result

    assert "打不开" in content["content"]
    assert artifact is not None
    assert client.extract.await_count == 1

    with pytest.raises(WebToolError, match=r"http\(s\)"):
        await tool_coroutine(fetch_tool)(
            url="file:///etc/passwd",
            query="测试",
            runtime=make_runtime(),
        )

    assert client.extract.await_count == 1


async def test_web_fetch_rejects_content_over_project_limit_without_truncating() -> (
    None
):
    url = "https://example.com/large"
    client = mock_client()
    client.extract.return_value = provider_response(
        {
            "results": [{"url": url, "raw_content": "字" * 11}],
            "failed_results": [],
        }
    )
    _search_tool, fetch_tool = build_web_tools(
        cast("AsyncTavilyClient", client),
        fetch_max_content_chars=10,
    )

    with pytest.raises(WebToolError, match="项目上限 10") as caught:
        await tool_coroutine(fetch_tool)(
            url=url,
            query="测试",
            runtime=fetch_runtime(url=url),
        )

    assert caught.value.artifact is not None
    assert client.extract.await_count == 1


def test_build_web_tools_rejects_non_positive_fetch_limit() -> None:
    with pytest.raises(ValueError, match="必须大于 0"):
        build_web_tools(
            cast("AsyncTavilyClient", mock_client()),
            search_max_snippet_chars=0,
        )

    with pytest.raises(ValueError, match="必须大于 0"):
        build_web_tools(
            cast("AsyncTavilyClient", mock_client()),
            fetch_max_content_chars=0,
        )


async def test_web_tools_propagate_provider_failure_without_retry() -> None:
    client = mock_client()
    client.search.side_effect = RuntimeError("provider unavailable")
    search_tool, _ = build_tools(client)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await tool_coroutine(search_tool)(query="测试", runtime=make_runtime())

    assert client.search.await_count == 1


@pytest.mark.parametrize("query", ["   ", "查" * 401])
async def test_web_search_rejects_invalid_query_before_provider(query: str) -> None:
    client = mock_client()
    search_tool, _ = build_tools(client)

    with pytest.raises(WebToolError, match="query"):
        await tool_coroutine(search_tool)(query=query, runtime=make_runtime())

    client.search.assert_not_awaited()


@pytest.mark.parametrize(
    ("metadata", "error_text"),
    [
        (
            {"request_id": "request", "response_time": "slow", "usage": {"credits": 1}},
            "response_time",
        ),
        (
            {"request_id": "request", "response_time": -1, "usage": {"credits": 1}},
            "response_time",
        ),
        ({"request_id": "request", "response_time": 1, "usage": {}}, "credits"),
        ({"request_id": "", "response_time": 1, "usage": {"credits": 1}}, "request_id"),
    ],
)
async def test_web_search_rejects_invalid_provider_metadata_without_retry(
    metadata: dict[str, object],
    error_text: str,
) -> None:
    client = mock_client()
    client.search.return_value = {
        "results": [
            {
                "title": "标题",
                "url": "https://example.com",
                "content": "内容",
                "score": 0.8,
            }
        ],
        **metadata,
    }
    search_tool, _ = build_tools(client)

    with pytest.raises(WebToolError, match=error_text):
        await tool_coroutine(search_tool)(query="测试", runtime=make_runtime())

    assert client.search.await_count == 1


async def test_provider_usage_ignores_unknown_fields_in_normalized_artifact() -> None:
    client = mock_client()
    client.search.return_value = {
        "results": [
            {
                "title": "标题",
                "url": "https://example.com",
                "content": "内容",
                "score": 0.8,
            }
        ],
        "request_id": "request",
        "response_time": 0.1,
        "usage": {"credits": 2, "future_provider_field": 9},
    }
    search_tool, _ = build_tools(client)

    _content, artifact = cast(
        tuple[dict[str, object], dict[str, object]],
        await tool_coroutine(search_tool)(query="测试", runtime=make_runtime()),
    )

    assert artifact == {
        "provider_metadata": {
            "request_id": "request",
            "response_time": 0.1,
            "usage": {"credits": 2},
        }
    }


async def test_web_fetch_accepts_exact_user_url_and_uses_hostname_title() -> None:
    url = "https://example.com/user-page?part=1"
    fetch_call_id = "direct-fetch"
    runtime = make_runtime(
        messages=[
            HumanMessage(content=f"请读取这个页面 {url}"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_fetch",
                        "args": {"url": url, "query": "重点"},
                        "id": fetch_call_id,
                        "type": "tool_call",
                    }
                ],
            ),
        ],
        tool_call_id=fetch_call_id,
    )
    client = mock_client()
    client.extract.return_value = provider_response(
        {
            "results": [{"url": url, "raw_content": "页面内容"}],
            "failed_results": [],
        }
    )
    _, fetch_tool = build_tools(client)

    content, _artifact = cast(
        tuple[dict[str, object], dict[str, object]],
        await tool_coroutine(fetch_tool)(url=url, query="重点", runtime=runtime),
    )

    assert content["source_id"] == "S1"
    assert content["title"] == "example.com"


async def test_web_fetch_accepts_user_url_surrounded_by_sentence_punctuation() -> None:
    url = "https://example.com/user-page"
    fetch_call_id = "punctuated-fetch"
    runtime = make_runtime(
        messages=[
            HumanMessage(content=f"请读取这个页面（{url}）。"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_fetch",
                        "args": {"url": url, "query": "重点"},
                        "id": fetch_call_id,
                        "type": "tool_call",
                    }
                ],
            ),
        ],
        tool_call_id=fetch_call_id,
    )
    client = mock_client()
    client.extract.return_value = provider_response(
        {
            "results": [{"url": url, "raw_content": "页面内容"}],
            "failed_results": [],
        }
    )
    _, fetch_tool = build_tools(client)

    content, _artifact = cast(
        tuple[dict[str, object], dict[str, object]],
        await tool_coroutine(fetch_tool)(url=url, query="重点", runtime=runtime),
    )

    assert content["url"] == url
    client.extract.assert_awaited_once()


async def test_new_run_source_number_ignores_historical_web_result() -> None:
    url = "https://example.com/current"
    search_call_id = "current-search"
    fetch_call_id = "current-fetch"
    historical_messages: list[BaseMessage] = [
        HumanMessage(content="上一轮问题"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "web_fetch",
                    "args": {"url": "https://old.example.com", "query": "旧内容"},
                    "id": "old-fetch",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "historical_result": True,
                    "title": "旧来源",
                    "url": "https://old.example.com",
                    "content": "旧网页内容",
                    "notice": "当前回答需要重新读取",
                },
                ensure_ascii=False,
            ),
            tool_call_id="old-fetch",
            name="web_fetch",
            additional_kwargs={"lc_source": "historical_tool_result"},
        ),
        AIMessage(content="上一轮回答，没有旧来源编号。"),
        HumanMessage(content="这次请查新的资料"),
    ]
    runtime = make_runtime(
        messages=[
            *historical_messages,
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "新资料"},
                        "id": search_call_id,
                        "type": "tool_call",
                    }
                ],
            ),
            search_tool_message(search_call_id, url, "新来源"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_fetch",
                        "args": {"url": url, "query": "新结论"},
                        "id": fetch_call_id,
                        "type": "tool_call",
                    }
                ],
            ),
        ],
        tool_call_id=fetch_call_id,
        input_message_count=len(historical_messages),
    )
    client = mock_client()
    client.extract.return_value = provider_response(
        {
            "results": [{"url": url, "raw_content": "新网页内容"}],
            "failed_results": [],
        }
    )
    _, fetch_tool = build_tools(client)

    content, _artifact = cast(
        tuple[dict[str, object], dict[str, object]],
        await tool_coroutine(fetch_tool)(url=url, query="新结论", runtime=runtime),
    )

    assert content["source_id"] == "S1"


@pytest.mark.parametrize("summary_source", ["summarization", "conversation_compaction"])
async def test_web_fetch_rejects_url_found_only_in_synthetic_summary(
    summary_source: str,
) -> None:
    url = "https://example.com/summary-only"
    fetch_call_id = "summary-fetch"
    runtime = make_runtime(
        messages=[
            HumanMessage(
                content=f"摘要里有 {url}",
                additional_kwargs={"lc_source": summary_source},
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_fetch",
                        "args": {"url": url, "query": "重点"},
                        "id": fetch_call_id,
                        "type": "tool_call",
                    }
                ],
            ),
        ],
        tool_call_id=fetch_call_id,
    )
    client = mock_client()
    _, fetch_tool = build_tools(client)

    with pytest.raises(WebToolError, match="只能读取"):
        await tool_coroutine(fetch_tool)(url=url, query="重点", runtime=runtime)

    client.extract.assert_not_awaited()


async def test_web_fetch_rejects_guessed_url_and_same_round_search_result() -> None:
    guessed_url = "https://example.com/guessed"
    search_call_id = "parallel-search"
    fetch_call_id = "parallel-fetch"
    calls = [
        {
            "name": "web_search",
            "args": {"query": "测试"},
            "id": search_call_id,
            "type": "tool_call",
        },
        {
            "name": "web_fetch",
            "args": {"url": guessed_url, "query": "重点"},
            "id": fetch_call_id,
            "type": "tool_call",
        },
    ]
    runtime = make_runtime(
        messages=[
            HumanMessage(content="帮我查一下"),
            AIMessage(content="", tool_calls=calls),
        ],
        tool_call_id=fetch_call_id,
    )
    client = mock_client()
    _, fetch_tool = build_tools(client)

    with pytest.raises(WebToolError, match="只能读取"):
        await tool_coroutine(fetch_tool)(
            url=guessed_url,
            query="重点",
            runtime=runtime,
        )

    client.extract.assert_not_awaited()


async def test_web_fetch_source_ids_follow_model_order_across_failed_and_parallel_calls() -> (
    None
):
    first_url = "https://example.com/first"
    second_url = "https://example.com/second"
    search_call_id = "search"
    failed_fetch_id = "failed-fetch"
    first_fetch_id = "first-fetch"
    second_fetch_id = "second-fetch"
    messages: list[BaseMessage] = [
        HumanMessage(content="帮我查一下"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "web_search",
                    "args": {"query": "测试"},
                    "id": search_call_id,
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "results": [
                        {
                            "title": "来源一",
                            "url": first_url,
                            "snippet": "一",
                            "score": 0.9,
                        },
                        {
                            "title": "来源二",
                            "url": second_url,
                            "snippet": "二",
                            "score": 0.8,
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            tool_call_id=search_call_id,
            name="web_search",
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "web_fetch",
                    "args": {"url": first_url, "query": "失败"},
                    "id": failed_fetch_id,
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content="提取失败",
            tool_call_id=failed_fetch_id,
            name="web_fetch",
            status="error",
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "web_fetch",
                    "args": {"url": first_url, "query": "重点"},
                    "id": first_fetch_id,
                    "type": "tool_call",
                },
                {
                    "name": "web_fetch",
                    "args": {"url": second_url, "query": "重点"},
                    "id": second_fetch_id,
                    "type": "tool_call",
                },
            ],
        ),
    ]
    client = mock_client()
    client.extract.side_effect = [
        provider_response(
            {
                "results": [{"url": second_url, "raw_content": "第二页"}],
                "failed_results": [],
            },
            request_id="second-request",
        ),
        provider_response(
            {
                "results": [{"url": first_url, "raw_content": "第一页"}],
                "failed_results": [],
            },
            request_id="first-request",
        ),
    ]
    _, fetch_tool = build_tools(client)

    second_content, _ = cast(
        tuple[dict[str, object], dict[str, object]],
        await tool_coroutine(fetch_tool)(
            url=second_url,
            query="重点",
            runtime=make_runtime(
                messages=messages,
                tool_call_id=second_fetch_id,
            ),
        ),
    )
    first_content, _ = cast(
        tuple[dict[str, object], dict[str, object]],
        await tool_coroutine(fetch_tool)(
            url=first_url,
            query="重点",
            runtime=make_runtime(
                messages=messages,
                tool_call_id=first_fetch_id,
            ),
        ),
    )

    assert second_content["source_id"] == "S3"
    assert second_content["title"] == "来源二"
    assert first_content["source_id"] == "S2"
    assert first_content["title"] == "来源一"
