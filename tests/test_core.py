"""核心逻辑单元测试：谜底渲染、答案解析、排行榜、对局管理。"""

from __future__ import annotations

import asyncio
import json

import pytest

from anime_guess.game import GameManager
from anime_guess.judge import (
    NO,
    UNKNOWN,
    YES,
    local_exact_match,
    normalize_name,
    parse_answer,
)
from anime_guess.leaderboard import EMPTY_SLOT, Leaderboard
from anime_guess.models import PUZZLE_CHARACTER, PUZZLE_WORK, Puzzle


# --------------------------------------------------------------------------- #
# 谜底揭晓文案
# --------------------------------------------------------------------------- #
def test_work_reveal_uses_book_quotes():
    puzzle = Puzzle(kind=PUZZLE_WORK, name="鬼父")
    assert puzzle.reveal_text() == "《鬼父》"


def test_character_reveal_includes_work():
    puzzle = Puzzle(
        kind=PUZZLE_CHARACTER, name="惠惠", work_name="为美好的世界献上祝福"
    )
    assert puzzle.reveal_text() == "《为美好的世界献上祝福》中的 惠惠"


def test_character_without_work_falls_back_to_quotes():
    puzzle = Puzzle(kind=PUZZLE_CHARACTER, name="惠惠")
    assert puzzle.reveal_text() == "《惠惠》"


def test_kind_label():
    assert Puzzle(kind=PUZZLE_WORK, name="x").kind_label == "作品"
    assert Puzzle(kind=PUZZLE_CHARACTER, name="x").kind_label == "角色"


def test_judge_context_contains_answer_and_facts():
    puzzle = Puzzle(
        kind=PUZZLE_CHARACTER,
        name="雷姆",
        aliases=("雷姆", "レム", "Rem"),
        work_name="Re:从零开始的异世界生活",
        work_aliases=("Re:从零开始的异世界生活", "Re:Zero"),
        summary="罗兹瓦尔宅邸的女仆。",
        facts=(("性别", "女"), ("配音", "水濑祈")),
        source="Bangumi",
    )
    context = puzzle.judge_context()
    assert "角色名：雷姆" in context
    assert "所属作品：Re:从零开始的异世界生活" in context
    assert "レム" in context
    assert "性别：女" in context
    assert "水濑祈" in context
    assert "Bangumi" in context


def test_all_names_puts_primary_first_without_duplicates():
    puzzle = Puzzle(kind=PUZZLE_WORK, name="命运石之门", aliases=("命运石之门", "STEINS;GATE"))
    assert puzzle.all_names == ("命运石之门", "STEINS;GATE")


# --------------------------------------------------------------------------- #
# 答案解析
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("是", YES),
        ("是。", YES),
        ("Yes", YES),
        ("正确", YES),
        ("是的", YES),
        ("否", NO),
        ("不是", NO),
        ("No", NO),
        ("不对", NO),
        ("不清楚", UNKNOWN),
        ("不确定", UNKNOWN),
        ("无法判断", UNKNOWN),
        ("", UNKNOWN),
        ("   ", UNKNOWN),
        ("我不知道呢", UNKNOWN),
    ],
)
def test_parse_answer(text, expected):
    assert parse_answer(text) == expected


def test_parse_answer_prefers_unknown_over_yes_prefix():
    # 「不清楚」里含「清」不含「是」，但「不确定」这类里可能带「是」字，
    # 必须先判 UNKNOWN 才不会被误判成 YES。
    assert parse_answer("这个我不确定，可能是吧") == UNKNOWN


def test_parse_answer_prefers_no_over_yes():
    assert parse_answer("不是的") == NO


# --------------------------------------------------------------------------- #
# 名称归一化与本地精确匹配
# --------------------------------------------------------------------------- #
def test_normalize_name_strips_punctuation_and_case():
    assert normalize_name("Re:从零开始的异世界生活") == normalize_name(
        "Re：从零开始的异世界生活"
    )
    assert normalize_name("STEINS;GATE") == normalize_name("steins gate")
    assert normalize_name("《进击的巨人》") == normalize_name("进击的巨人")
    assert normalize_name("") == ""


def test_local_exact_match_hits_alias():
    puzzle = Puzzle(
        kind=PUZZLE_CHARACTER,
        name="雷姆",
        aliases=("雷姆", "レム", "Rem"),
    )
    assert local_exact_match("雷姆", puzzle)
    assert local_exact_match("レム", puzzle)
    assert local_exact_match("rem", puzzle)
    assert local_exact_match(" Rem ", puzzle)
    assert not local_exact_match("拉姆", puzzle)
    assert not local_exact_match("", puzzle)


