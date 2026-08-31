"""Anime Guess 的核心逻辑。

拆成独立包，便于脱离 AstrBot 运行时做单元测试。
"""

from __future__ import annotations

from .game import HELP_TEXT, START_TEXT, GameManager
from .judge import NO, UNKNOWN, YES, Judge, local_exact_match, normalize_name, parse_answer
from .leaderboard import EMPTY_SLOT, TOP_N, Leaderboard
from .models import PUZZLE_CHARACTER, PUZZLE_WORK, GameSession, Puzzle
from .parsing import (
    is_command_like,
    looks_like_question,
    mentions_bot,
    parse_action,
    parse_guess,
)

__all__ = [
    "EMPTY_SLOT",
    "HELP_TEXT",
    "NO",
    "PUZZLE_CHARACTER",
    "PUZZLE_WORK",
    "START_TEXT",
    "TOP_N",
    "UNKNOWN",
    "YES",
    "GameManager",
    "GameSession",
    "Judge",
    "Leaderboard",
    "Puzzle",
    "is_command_like",
    "local_exact_match",
    "looks_like_question",
    "mentions_bot",
    "normalize_name",
    "parse_action",
    "parse_answer",
    "parse_guess",
]
