"""LLM 裁判测试。用假 context 记录调用，不打真模型。"""

from __future__ import annotations

import asyncio

import pytest

from anime_guess.judge import NO, UNKNOWN, YES, Judge
from anime_guess.models import PUZZLE_CHARACTER, PUZZLE_WORK, Puzzle


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.completion_text = text


class FakeContext:
    """记录 llm_generate 的入参，并返回预设回答。"""

    def __init__(self, reply: str = "是", current_provider: str = "session-model"):
        self.reply = reply
        self.current_provider = current_provider
        self.calls: list[dict] = []
        self.provider_lookups: list[str] = []

    async def get_current_chat_provider_id(self, umo: str = ""):
        self.provider_lookups.append(umo)
        return self.current_provider

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.reply)


class BrokenProviderContext(FakeContext):
    """取当前模型时抛异常，用于验证降级路径。"""

    async def get_current_chat_provider_id(self, umo: str = ""):
        raise RuntimeError("provider manager unavailable")


WORK = Puzzle(
    kind=PUZZLE_WORK,
    name="命运石之门",
    aliases=("命运石之门", "STEINS;GATE", "シュタインズ・ゲート"),
    summary="秋叶原的时间机器故事。",
    facts=(("放送日期", "2011-04-06"),),
    source="Bangumi",
)

CHARACTER = Puzzle(
    kind=PUZZLE_CHARACTER,
    name="惠惠",
    aliases=("惠惠", "めぐみん", "Megumin"),
    work_name="为美好的世界献上祝福",
    work_aliases=("为美好的世界献上祝福", "この素晴らしい世界に祝福を!"),
    summary="爆裂魔法爱好者。",
    source="Bangumi",
)


# --------------------------------------------------------------------------- #
# provider 解析
# --------------------------------------------------------------------------- #
def test_uses_configured_provider_when_set():
    context = FakeContext()
    judge = Judge(context, "my-judge-model")
    asyncio.run(judge.answer_question("umo", WORK, "是科幻吗？"))
    assert context.calls[0]["chat_provider_id"] == "my-judge-model"
    # 配了裁判就不该再去查当前会话模型
    assert context.provider_lookups == []


def test_falls_back_to_session_provider_when_blank():
    context = FakeContext()
    judge = Judge(context, "")
    asyncio.run(judge.answer_question("umo-1", WORK, "是科幻吗？"))
    assert context.calls[0]["chat_provider_id"] == "session-model"
    assert context.provider_lookups == ["umo-1"]


def test_blank_provider_config_is_stripped():
    context = FakeContext()
    judge = Judge(context, "   ")
    asyncio.run(judge.answer_question("umo", WORK, "是科幻吗？"))
    assert context.calls[0]["chat_provider_id"] == "session-model"


def test_raises_when_no_provider_available():
    context = FakeContext(current_provider="")
    judge = Judge(context, "")
    with pytest.raises(RuntimeError, match="没有可用的聊天模型"):
        asyncio.run(judge.answer_question("umo", WORK, "是科幻吗？"))


def test_raises_when_provider_lookup_fails():
    judge = Judge(BrokenProviderContext(), "")
    with pytest.raises(RuntimeError, match="没有可用的聊天模型"):
        asyncio.run(judge.answer_question("umo", WORK, "是科幻吗？"))


# --------------------------------------------------------------------------- #
# 回答提问
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("reply", "expected"),
    [("是", YES), ("否", NO), ("不清楚", UNKNOWN), ("Yes.", YES), ("乱码", UNKNOWN)],
)
def test_answer_question_normalizes_reply(reply, expected):
    judge = Judge(FakeContext(reply=reply), "m")
    assert asyncio.run(judge.answer_question("umo", WORK, "是科幻吗？")) == expected


def test_answer_question_prompt_carries_puzzle_and_question():
    context = FakeContext()
    judge = Judge(context, "m")
    asyncio.run(judge.answer_question("umo", CHARACTER, "这是女性角色吗？"))
    call = context.calls[0]
    assert "惠惠" in call["prompt"]
    assert "为美好的世界献上祝福" in call["prompt"]
    assert "这是女性角色吗？" in call["prompt"]
    assert "爆裂魔法" in call["prompt"]
    # 系统提示词必须约束只输出三个词，并禁止泄底
    assert "是、否、不清楚" in call["system_prompt"]
    assert "不要泄露谜底" in call["system_prompt"]
    assert call["contexts"] == []


# --------------------------------------------------------------------------- #
# 判断猜测
# --------------------------------------------------------------------------- #
def test_exact_match_skips_llm_entirely():
    # 即使 LLM 会说「否」，本地精确匹配也应直接判对
    context = FakeContext(reply="否")
    judge = Judge(context, "m")
    assert asyncio.run(judge.check_guess("umo", WORK, "命运石之门")) is True
    assert context.calls == []


def test_alias_match_skips_llm():
    context = FakeContext(reply="否")
    judge = Judge(context, "m")
    assert asyncio.run(judge.check_guess("umo", CHARACTER, "めぐみん")) is True
    assert context.calls == []


def test_punctuation_variant_matches_locally():
    context = FakeContext(reply="否")
    judge = Judge(context, "m")
    assert asyncio.run(judge.check_guess("umo", WORK, "《命运石之门》")) is True
    assert context.calls == []


def test_near_miss_goes_to_llm_and_can_pass():
    context = FakeContext(reply="是")
    judge = Judge(context, "m")
    assert asyncio.run(judge.check_guess("umo", WORK, "命运石之门 0")) is True
    assert len(context.calls) == 1


def test_wrong_guess_rejected_by_llm():
    context = FakeContext(reply="否")
    judge = Judge(context, "m")
    assert asyncio.run(judge.check_guess("umo", CHARACTER, "阿库娅")) is False
    assert len(context.calls) == 1


def test_unclear_llm_verdict_counts_as_wrong():
    judge = Judge(FakeContext(reply="不清楚"), "m")
    assert asyncio.run(judge.check_guess("umo", WORK, "某个说不清的东西")) is False


def test_guess_prompt_includes_answer_and_aliases():
    context = FakeContext(reply="否")
    judge = Judge(context, "m")
    asyncio.run(judge.check_guess("umo", CHARACTER, "阿库娅"))
    call = context.calls[0]
    assert "惠惠" in call["prompt"]
    assert "为美好的世界献上祝福" in call["prompt"]
    assert "めぐみん" in call["prompt"]
    assert "阿库娅" in call["prompt"]
    assert "是、否、不清楚" in call["system_prompt"]


def test_guess_prompt_for_work_omits_work_clause():
    context = FakeContext(reply="否")
    judge = Judge(context, "m")
    asyncio.run(judge.check_guess("umo", WORK, "别的番"))
    prompt = context.calls[0]["prompt"]
    assert "谜底是作品：命运石之门" in prompt
    assert "出自《" not in prompt


def test_empty_guess_does_not_match_locally():
    context = FakeContext(reply="否")
    judge = Judge(context, "m")
    assert asyncio.run(judge.check_guess("umo", WORK, "   ")) is False
    # 空猜测没有本地命中，仍会问一次 LLM
    assert len(context.calls) == 1
