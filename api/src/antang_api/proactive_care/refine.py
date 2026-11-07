"""把「第二稿」接到模型上：一段话进、一段话出。

`psychosocial/two_draft.py` 只负责比对数字，不碰模型；这里把模型包成它要的那个
`rewrite` 函数，让生成侧和校验侧分开，两边都能单独测。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from antang_api.psychosocial.two_draft import REFINE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

Refiner = Callable[[str], Awaitable[str]]


def build_wording_refiner(model: BaseChatModel) -> Refiner:
    """用同一把模型做第二稿：温度低一点，别让它发挥。"""

    async def rewrite(draft: str) -> str:
        reply = await model.ainvoke(
            [
                SystemMessage(content=REFINE_SYSTEM_PROMPT),
                HumanMessage(content=draft),
            ]
        )
        content = reply.content
        if isinstance(content, list):
            # 有些供应商回来的是分段内容；这里只拼文本段，忽略别的类型。
            content = "".join(
                part if isinstance(part, str) else str(part.get("text", ""))
                for part in content
            )
        return str(content)

    return rewrite


__all__ = ["Refiner", "build_wording_refiner"]
