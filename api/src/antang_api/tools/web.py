import json
import math
import re
from collections.abc import Sequence
from typing import Annotated, Any, TypedDict
from urllib.parse import urlsplit

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import Field
from tavily import AsyncTavilyClient

from antang_api.agents.runtime import AgentContext, ToolResponseError


class WebToolError(ToolResponseError):
    """Tavily 返回了无法交给模型使用的结果。"""


# 「这一页读不出来」是网站的常态（要登录、反爬、超时），不是我们内部出错：
# 让模型知道就好，不要因此把患者的整条回复判失败。
PAGE_UNREADABLE = "Tavily 无法提取请求的网页"


class SearchResult(TypedDict):
    title: str
    url: str
    snippet: str
    score: float


class SearchOutput(TypedDict):
    results: list[SearchResult]


class FetchOutput(TypedDict):
    source_id: str
    title: str
    url: str
    content: str


class ProviderUsage(TypedDict):
    credits: int | float


class ProviderMetadata(TypedDict):
    request_id: str
    response_time: float
    usage: ProviderUsage


class WebToolArtifact(TypedDict):
    provider_metadata: ProviderMetadata


MAX_QUERY_LENGTH = 400
WebQuery = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_QUERY_LENGTH,
        description="公开网页检索词，不能包含不必要的用户个人或健康信息。",
    ),
]
WebFetchUrl = Annotated[
    str,
    Field(
        description=(
            "要读取的完整 http(s) URL。必须原样复制用户明确给出的 URL，"
            "或先前 web_search 结果中的 url，不能改写、解码或删减。"
        ),
    ),
]
_URL_PATTERN = re.compile(r"https?://[^\s<>\"'，。！？；：、]+")
_URL_SENTENCE_ENDINGS = ".,!?;:，。！？；：、"
_URL_BRACKETS = (
    ("(", ")"),
    ("[", "]"),
    ("{", "}"),
    ("（", "）"),
    ("［", "］"),
    ("｛", "｝"),
)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WebToolError(f"Tavily 响应缺少有效的 {field}")

    return value.strip()


def _query(value: object) -> str:
    query = _required_text(value, "query")
    if len(query) > MAX_QUERY_LENGTH:
        raise WebToolError(f"query 不能超过 {MAX_QUERY_LENGTH} 个字符")
    return query


def _http_url(value: object, field: str = "url") -> str:
    url = _required_text(value, field)
    parsed = urlsplit(url)

    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise WebToolError(f"{field} 必须是有效的 http(s) URL")

    return url


def _url_from_user_text(candidate: str) -> str:
    """移除包住自然语言 URL 的句末标点，不改动 URL 内的成对括号。"""

    url = candidate.rstrip(_URL_SENTENCE_ENDINGS)
    for opening, closing in _URL_BRACKETS:
        while url.endswith(closing) and url.count(closing) > url.count(opening):
            url = url[:-1].rstrip(_URL_SENTENCE_ENDINGS)
    return _http_url(url)


def _response_results(response: object, endpoint: str) -> list[object]:
    if not isinstance(response, dict):
        raise WebToolError(f"Tavily {endpoint} 响应不是对象")

    results = response.get("results")

    if not isinstance(results, list):
        raise WebToolError(f"Tavily {endpoint} 响应缺少 results 数组")

    if not results:
        raise WebToolError(f"Tavily {endpoint} 没有返回结果")

    return results


