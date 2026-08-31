"""排行榜持久化。

数据落在 AstrBot 的 ``data`` 目录下，插件更新或重装都不会丢。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

#: 排行榜只显示前五名。
TOP_N = 5

#: 某个名次没有数据时显示的占位文案。
EMPTY_SLOT = "虚以待位"

_RANK_PREFIXES = (
    "👑AGのKing👑",
    "🥈第二名",
    "🥉第三名",
    "第四名",
    "第五名",
)


class Leaderboard:
    """猜对次数排行榜，按用户 ID 计数。"""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._scores: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        """从磁盘读取。文件缺失或损坏时视为空榜。"""
        self._loaded = True
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._scores = {}
            return

        scores = raw.get("scores") if isinstance(raw, dict) else None
        if not isinstance(scores, dict):
            scores = raw if isinstance(raw, dict) else {}

        cleaned: dict[str, dict] = {}
        for user_id, entry in scores.items():
            if not isinstance(entry, dict):
                continue
            try:
                wins = int(entry.get("wins", 0))
            except (TypeError, ValueError):
                continue
            if wins <= 0:
                continue
            cleaned[str(user_id)] = {
                "name": str(entry.get("name") or user_id),
                "wins": wins,
            }
        self._scores = cleaned

    def _save(self) -> None:
        """原子写盘：先写临时文件再替换，避免中断留下半个文件。"""
        payload = {"version": 1, "scores": self._scores}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp_path, self._path)

    async def add_win(self, user_id: str, user_name: str) -> int:
        """给某人加一次猜对，返回其累计次数。"""
        key = str(user_id or "").strip() or "unknown"
        async with self._lock:
            if not self._loaded:
                self.load()
            entry = self._scores.setdefault(key, {"name": key, "wins": 0})
            entry["wins"] = int(entry.get("wins", 0)) + 1
            if user_name:
                entry["name"] = user_name
            wins = entry["wins"]
            # 写盘失败不该打断对局，内存里的成绩仍然生效。
            with contextlib.suppress(OSError):
                self._save()
        return wins

    def top(self, limit: int = TOP_N) -> list[tuple[str, int]]:
        """取前 ``limit`` 名，返回 ``(名称, 次数)``。

        按次数降序、名称升序排，保证渲染结果稳定。
        """
        if not self._loaded:
            self.load()
        ordered = sorted(
            self._scores.values(),
            key=lambda entry: (-int(entry.get("wins", 0)), str(entry.get("name", ""))),
        )
        return [
            (str(entry.get("name") or "?"), int(entry.get("wins", 0)))
            for entry in ordered[:limit]
        ]

    def render(self) -> str:
        """渲染成用户看到的排行榜文本。"""
        entries = self.top(TOP_N)
        lines = []
        for index, prefix in enumerate(_RANK_PREFIXES):
            if index < len(entries):
                name, wins = entries[index]
                lines.append(f"{prefix}：{name} 猜对{wins}次")
            else:
                lines.append(f"{prefix}：{EMPTY_SLOT}")
        return "\n".join(lines)
