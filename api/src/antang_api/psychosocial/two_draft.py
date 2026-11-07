"""两阶段生成（Think Twice）的回归检查：第二稿不许改数字。

想法很简单：第一稿给内容与结构，第二稿只改措辞。数字、剂量、时间这些
事实一旦被「润色」掉，患者看到的就不是他自己说的那个数了。

提示词里写了「不许改数字」不等于模型真的不改，所以这里做成可以断言的
函数：把两稿里出现的数字都抽出来，集合必须相同。只比数字，不比句子——
这正是为了让第二稿还能自由改语气。

明确的边界（写在这里，免得以后误以为它管得更宽）：

- 只认阿拉伯数字与全角数字（`７.２` 归一成 `7.2`），中文数字（「两支」
  「三片」）不在比对范围内；模型改写中文数量词时这个检查不会报。
- 不判断数字有没有单位、单位对不对（`8 单位` 改成 `8 片` 不会被拦）。
- 不做医学判断：它只能证明「两稿说的一样」，不能证明「说的对」。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# 匹配阿拉伯数字（可带小数），不吞掉紧邻的单位或标点。
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def normalize(text: str) -> str:
    """全角转半角，并把连续的空白压成一个空格。"""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _canonical(token: str) -> str:
    """`7.20` 与 `7.2` 是同一个数；`08` 与 `8` 也是。"""
    if "." in token:
        whole, _, fraction = token.partition(".")
        whole = whole.lstrip("0") or "0"
        fraction = fraction.rstrip("0")
        return f"{whole}.{fraction}" if fraction else whole
    return token.lstrip("0") or "0"


def numbers_in(text: str) -> frozenset[str]:
    """一段话里出现的数字，按大小写法归一后去重。"""
    return frozenset(_canonical(match) for match in _NUMBER.findall(normalize(text)))


@dataclass(frozen=True)
class DraftMismatch(ValueError):
    """两稿的数字对不上。`message` 只说差在哪，不改写原稿。"""

    first_only: tuple[str, ...]
    second_only: tuple[str, ...]

    def __str__(self) -> str:
        parts = []
        if self.first_only:
            parts.append("只有第一稿有：" + "、".join(self.first_only))
        if self.second_only:
            parts.append("只有第二稿有：" + "、".join(self.second_only))
        return "第二稿改了数字，退回重写（" + "；".join(parts) + "）。"


def ensure_same_numbers(first: str, second: str) -> None:
    """两稿的数字集合必须相同，否则抛 `DraftMismatch`。

    抛错时不返回「改好的版本」——改写是生成侧的事，这里只负责拦下来。
    """
    left, right = numbers_in(first), numbers_in(second)
    if left == right:
        return
    raise DraftMismatch(
        first_only=tuple(sorted(left - right)),
        second_only=tuple(sorted(right - left)),
    )


REFINE_SYSTEM_PROMPT = """
你是一名只做措辞的中文编辑。给你一段已经写好的回复，你把它改得更顺、更像一个人
在说话：去掉重复、拆开长句、把书面词换成口语。

硬规则（违反任何一条都算改坏）：
- 不许增删任何数字、剂量、单位、时间点。原文有「7.2」就还写 7.2，原文没有就不要加。
- 不许增加原文没有的事实、建议、诊断或承诺。
- 不许改变原文让患者做的事（让他测血糖、让他告诉家人），只能换说法。
- 长度大体不变，不要从三句话改写成一段。

只输出改写后的正文，不要解释你改了什么。
""".strip()


@dataclass(frozen=True)
class Refinement:
    """一次两稿生成的结果：最终用的那稿，以及被退回的稿数。"""

    text: str
    attempts: int
    rejected: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.attempts > 0


async def refine_wording(
    rewrite: Callable[[str], Awaitable[str]],
    *,
    draft: str,
    max_attempts: int = 2,
) -> Refinement:
    """让第二稿只改措辞；数字变了就打回重写，多次仍不合规就用第一稿。

    `rewrite` 是唯一接触模型的地方（传一个「给我一段话，还我一段话」的函数），
    这样这个流程本身可以在测试里跑，不需要真的调模型。

    退回到第一稿不是失败：第一稿是模型直接产出的内容，本来就能发；第二稿只是
    让它更好读。宁可少一点润色，也不要让患者看到一个被改过的数字。
    """

    rejected: list[str] = []
    for attempt in range(max_attempts):
        candidate = await rewrite(draft)
        try:
            ensure_same_numbers(draft, candidate)
        except DraftMismatch as mismatch:
            rejected.append(str(mismatch))
            continue
        return Refinement(text=candidate.strip(), attempts=attempt + 1)
    return Refinement(text=draft, attempts=max_attempts, rejected=tuple(rejected))