# --------------------------------------------------------------------------- #
# 排行榜
# --------------------------------------------------------------------------- #
def test_leaderboard_empty_renders_all_placeholders(tmp_path):
    board = Leaderboard(tmp_path / "lb.json")
    board.load()
    rendered = board.render()
    assert rendered.count(EMPTY_SLOT) == 5
    assert rendered.startswith(f"👑AGのKing👑：{EMPTY_SLOT}")
    assert "🥈第二名" in rendered
    assert "🥉第三名" in rendered
    assert "第四名" in rendered
    assert "第五名" in rendered


def test_leaderboard_counts_and_orders(tmp_path):
    board = Leaderboard(tmp_path / "lb.json")
    board.load()

    async def scenario():
        for _ in range(3):
            await board.add_win("u1", "小明")
        await board.add_win("u2", "小红")
        await board.add_win("u2", "小红")
        return await board.add_win("u3", "小刚")

    wins = asyncio.run(scenario())
    assert wins == 1
    assert board.top() == [("小明", 3), ("小红", 2), ("小刚", 1)]
    rendered = board.render()
    assert "👑AGのKing👑：小明 猜对3次" in rendered
    assert "🥈第二名：小红 猜对2次" in rendered
    assert "🥉第三名：小刚 猜对1次" in rendered
    assert rendered.count(EMPTY_SLOT) == 2


def test_leaderboard_persists_across_instances(tmp_path):
    path = tmp_path / "lb.json"
    first = Leaderboard(path)
    first.load()
    asyncio.run(first.add_win("u1", "小明"))

    second = Leaderboard(path)
    second.load()
    assert second.top() == [("小明", 1)]


def test_leaderboard_only_shows_top_five(tmp_path):
    board = Leaderboard(tmp_path / "lb.json")
    board.load()

    async def scenario():
        for index in range(7):
            for _ in range(index + 1):
                await board.add_win(f"u{index}", f"玩家{index}")

    asyncio.run(scenario())
    assert len(board.top()) == 5
    rendered = board.render()
    assert "玩家6 猜对7次" in rendered
    assert "玩家0" not in rendered
    assert EMPTY_SLOT not in rendered


def test_leaderboard_tolerates_corrupt_file(tmp_path):
    path = tmp_path / "lb.json"
    path.write_text("{ not json", encoding="utf-8")
    board = Leaderboard(path)
    board.load()
    assert board.top() == []


def test_leaderboard_skips_invalid_entries(tmp_path):
    path = tmp_path / "lb.json"
    path.write_text(
        json.dumps(
            {
                "scores": {
                    "ok": {"name": "好", "wins": 2},
                    "zero": {"name": "零", "wins": 0},
                    "bad": {"name": "坏", "wins": "abc"},
                    "notdict": "nope",
                }
            }
        ),
        encoding="utf-8",
    )
    board = Leaderboard(path)
    board.load()
    assert board.top() == [("好", 2)]


def test_leaderboard_updates_name_on_new_win(tmp_path):
    board = Leaderboard(tmp_path / "lb.json")
    board.load()

    async def scenario():
        await board.add_win("u1", "旧名")
        await board.add_win("u1", "新名")

    asyncio.run(scenario())
    assert board.top() == [("新名", 2)]


# --------------------------------------------------------------------------- #
# 对局管理
# --------------------------------------------------------------------------- #
def test_pick_kind():
    assert GameManager.pick_kind("作品") == PUZZLE_WORK
    assert GameManager.pick_kind("角色") == PUZZLE_CHARACTER
    assert GameManager.pick_kind("work") == PUZZLE_WORK
    assert GameManager.pick_kind("character") == PUZZLE_CHARACTER
    assert GameManager.pick_kind("随机") in (PUZZLE_WORK, PUZZLE_CHARACTER)
    assert GameManager.pick_kind("") in (PUZZLE_WORK, PUZZLE_CHARACTER)


def test_game_manager_lifecycle():
    manager = GameManager()
    puzzle = Puzzle(kind=PUZZLE_WORK, name="孤独摇滚")

    async def scenario():
        assert not manager.has_active("g1")
        session = await manager.start("g1", puzzle, "host", "房主")
        assert manager.has_active("g1")
        assert manager.get("g1") is session
        assert session.host_name == "房主"
        assert not manager.has_active("g2")

        stopped = await manager.stop("g1")
        assert stopped is session
        assert not manager.has_active("g1")
        assert await manager.stop("g1") is None

    asyncio.run(scenario())


def test_game_manager_isolates_sessions():
    manager = GameManager()

    async def scenario():
        await manager.start("g1", Puzzle(kind=PUZZLE_WORK, name="A"), "h1", "H1")
        await manager.start("g2", Puzzle(kind=PUZZLE_WORK, name="B"), "h2", "H2")
        assert manager.get("g1").puzzle.name == "A"
        assert manager.get("g2").puzzle.name == "B"
        await manager.clear()
        assert not manager.has_active("g1")
        assert not manager.has_active("g2")

    asyncio.run(scenario())
