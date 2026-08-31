"""消息解析测试：抢答格式、提问识别、/ag 子命令。"""

from __future__ import annotations

import pytest

from anime_guess.parsing import (
    is_command_like,
    looks_like_question,
    mentions_bot,
    parse_action,
    parse_ask,
    parse_guess,
)


class At:
    """仿 astrbot 的 At 组件。

    ``mentions_bot`` 按类名做鸭子类型判断，所以这个类必须字面叫 ``At``。
    """

    def __init__(self, qq):
        self.qq = qq


class Plain:
    def __init__(self, text):
        self.text = text


class FakeAtLike:
    """有 ``qq`` 属性但类名不是 At，不该被认作 @。"""

    def __init__(self, qq):
        self.qq = qq


# --------------------------------------------------------------------------- #
# /ag 子命令
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("", "help"),
        ("作品", "work"),
        ("角色", "character"),
        ("随机", "random"),
        ("结束", "stop"),
        ("排行榜", "rank"),
        ("帮助", "help"),
        ("work", "work"),
        ("WORK", "work"),
        ("Rank", "rank"),
        ("  角色  ", "character"),
        ("乱写的东西", "help"),
        (None, "help"),
    ],
)
def test_parse_action(action, expected):
    assert parse_action(action) == expected


# --------------------------------------------------------------------------- #
# 抢答格式
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("猜 蕾姆", "蕾姆"),
        ("猜　蕾姆", "蕾姆"),  # 全角空格
        ("猜：蕾姆", "蕾姆"),
        ("猜:蕾姆", "蕾姆"),
        ("猜  命运石之门  ", "命运石之门"),
        ("猜 Re:从零开始的异世界生活", "Re:从零开始的异世界生活"),
        ("猜 为美好的世界献上祝福！", "为美好的世界献上祝福！"),
    ],
)
def test_parse_guess_accepts_variants(text, expected):
    assert parse_guess(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "猜蕾姆",  # 没有分隔符，避免和「猜猜看」这类词误撞
        "我猜 蕾姆",  # 不在开头
        "猜",
        "猜 ",
        "这个角色是蕾姆吗",
        "",
        "随便猜猜",
    ],
)
def test_parse_guess_rejects_non_guesses(text):
    assert parse_guess(text) == ""


# --------------------------------------------------------------------------- #
# 显式提问格式
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("问 这个角色会使用魔法吗？", "这个角色会使用魔法吗？"),
        ("问　这个角色是女性吗", "这个角色是女性吗"),  # 全角空格
        ("问：是2020年之后的番吗", "是2020年之后的番吗"),
        ("问:有第二季吗", "有第二季吗"),
        ("问  会魔法  ", "会魔法"),
        # 显式指令不要求带疑问标记，玩家写「问 男主」也照样受理。
        ("问 男主", "男主"),
        ("  问 是京都动画做的吗  ", "是京都动画做的吗"),
        ("问 Re:从零开始的异世界生活里出现过吗", "Re:从零开始的异世界生活里出现过吗"),
    ],
)
def test_parse_ask_accepts_variants(text, expected):
    assert parse_ask(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "问题是什么",  # 没有分隔符，普通消息不能被吞掉
        "问号",
        "请问 这个角色会魔法吗",  # 不在开头
        "问",
        "问 ",
        "问　",
        "这个角色会使用魔法吗",  # 隐式提问走 looks_like_question，不是这里
        "",
    ],
)
def test_parse_ask_rejects_non_asks(text):
    assert parse_ask(text) == ""


def test_parse_ask_and_parse_guess_do_not_overlap():
    """「问 xxx」不该被当成抢答，「猜 xxx」也不该被当成提问。"""
    assert parse_guess("问 这个角色会魔法吗") == ""
    assert parse_ask("猜 蕾姆") == ""


# --------------------------------------------------------------------------- #
# 指令前缀
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/ag 作品", True),
        ("  /help", True),
        ("#tag", True),
        ("!cmd", True),
        ("！指令", True),
        ("猜 蕾姆", False),
        ("这是女性角色吗？", False),
        ("", False),
    ],
)
def test_is_command_like(text, expected):
    assert is_command_like(text) is expected