def _non_negative_number(value: object, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise WebToolError(f"Tavily 响应缺少有效的 {field}")
    if not math.isfinite(value) or value < 0:
        raise WebToolError(f"Tavily 响应中的 {field} 必须是非负有限数值")
    return value


def _response_time(value: object) -> float:
    if isinstance(value, str):
        try:
            response_time = float(value)
        except ValueError as error:
            raise WebToolError("Tavily 响应缺少有效的 response_time") from error
    else:
        response_time = float(_non_negative_number(value, "response_time"))

    if not math.isfinite(response_time) or response_time < 0:
        raise WebToolError("Tavily 响应中的 response_time 必须是非负有限数值")
    return response_time


def _normalize_provider_artifact(response: object) -> WebToolArtifact:
    """提取仅供应用审计使用、不会发送给模型的 Tavily 元数据。"""

    if not isinstance(response, dict):
        raise WebToolError("Tavily 响应不是对象")

    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise WebToolError("Tavily 响应缺少有效的 usage")
    if "credits" not in usage:
        raise WebToolError("Tavily usage 缺少 credits")

    return {
        "provider_metadata": {
            "request_id": _required_text(response.get("request_id"), "request_id"),
            "response_time": _response_time(response.get("response_time")),
            "usage": {
                "credits": _non_negative_number(usage.get("credits"), "usage.credits")
            },
        }
    }


def _normalize_search_response(
    response: object,
    max_snippet_chars: int,
) -> SearchOutput:
    normalized_results: list[SearchResult] = []

    for result in _response_results(response, "search"):
        if not isinstance(result, dict):
            raise WebToolError("Tavily search 的结果项不是对象")

        score = result.get("score")

        if isinstance(score, bool) or not isinstance(score, int | float):
            raise WebToolError("Tavily search 的结果项缺少有效的 score")

        snippet = _required_text(result.get("content"), "content")
        if len(snippet) > max_snippet_chars:
            raise WebToolError(
                f"Tavily search 返回的内容片段超过项目上限 {max_snippet_chars} 字符"
            )

        normalized_results.append(
            {
                "title": _required_text(result.get("title"), "title"),
                "url": _http_url(result.get("url")),
                "snippet": snippet,
                "score": float(score),
            }
        )

    if len(normalized_results) > 5:
        raise WebToolError("Tavily search 返回了超过五个结果")

    return {"results": normalized_results}


def _normalize_fetch_content(
    response: object,
    requested_url: str,
    max_content_chars: int,
) -> str:
    if not isinstance(response, dict):
        raise WebToolError("Tavily extract 响应不是对象")

    failed_results = response.get("failed_results")

    if not isinstance(failed_results, list):
        raise WebToolError("Tavily extract 响应缺少 failed_results 数组")

    if failed_results:
        raise WebToolError(PAGE_UNREADABLE)

    results = _response_results(response, "extract")

    if len(results) != 1 or not isinstance(results[0], dict):
        raise WebToolError("Tavily extract 必须只返回一个网页结果")

    result: dict[str, Any] = results[0]

    returned_url = _http_url(result.get("url"))
    if returned_url != requested_url:
        raise WebToolError("Tavily extract 返回的 URL 与请求 URL 不一致")

    content = _required_text(result.get("raw_content"), "raw_content")
    if len(content) > max_content_chars:
        raise WebToolError(
            f"Tavily extract 返回的内容超过项目上限 {max_content_chars} 字符"
        )
    return content


def _runtime_messages(
    runtime: ToolRuntime[AgentContext],
) -> tuple[BaseMessage, ...]:
    state = runtime.state
    if not isinstance(state, dict):
        raise WebToolError("工具运行状态不是对象")

    messages = state.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes):
        raise WebToolError("工具运行状态缺少 messages")
    if not all(isinstance(message, BaseMessage) for message in messages):
        raise WebToolError("工具运行状态包含无效消息")

    return tuple(messages)


def _current_model_turn(
    messages: Sequence[BaseMessage],
    input_message_count: int,
    tool_call_id: str,
) -> int:
    if input_message_count > len(messages):
        raise WebToolError("input_message_count 超过工具运行状态中的消息数量")

    matching_indexes: list[int] = []
    for message_index, message in enumerate(
        messages[input_message_count:],
        start=input_message_count,
    ):
        if not isinstance(message, AIMessage):
            continue
        if any(call.get("id") == tool_call_id for call in message.tool_calls):
            matching_indexes.append(message_index)

    if len(matching_indexes) != 1:
        raise WebToolError("工具运行状态中找不到唯一的当前工具调用")
    return matching_indexes[0]


def _fetch_source_id(
    messages: Sequence[BaseMessage],
    input_message_count: int,
    current_model_turn: int,
    tool_call_id: str,
) -> str:
    fetch_index = 0

    for message in messages[input_message_count : current_model_turn + 1]:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            if call.get("name") == "web_fetch":
                fetch_index += 1
            if call.get("id") == tool_call_id:
                if call.get("name") != "web_fetch":
                    raise WebToolError("当前工具调用不是 web_fetch")
                return f"S{fetch_index}"

    raise WebToolError("工具运行状态中找不到当前 web_fetch 调用")


def _normalized_search_tool_content(message: ToolMessage) -> SearchOutput:
    if message.status == "error" or not isinstance(message.content, str):
        raise WebToolError("先前的 web_search 没有有效结果")

    try:
        content = json.loads(message.content)
    except json.JSONDecodeError as error:
        raise WebToolError("先前的 web_search 结果不是有效 JSON") from error

    if not isinstance(content, dict) or set(content) != {"results"}:
        raise WebToolError("先前的 web_search 结果结构无效")

    results = content.get("results")
    if not isinstance(results, list) or not results:
        raise WebToolError("先前的 web_search 结果为空")

    normalized: list[SearchResult] = []
    for result in results:
        if not isinstance(result, dict) or set(result) != {
            "title",
            "url",
            "snippet",
            "score",
        }:
            raise WebToolError("先前的 web_search 结果项结构无效")
        score = result.get("score")
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise WebToolError("先前的 web_search 结果项缺少有效 score")
        normalized.append(
            {
                "title": _required_text(result.get("title"), "title"),
                "url": _http_url(result.get("url")),
                "snippet": _required_text(result.get("snippet"), "snippet"),
                "score": float(score),
            }
        )

    return {"results": normalized}


