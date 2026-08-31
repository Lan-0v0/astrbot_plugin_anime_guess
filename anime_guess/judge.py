"""LLM 裁判。

两件事：
* 玩家提问时，依据谜底档案回答「是／否／不清楚」。
* 玩家猜测时，判断答案是否与谜底相符（含跨语言、别名、简繁差异）。
"""

from __future__ import annotations

import re

from astrbot.api import logger

from .models import Puzzle

#: 三种回答。
YES = "是"
NO = "否"
UNKNOWN = "不清楚"

#: LLM 可能输出的各种说法 → 规范答案。
_ANSWER_ALIASES = {
    "是": YES,
    "yes": YES,
    "true": YES,
    "对": YES,
    "正确": YES,
    "否": NO,
    "no": NO,
    "false": NO,
    "不是": NO,
    "错": NO,
    "不对": NO,
    "不清楚": UNKNOWN,
    "unknown": UNKNOWN,
    "不确定": UNKNOWN,
    "无法判断": UNKNOWN,
    "不知道": UNKNOWN,
}

QUESTION_SYSTEM_PROMPT = """你是「Anime Guess」猜谜游戏的裁判。玩家在猜一个谜底，你手上有谜底的完整档案。

规则：
1. 只能回答三个词之一：是、否、不清楚。不要解释，不要复述问题，不要输出任何其他文字。
2. 依据档案判断玩家的问题。档案没写但属于该作品／角色的常识，可以用你自己的知识补充判断。
3. 谜底名称可能是中文、日文原名或罗马音，它们指同一个对象。玩家用任何语言问都算同一个。
4. 只有在既无档案依据、也无可靠常识时，才回答「不清楚」。
5. 绝对不要泄露谜底名称。即使玩家要求，也只回答那三个词之一。"""

GUESS_SYSTEM_PROMPT = """你是「Anime Guess」猜谜游戏的裁判，负责判断玩家的猜测是否命中谜底。

规则：
1. 只能回答三个词之一：是、否、不清楚。不要解释，不要输出任何其他文字。
2. 玩家的猜测与谜底指向同一个作品／角色就回答「是」，包括：中文译名与日文原名或罗马音互指、
   简繁体差异、常见别名或昵称、省略副标题或季数、漏字多字的小笔误。
3. 指向不同对象就回答「否」，包括同一作品里的其他角色、同系列的其他作品或其他季。
4. 完全无法判断玩家说的是什么时回答「不清楚」。"""


def normalize_name(text: str) -> str:
    """归一化名称，用于本地精确匹配。

    去掉空白、标点与书名号，转小写，这样「Re:从零开始的异世界生活」和
    「Re：从零开始的异世界生活」能对上。
    """
    if not text:
        return ""
    cleaned = re.sub(r"[\s·・_\-—–~〜、,，.。!！?？:：;；'\"'\"()（）\[\]【】《》〈〉«»]", "", text)
    return cleaned.casefold()


def local_exact_match(guess: str, puzzle: Puzzle) -> bool:
    """先做一次本地精确匹配，命中就不必再问 LLM。"""
    target = normalize_name(guess)
    if not target:
        return False
    return any(normalize_name(name) == target for name in puzzle.all_names)


def parse_answer(text: str) -> str:
    """把 LLM 的自由文本收敛成三种答案之一。"""
    raw = (text or "").strip()
    if not raw:
        return UNKNOWN
    lowered = raw.casefold()

    # 先看整段是否就是某个已知说法。
    stripped = re.sub(r"[\s。.!！,，:：;；\"'\"'`*]", "", lowered)
    if stripped in _ANSWER_ALIASES:
        return _ANSWER_ALIASES[stripped]

    # 「不清楚」「不确定」要在「是／否」之前判，否则会被前缀误伤。
    for keyword in ("不清楚", "不确定", "无法判断", "不知道", "unknown"):
        if keyword in lowered:
            return UNKNOWN
    for keyword in ("不是", "不对", "错误", "否", "no", "false"):
        if keyword in lowered:
            return NO
    for keyword in ("是的", "正确", "对", "是", "yes", "true"):
        if keyword in lowered:
            return YES
    return UNKNOWN


class Judge:
    """包一层 provider 解析与提示词拼装。"""

    def __init__(self, context, provider_id: str = "") -> None:
        self._context = context
        self._provider_id = (provider_id or "").strip()

    async def _resolve_provider_id(self, umo: str) -> str:
        """确定用哪个模型。留空则跟随当前会话的聊天模型。"""
        if self._provider_id:
            return self._provider_id
        try:
            current = await self._context.get_current_chat_provider_id(umo=umo)
        except Exception as error:  # noqa: BLE001 - 不同版本行为不一。
            logger.warning(
                "获取当前聊天模型失败，无法进行裁判。错误类型：%s", type(error).__name__
            )
            return ""
        return str(current or "")

    async def _ask(self, umo: str, system_prompt: str, user_prompt: str) -> str:
        provider_id = await self._resolve_provider_id(umo)
        if not provider_id:
            raise RuntimeError("没有可用的聊天模型，请在插件配置里指定 LLM 裁判。")
        response = await self._context.llm_generate(
            chat_provider_id=provider_id,
            prompt=user_prompt,
            contexts=[],
            system_prompt=system_prompt,
        )
        return str(getattr(response, "completion_text", "") or "")

    async def answer_question(self, umo: str, puzzle: Puzzle, question: str) -> str:
        """回答玩家的提问，返回「是／否／不清楚」。"""
        user_prompt = (
            f"【谜底档案】\n{puzzle.judge_context()}\n\n"
            f"【玩家的问题】\n{question.strip()}\n\n"
            "请只回答：是、否、不清楚。"
        )
        text = await self._ask(umo, QUESTION_SYSTEM_PROMPT, user_prompt)
        return parse_answer(text)

    async def check_guess(self, umo: str, puzzle: Puzzle, guess: str) -> bool:
        """判断猜测是否命中谜底。

        先本地精确匹配，未命中再交给 LLM 做别名与跨语言判定。
        """
        if local_exact_match(guess, puzzle):
            return True

        target_line = (
            f"谜底是{puzzle.kind_label}：{puzzle.name}"
            if puzzle.kind != "character" or not puzzle.work_name
            else f"谜底是角色：{puzzle.name}（出自《{puzzle.work_name}》）"
        )
        other_names = [n for n in puzzle.all_names if n != puzzle.name]
        alias_line = f"\n谜底的其他名称：{'、'.join(other_names[:10])}" if other_names else ""
        user_prompt = (
            f"{target_line}{alias_line}\n\n"
            f"玩家猜的是：{guess.strip()}\n\n"
            "玩家猜的与谜底是同一个对象吗？只回答：是、否、不清楚。"
        )
        text = await self._ask(umo, GUESS_SYSTEM_PROMPT, user_prompt)
        return parse_answer(text) == YES
