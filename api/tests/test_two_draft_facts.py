"""两稿数字一致性的回归测试。"""

import pytest

from antang_api.psychosocial.two_draft import (
    DraftMismatch,
    ensure_same_numbers,
    numbers_in,
    refine_wording,
)


def test_rewording_with_the_same_numbers_passes() -> None:
    first = "今晚 8.9，测于 17:46，先记下来，睡前再看一次。"
    second = "17 点 46 分测的是 8.9，我先记着，睡前可以再测一次。"

    # 措辞改了、语气软了一点，数字一个没动：放行。
    ensure_same_numbers(first, second)


def test_a_polished_dose_is_caught() -> None:
    first = "你把晚饭前的胰岛素从 8 单位加到了 12。"
    second = "你把晚饭前的胰岛素加到了 10 单位。"

    with pytest.raises(DraftMismatch) as caught:
        ensure_same_numbers(first, second)

    assert caught.value.second_only == ("10",)
    assert caught.value.first_only == ("12", "8")
    assert "第二稿改了数字" in str(caught.value)


def test_a_number_dropped_in_the_second_draft_is_caught() -> None:
    first = "血糖 6.1，刚才打了 8 单位。"
    second = "血糖 6.1，胰岛素已经记下了。"

    with pytest.raises(DraftMismatch) as caught:
        ensure_same_numbers(first, second)

    assert caught.value.first_only == ("8",)
    assert caught.value.second_only == tuple()


def test_full_width_and_trailing_zeros_are_the_same_number() -> None:
    # 模型有时把数字写成全角、有时多带一个 0：这两种都不该算成「改了」。
    assert numbers_in("血糖７.２０ mmol/L") == numbers_in("血糖 7.2 mmol/L")
    assert numbers_in("剂量 08 单位") == numbers_in("剂量 8 单位")


def test_chinese_number_words_are_out_of_scope() -> None:
    # 记在这里当文档：中文数量词不在比对范围内，这是已知边界。
    assert numbers_in("吃了两片") == frozenset()


async def test_a_clean_second_draft_is_used() -> None:
    async def rewrite(draft: str) -> str:
        return f"{draft}你可以先喝口水。"

    result = await refine_wording(rewrite, draft="血糖 7.2，比目标高一点。")

    assert result.text == "血糖 7.2，比目标高一点。你可以先喝口水。"
    assert result.attempts == 1
    assert result.rejected == tuple()
    assert result.changed is True


async def test_a_second_draft_that_moves_a_number_falls_back_to_the_first() -> None:
    async def rewrite(draft: str) -> str:
        return "血糖 8.2，比目标高一点。"

    result = await refine_wording(rewrite, draft="血糖 7.2，比目标高一点。", max_attempts=2)

    # 两轮都不合规，最终发出的仍是第一稿：宁可少一点润色，也不要让他看到改过的数字。
    assert result.text == "血糖 7.2，比目标高一点。"
    assert result.attempts == 2
    assert len(result.rejected) == 2


async def test_the_second_try_can_still_be_used() -> None:
    answers = iter(["血糖 9.9，比目标高一点。", "血糖 7.2，比目标高一点，留意一下。"])

    async def rewrite(draft: str) -> str:
        return next(answers)

    result = await refine_wording(rewrite, draft="血糖 7.2，比目标高一点。")

    assert result.text == "血糖 7.2，比目标高一点，留意一下。"
    assert result.attempts == 2
