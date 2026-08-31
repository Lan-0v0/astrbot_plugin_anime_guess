"""对局中的消息解析：抢答格式、提问识别、``/ag`` 子命令。

单独放一个模块，这样不依赖 AstrBot 运行时就能测。
"""

from __future__ import annotations

import re

#: 「猜 xxx」的抢答格式。空格、全角空格、冒号都认。
GUESS_PATTERN = re.compile(r"^猜[\s　:：]+(?P<answer>.+)$")

#: 提问至少要有这么多字，避免把「嗯」当提问喂给 LLM。
MIN_QUESTION_LENGTH = 2

#: 提问最多这么长，超长的基本是复制粘贴的闲聊。
MAX_QUESTION_LENGTH = 200

#: 明显不是提问的短语，直接忽略，不消耗 LLM 调用。
IGNORED_PHRASES = frozenset(
    {
        "哈哈", "哈哈哈", "hh", "hhh", "?", "？", "。", "…", "...", "666",
        "草", "笑死", "ok", "好", "好的", "嗯", "awsl", "行", "牛", "6",
    }
)

#: 疑问标记。命中任一即视为提问。
QUESTION_MARKERS = (
    "吗", "么", "是不是", "有没有", "是否", "属于", "算不算", "对吗", "对不对",
    "难道", "该不会", "会不会", "能不能", "多少", "几", "谁", "哪", "什么",
    # 选择问句：「是2020年之前的番还是之后的」这种没有「吗」也是提问。
    "还是", "或者", "是不", "有无",
)

#: 消息以这些字符开头时视为其他插件的指令，不参与对局。
COMMAND_PREFIXES = ("/", "#", "!", "！", ".", "。/")

#: ``/ag`` 的子命令别名 → 动作。键都是小写。
ACTION_ALIASES = {
    "作品": "work",
    "work": "work",
    "猜作品": "work",
    "角色": "character",
    "character": "character",
    "人物": "character",
    "猜角色": "character",
    "随机": "random",
    "random": "random",
    "随便": "random",
    "结束": "stop",
    "stop": "stop",
    "end": "stop",
    "退出": "stop",
    "排行榜": "rank",
    "rank": "rank",
    "leaderboard": "rank",
    "榜": "rank",
    "帮助": "help",
    "help": "help",
}


def parse_action(action: str) -> str:
    """把 ``/ag`` 后面的词转成动作名，无法识别时返回 ``"help"``。"""
    return ACTION_ALIASES.get(str(action or "").strip().casefold(), "help")


def parse_guess(text: str) -> str:
    """从消息里取出被猜的名字。不是抢答格式时返回空串。"""
    match = GUESS_PATTERN.match(str(text or "").strip())
    if match is None:
        return ""
    return match.group("answer").strip()


def is_command_like(text: str) -> bool:
    """判断消息是否是（其他插件的）指令。"""
    stripped = str(text or "").lstrip()
    return stripped.startswith(COMMAND_PREFIXES)


def looks_like_question(text: str) -> bool:
    """判断一条消息是否算「提问」。

    对局中不该把群里每句闲聊都送去 LLM，所以要求带疑问标记。
    """
    stripped = str(text or "").strip()
    if not MIN_QUESTION_LENGTH <= len(stripped) <= MAX_QUESTION_LENGTH:
        return False
    if stripped.casefold() in IGNORED_PHRASES:
        return False
    # 问号出现在任何位置都算：玩家常写「……之前的番？是之前的回答是」，
    # 问号在中间，只看结尾会漏判，进而让消息落到 LLM 主链路上去。
    if "?" in stripped or "？" in stripped:
        return True
    return any(marker in stripped for marker in QUESTION_MARKERS)


def mentions_bot(message_chain, self_id) -> bool:
    """判断消息链里是否真的 @ 了 bot 本身。

    刻意不用 ``event.is_at_or_wake_command``：那个属性在「命中唤醒前缀」时
    也为真，用它做「仅在被@时回答」会把带唤醒前缀的普通提问也放行。

    组件类型用类名判断而不是 ``isinstance``，好让本模块不依赖 AstrBot，
    从而可以脱离运行时做单元测试。
    """
    target = str(self_id or "").strip()
    if not target or not message_chain:
        return False
    for component in message_chain:
        if type(component).__name__ != "At":
            continue
        if str(getattr(component, "qq", "") or "").strip() == target:
            return True
    return False
