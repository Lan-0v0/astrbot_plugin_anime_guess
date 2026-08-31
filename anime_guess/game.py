"""对局管理：每个会话（群或私聊）同时最多一局。"""

from __future__ import annotations

import asyncio
import random

from .models import PUZZLE_CHARACTER, PUZZLE_WORK, GameSession, Puzzle

#: ``/ag 随机`` 可选的两种谜底类型。
RANDOM_KINDS = (PUZZLE_WORK, PUZZLE_CHARACTER)

START_TEXT = "游戏开始，请开始提问，我会回答“是/否/不清楚”直至谜底揭晓"

HELP_TEXT = """Anime Guess指令帮助：
“/ag 作品”：以作品名为谜底开启游戏
“/ag 角色”：以角色名为谜底开启游戏
“/ag 随机”：以作品/角色名为谜底开启游戏
“猜 作品/角色名”：对谜底进行猜测，比如“猜 蕾姆”
“/ag 结束”：结束游戏
“/ag 排行榜”：查看AGのKing~"""


class GameManager:
    """按会话 ID 存放进行中的对局。"""

    def __init__(self) -> None:
        self._sessions: dict[str, GameSession] = {}
        self._lock = asyncio.Lock()

    def get(self, origin: str) -> GameSession | None:
        return self._sessions.get(origin)

    def has_active(self, origin: str) -> bool:
        return origin in self._sessions

    async def start(
        self, origin: str, puzzle: Puzzle, host_id: str, host_name: str
    ) -> GameSession:
        """开一局。调用方需先确认该会话没有进行中的对局。"""
        session = GameSession(
            puzzle=puzzle, host_id=host_id, host_name=host_name, origin=origin
        )
        async with self._lock:
            self._sessions[origin] = session
        return session

    async def stop(self, origin: str) -> GameSession | None:
        """结束一局，返回被结束的对局（没有则为 None）。"""
        async with self._lock:
            return self._sessions.pop(origin, None)

    async def clear(self) -> None:
        """清空全部对局，插件卸载时调用。"""
        async with self._lock:
            self._sessions.clear()

    @staticmethod
    def pick_kind(requested: str) -> str:
        """把用户给的类型词转成谜底类型。"""
        text = (requested or "").strip()
        if text in ("作品", "work"):
            return PUZZLE_WORK
        if text in ("角色", "character"):
            return PUZZLE_CHARACTER
        return random.choice(RANDOM_KINDS)
