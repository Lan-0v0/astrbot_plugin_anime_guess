"""Bangumi（bgm.tv）数据源。

公开读取不需要 Access Token；填了 token 可提高频率限制。作品与角色都有
``简体中文名``，是三个来源里中文覆盖最好的一个，因此作为默认来源。
"""

from __future__ import annotations

import random

from ..models import PUZZLE_CHARACTER, PUZZLE_WORK, Puzzle
from .base import AnimeSource, SourceError, dedupe, prefer_chinese, strip_markup

API_BASE = "https://api.bgm.tv"
USER_AGENT = (
    "Lan-0v0/astrbot_plugin_anime_guess "
    "(https://github.com/Lan-0v0/astrbot_plugin_anime_guess)"
)

#: 单次搜索最多只能翻到 1000 条，用 rank 分窗口把可抽取范围拉大。
RANK_WINDOWS = ((1, 800), (800, 1800), (1800, 3200), (3200, 5000), (5000, 8000))

#: 命中这些标签的条目不是日本动画（欧美动画、国产动画等），抽到就重抽。
_NON_JP_TAGS = frozenset({"欧美", "欧美动画", "美国", "美国动画", "国产", "国产动画", "中国", "韩国", "韩国动画"})

#: 主角优先，其次配角；这两类之外（客串、路人）不适合当谜底。
_GOOD_RELATIONS = ("主角", "配角")


class BangumiSource(AnimeSource):
    """从 Bangumi 抽取作品或角色。"""

    name = "Bangumi"

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _search_body(self, rank_window) -> dict:
        low, high = rank_window
        return {
            "keyword": "",
            "sort": "rank",
            "filter": {
                "type": [2],  # 2 = 动画
                "nsfw": False,
                "rank": [f">={low}", f"<{high}"],
            },
        }

    async def _random_subject(self, attempts: int = 4) -> dict:
        """随机取一个日本动画条目。

        Bangumi 的 ``type=2`` 也包含欧美／国产动画，这里按标签过滤后重抽。
        """
        last_error: str = ""
        for _ in range(attempts):
            body = self._search_body(random.choice(RANK_WINDOWS))
            probe = await self._post_json(
                f"{API_BASE}/v0/search/subjects?limit=1&offset=0",
                headers=self._headers(),
                json_body=body,
            )
            total = min(int(probe.get("total") or 0), 1000)
            if total <= 0:
                last_error = "搜索结果为空"
                continue
            offset = random.randint(0, total - 1)
            page = await self._post_json(
                f"{API_BASE}/v0/search/subjects?limit=1&offset={offset}",
                headers=self._headers(),
                json_body=body,
            )
            items = page.get("data") or []
            if not items:
                last_error = "分页结果为空"
                continue
            subject = items[0]
            tags = {str(t.get("name") or "") for t in (subject.get("tags") or [])}
            if tags & _NON_JP_TAGS:
                last_error = "抽到非日本动画"
                continue
            if not (subject.get("name_cn") or subject.get("name")):
                last_error = "条目没有名称"
                continue
            return subject
        raise SourceError(f"Bangumi 未能抽到合适的条目（{last_error}）")

    @staticmethod
    def _subject_facts(subject: dict) -> tuple[tuple[str, str], ...]:
        tags = [str(t.get("name") or "") for t in (subject.get("tags") or [])][:12]
        rating = subject.get("rating") or {}
        facts = [
            ("放送日期", str(subject.get("date") or "")),
            ("载体", str(subject.get("platform") or "")),
            ("集数", str(subject.get("eps") or "") if subject.get("eps") else ""),
            ("评分", str(rating.get("score") or "")),
            ("标签", "、".join(t for t in tags if t)),
        ]
        return tuple((k, v) for k, v in facts if v)

    @staticmethod
    def _subject_names(subject: dict) -> tuple[str, ...]:
        return prefer_chinese(
            [subject.get("name_cn") or "", subject.get("name") or ""]
        )

    async def random_work(self) -> Puzzle:
        subject = await self._random_subject()
        names = self._subject_names(subject)
        return Puzzle(
            kind=PUZZLE_WORK,
            name=names[0],
            aliases=names,
            summary=strip_markup(subject.get("summary") or "")[:600],
            facts=self._subject_facts(subject),
            source=self.name,
        )

    @staticmethod
    def _infobox_names(detail: dict) -> tuple[str, ...]:
        """从角色 infobox 里刮出中文名与各种别名。"""
        collected: list[str] = []
        for row in detail.get("infobox") or []:
            key = str(row.get("key") or "")
            value = row.get("value")
            if key in ("简体中文名", "第二中文名"):
                if isinstance(value, str):
                    collected.append(value)
            elif key == "别名" and isinstance(value, list):
                for alias in value:
                    if isinstance(alias, dict):
                        collected.append(str(alias.get("v") or ""))
                    else:
                        collected.append(str(alias or ""))
        collected.append(str(detail.get("name") or ""))
        return prefer_chinese(collected)

    async def random_character(self, attempts: int = 4) -> Puzzle:
        last_error = ""
        for _ in range(attempts):
            subject = await self._random_subject()
            subject_id = subject.get("id")
            try:
                cast = await self._get_json(
                    f"{API_BASE}/v0/subjects/{subject_id}/characters",
                    headers=self._headers(),
                )
            except SourceError:
                last_error = "取角色列表失败"
                continue
            if not isinstance(cast, list) or not cast:
                last_error = "条目没有角色"
                continue

            candidates: list[dict] = []
            for relation in _GOOD_RELATIONS:
                candidates = [c for c in cast if c.get("relation") == relation]
                if candidates:
                    break
            if not candidates:
                last_error = "没有主角或配角"
                continue

            chosen = random.choice(candidates)
            try:
                detail = await self._get_json(
                    f"{API_BASE}/v0/characters/{chosen.get('id')}",
                    headers=self._headers(),
                )
            except SourceError:
                last_error = "取角色详情失败"
                continue

            names = self._infobox_names(detail)
            if not names:
                last_error = "角色没有名称"
                continue

            work_names = self._subject_names(subject)
            gender = str(detail.get("gender") or "")
            facts = [
                ("性别", {"male": "男", "female": "女"}.get(gender, gender)),
                ("出场作品放送日期", str(subject.get("date") or "")),
                (
                    "作品标签",
                    "、".join(
                        str(t.get("name") or "")
                        for t in (subject.get("tags") or [])[:10]
                    ),
                ),
                ("在作品中的定位", str(chosen.get("relation") or "")),
            ]
            return Puzzle(
                kind=PUZZLE_CHARACTER,
                name=names[0],
                aliases=names,
                work_name=work_names[0] if work_names else "",
                work_aliases=work_names,
                summary=strip_markup(detail.get("summary") or "")[:600],
                facts=tuple((k, v) for k, v in facts if v),
                source=self.name,
            )
        raise SourceError(f"Bangumi 未能抽到合适的角色（{last_error}）")


__all__ = ["BangumiSource", "dedupe"]