def _prior_search_sources(
    messages: Sequence[BaseMessage],
    input_message_count: int,
    current_model_turn: int,
) -> dict[str, str]:
    """读取当前调用之前已完成的搜索结果；同一并行轮不算先前结果。"""

    prior_messages = messages[input_message_count:current_model_turn]
    ordered_search_call_ids: list[str] = []
    for message in prior_messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            call_id = call.get("id")
            if call.get("name") == "web_search" and isinstance(call_id, str):
                ordered_search_call_ids.append(call_id)
    tool_messages = {
        message.tool_call_id: message
        for message in prior_messages
        if isinstance(message, ToolMessage)
    }

    sources: dict[str, str] = {}
    for call_id in ordered_search_call_ids:
        tool_message = tool_messages.get(call_id)
        if tool_message is None:
            continue
        for result in _normalized_search_tool_content(tool_message)["results"]:
            sources.setdefault(result["url"], result["title"])

    return sources


def _human_message_urls(
    messages: Sequence[BaseMessage],
    input_message_count: int,
) -> set[str]:
    urls: set[str] = set()

    for message in messages[:input_message_count]:
        if not isinstance(message, HumanMessage):
            continue
        if message.additional_kwargs.get("lc_source") is not None:
            continue
        if not isinstance(message.content, str):
            raise WebToolError("真实用户消息必须是文本")
        urls.update(
            _url_from_user_text(match.group(0))
            for match in _URL_PATTERN.finditer(message.content)
        )

    return urls


def _resolve_fetch_source(
    runtime: ToolRuntime[AgentContext],
    requested_url: str,
) -> tuple[str, str]:
    messages = _runtime_messages(runtime)
    tool_call_id = runtime.tool_call_id
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise WebToolError("web_fetch 缺少 tool_call_id")

    current_model_turn = _current_model_turn(
        messages,
        runtime.context.input_message_count,
        tool_call_id,
    )
    source_id = _fetch_source_id(
        messages,
        runtime.context.input_message_count,
        current_model_turn,
        tool_call_id,
    )
    search_sources = _prior_search_sources(
        messages,
        runtime.context.input_message_count,
        current_model_turn,
    )

    if requested_url in search_sources:
        return source_id, search_sources[requested_url]
    if requested_url in _human_message_urls(
        messages,
        runtime.context.input_message_count,
    ):
        hostname = urlsplit(requested_url).hostname
        if hostname is None:
            raise WebToolError("用户提供的 URL 缺少 hostname")
        return source_id, hostname

    raise WebToolError("web_fetch 只能读取用户明确提供或先前 web_search 返回的 URL")


def build_web_tools(
    client: AsyncTavilyClient,
    *,
    search_max_snippet_chars: int = 2_000,
    fetch_max_content_chars: int = 10_000,
) -> tuple[BaseTool, BaseTool]:
    """创建联网搜索和单页提取工具。"""

    if search_max_snippet_chars < 1:
        raise ValueError("search_max_snippet_chars 必须大于 0")
    if fetch_max_content_chars < 1:
        raise ValueError("fetch_max_content_chars 必须大于 0")

    @tool("web_search", response_format="content_and_artifact")
    async def web_search(
        query: WebQuery,
        runtime: ToolRuntime[AgentContext],
    ) -> tuple[SearchOutput, WebToolArtifact]:
        """搜索公开网页，返回最多五个标题、URL 和相关内容片段。"""

        normalized_query = _query(query)
        response = await client.search(
            query=normalized_query,
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
        artifact = _normalize_provider_artifact(response)
        try:
            content = _normalize_search_response(response, search_max_snippet_chars)
        except WebToolError as error:
            raise WebToolError(str(error), artifact=artifact) from error
        return content, artifact

    @tool("web_fetch", response_format="content_and_artifact")
    async def web_fetch(
        url: WebFetchUrl,
        query: WebQuery,
        runtime: ToolRuntime[AgentContext],
    ) -> tuple[FetchOutput, WebToolArtifact]:
        """读取一个 http(s) 网页，按 query 返回最多三个相关 Markdown 片段。"""

        normalized_url = _http_url(url)
        normalized_query = _query(query)
        source_id, title = _resolve_fetch_source(runtime, normalized_url)
        response = await client.extract(
            urls=normalized_url,
            query=normalized_query,
            extract_depth="advanced",
            format="markdown",
            chunks_per_source=3,
            include_images=False,
            include_usage=True,
            timeout=30,
            session_id=str(runtime.context.run_id),
        )
        artifact = _normalize_provider_artifact(response)
        try:
            content = _normalize_fetch_content(
                response,
                normalized_url,
                fetch_max_content_chars,
            )
        except WebToolError as error:
            if str(error) != PAGE_UNREADABLE:
                raise WebToolError(str(error), artifact=artifact) from error
            return (
                {
                    "source_id": source_id,
                    "title": title,
                    "url": normalized_url,
                    "content": (
                        "这一页的内容打不开（可能要求登录、拒绝抓取，或者超时）。"
                        "不要猜里面的内容：跟患者说这一页读不了，可以换个链接或把关键段落复制过来。"
                    ),
                },
                artifact,
            )
        return (
            {
                "source_id": source_id,
                "title": title,
                "url": normalized_url,
                "content": content,
            },
            artifact,
        )

    return web_search, web_fetch
