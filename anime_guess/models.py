"""谜底与对局的数据结构。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

#: 谜底类型：作品。
PUZZLE_WORK = "work"
#: 谜底类型：角色。
PUZZLE_CHARACTER = "character"

_KIND_LABELS = {PUZZLE_WORK: "作品", PUZZLE_CHARACTER: "角色"}


@dataclass(frozen=True)
class Puzzle:
    """一个谜底，以及供 LLM 裁判参考的上下文。

    Attributes:
        kind: :data:`PUZZLE_WORK` 或 :data:`PUZZLE_CHARACTER`。
        name: 谜底的首选名称。中文优先，缺失时为原文名。
        aliases: 其余可接受的名称（别名、原文名、罗马音、译名）。
        work_name: 角色谜底所属作品名；作品谜底为空。
        work_aliases: 所属作品的其他名称，供跨语言判定。
        summary: 简介，喂给 LLM 裁判。
        facts: 结构化事实（年份、类型、标签、性别等）。
        source: 数据来源库名称。
    """

    kind: str
    name: str
    aliases: tuple[str, ...] = ()
    work_name: str = ""
    work_aliases: tuple[str, ...] = ()
    summary: str = ""
    facts: tuple[tuple[str, str], ...] = ()
    source: str = ""

    @property
    def kind_label(self) -> str:
        """中文类型名，用于提示文案。"""
        return _KIND_LABELS.get(self.kind, self.kind)

    @property
    def all_names(self) -> tuple[str, ...]:
        """谜底的全部可接受名称，首选名在最前。"""
        return (self.name, *(a for a in self.aliases if a != self.name))

    def reveal_text(self) -> str:
        """揭晓文案。

        作品形如 ``《鬼父》``；角色形如 ``《为美好的世界献上祝福》中的 惠惠``。
        """
        if self.kind == PUZZLE_CHARACTER and self.work_name:
            return f"《{self.work_name}》中的 {self.name}"
        return f"《{self.name}》"

    def judge_context(self) -> str:
        """拼出给 LLM 裁判的谜底档案。"""
        lines = [f"谜底类型：{self.kind_label}"]
        if self.kind == PUZZLE_CHARACTER:
            lines.append(f"角色名：{self.name}")
            if self.work_name:
                lines.append(f"所属作品：{self.work_name}")
            if self.work_aliases:
                lines.append(f"作品别名：{'、'.join(self.work_aliases[:8])}")
        else:
            lines.append(f"作品名：{self.name}")
        other_names = [a for a in self.aliases if a != self.name]
        if other_names:
            lines.append(f"别名／其他语言名：{'、'.join(other_names[:10])}")
        for key, value in self.facts:
            if value:
                lines.append(f"{key}：{value}")
        if self.summary:
            lines.append(f"简介：{self.summary}")
        if self.source:
            lines.append(f"资料来源：{self.source}")
        return "\n".join(lines)


@dataclass
class GameSession:
    """一个群／私聊里正在进行的对局。"""

    puzzle: Puzzle
    host_id: str
    host_name: str
    origin: str
    started_at: float = field(default_factory=time.time)
    question_count: int = 0
    guess_count: int = 0

    def elapsed_seconds(self) -> float:
        return max(0.0, time.time() - self.started_at)