# --------------------------------------------------------------------------- #
# 提问识别
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    [
        "这是女性角色吗？",
        "是恋爱题材吗",
        "有没有魔法元素",
        "2020年后播出的?",
        "主角是学生么",
        "这部作品是否属于科幻",
        "算不算热血番",
        "谁是主角",
        "哪一年播出",
        "多少集",
        "会不会是京阿尼做的",
        "是什么题材",
        # 选择问句：没有「吗」，靠「还是」识别
        "是2020年之前的番还是之后的番",
        "男主还是女主",
        "是漫画改编或者原创动画",
        # 问号在句中而不是句尾。玩家常自带答题说明，问号后面还有话。
        "是2020年之前的番还是之后的番？是之前的回答是，不是之前的回答否",
        "她是主角？我猜是",
    ],
)
def test_looks_like_question_accepts_questions(text):
    assert looks_like_question(text) is True


def test_looks_like_question_accepts_mid_string_question_mark():
    """线上实测的漏判：问号在句中，只看句尾会漏，导致消息落到 LLM 主链路。

    漏判的后果不是「不回答」，而是消息被 LLM 的函数工具当成开局请求，
    回一句「本群已经有一局在进行了」——对一句提问来说是答非所问。
    """
    reported = "是2020年之前的番还是之后的番？是之前的回答是，不是之前的回答否"
    assert looks_like_question(reported) is True
    # 两个判据各自都能独立命中，去掉任意一个仍然识别得出
    assert looks_like_question(reported.replace("？", "")) is True
    assert looks_like_question("这番在2020之前？") is True


@pytest.mark.parametrize(
    "text",
    [
        "哈哈",
        "hh",
        "666",
        "草",
        "笑死",
        "ok",
        "嗯",
        "awsl",
        "?",
        "。",
        "好的",
        "牛",
        "",
        "a",  # 太短
        "这是一段很长的闲聊" * 30,  # 太长
        "我觉得这部作品很好看",  # 陈述句，无疑问标记
        "开始了开始了",
        # 自然语言控制指令：必须仍算「非提问」，否则会被裁判接走、
        # 走不到 start_anime_guess / stop_anime_guess 函数工具。
        "结束游戏",
        "再来一局",
        "来局动漫猜谜，猜作品",
        "不玩了",
        "开一局猜角色",
    ],
)
def test_looks_like_question_rejects_chatter(text):
    assert looks_like_question(text) is False


def test_looks_like_question_boundary_lengths():
    assert looks_like_question("谁") is False  # 1 字，低于下限
    assert looks_like_question("是谁") is True
    assert looks_like_question("吗" + "啊" * 200) is False  # 201 字，超上限
    assert looks_like_question("吗" + "啊" * 199) is True  # 200 字，刚好


# --------------------------------------------------------------------------- #
# @ 识别
# --------------------------------------------------------------------------- #
def test_mentions_bot_matches_self_id():
    assert mentions_bot([At("10001"), Plain("是女性吗")], "10001") is True


def test_mentions_bot_tolerates_int_ids():
    # 平台适配器给的 qq 与 self_id 有时是 int，有时是 str
    assert mentions_bot([At(10001)], "10001") is True
    assert mentions_bot([At("10001")], 10001) is True
    assert mentions_bot([At(10001)], 10001) is True


def test_mentions_bot_ignores_other_users():
    assert mentions_bot([At("99999"), Plain("问题")], "10001") is False


def test_mentions_bot_finds_at_anywhere_in_chain():
    assert mentions_bot([Plain("前缀"), At("10001")], "10001") is True


def test_mentions_bot_requires_at_component():
    assert mentions_bot([Plain("@bot 是女性吗")], "10001") is False


def test_mentions_bot_rejects_lookalike_component():
    # 类名不是 At，即使有 qq 属性也不算
    assert mentions_bot([FakeAtLike("10001")], "10001") is False


@pytest.mark.parametrize(
    ("chain", "self_id"),
    [
        (None, "10001"),
        ([], "10001"),
        ([At("10001")], ""),
        ([At("10001")], None),
        ([At("10001")], "   "),
        ([At("")], "10001"),
        ([At(None)], "10001"),
    ],
)
def test_mentions_bot_handles_missing_data(chain, self_id):
    assert mentions_bot(chain, self_id) is False
