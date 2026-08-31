"""AniList（GraphQL）数据源。

公开读取无需鉴权；AniList 的 token 只用于写用户列表，因此配置里的密钥是可选的。
中文译名只存在 ``synonyms`` / ``name.alternative`` 里，覆盖率约一半，缺失时
回退到日文原名或罗马音，交由 LLM 裁判做跨语言判定。
"""

from __future__ import annotations

import random

from ..models import PUZZLE_CHARACTER, PUZZLE_WORK, Puzzle
from .base import AnimeSource, SourceError, prefer_chinese, strip_markup

API_URL = "https://graphql.anilist.co"

#: AniList 分页在 5000 条左右见底，按 perPage=25 折算约 200 页。
MAX_PAGE = 200
PER_PAGE = 25

WORK_QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    media(type: ANIME, sort: POPULARITY_DESC, isAdult: false) {
      title { romaji english native }
      synonyms
      genres
      seasonYear
      season
      format
      episodes
      averageScore
      studios(isMain: true) { nodes { name } }
      description(asHtml: false)
    }
  }
}
"""

CHARACTER_QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    characters(sort: FAVOURITES_DESC) {
      name { full native alternative }
      gender
      age
      favourites
      description(asHtml: false)
      media(perPage: 2, sort: POPULARITY_DESC) {
        nodes {
          type
          title { romaji english native }
          synonyms
          genres
          seasonYear
        }
      }
    }
  }
}
"""

_SEASON_LABELS = {
    "WINTER": "冬季",
    "SPRING": "春季",
    "SUMMER": "夏季",
    "FALL": "秋季",
}


class AniListSource(AnimeSource):
    """从 AniList 抽取作品或角色。"""

    name = "AniList"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _query(self, query: str) -> dict:
        payload = {
            "query": query,
            "variables": {"page": random.randint(1, MAX_PAGE), "perPage": PER_PAGE},
        }
        data = await self._post_json(API_URL, headers=self._headers(), json_body=payload)
        if data.get("errors"):
            first = (data["errors"] or [{}])[0]
            raise SourceError(f"AniList 返回错误：{first.get('message', '未知错误')}")
        page = (data.get("data") or {}).get("Page") or {}
        return page

    @staticmethod
    def _media_names(media: dict) -> tuple[str, ...]:
        """列出作品的全部名称，正式名在各国译名之前。

        ``synonyms`` 里混着各语言译名，正式名必须排在前面：没有中文译名时
        才不会让意大利语之类的译名当谜底。prefer_chinese 仍会把中文提到最前，
        所以有中文译名时不受影响。
        """
        title = media.get("title") or {}
        return prefer_chinese(
            [
                title.get("native") or "",
                title.get("romaji") or "",
                title.get("english") or "",
                *(media.get("synonyms") or []),
            ]
        )

    @staticmethod
    def _media_facts(media: dict) -> tuple[tuple[str, str], ...]:
        studios = [
            str(node.get("name") or "")
            for node in ((media.get("studios") or {}).get("nodes") or [])
        ]
        season = _SEASON_LABELS.get(str(media.get("season") or ""), "")
        year = media.get("seasonYear")
        facts = [
            ("首播", f"{year} 年{season}" if year else season),
            ("类型", str(media.get("format") or "")),
            ("集数", str(media.get("episodes") or "") if media.get("episodes") else ""),
            ("题材", "、".join(str(g) for g in (media.get("genres") or []))),
            ("制作公司", "、".join(s for s in studios if s)),
            (
                "均分",
                f"{media['averageScore']}/100" if media.get("averageScore") else "",
            ),
        ]
        return tuple((k, v) for k, v in facts if v)

    async def random_work(self, attempts: int = 3) -> Puzzle:
        last_error = ""
        for _ in range(attempts):
            page = await self._query(WORK_QUERY)
            pool = [m for m in (page.get("media") or []) if m]
            if not pool:
                last_error = "结果为空"
                continue
            media = random.choice(pool)
            names = self._media_names(media)
            if not names:
                last_error = "条目没有名称"
                continue
            return Puzzle(
                kind=PUZZLE_WORK,
                name=names[0],
                aliases=names,
                summary=strip_markup(media.get("description") or "")[:600],
                facts=self._media_facts(media),
                source=self.name,
            )
        raise SourceError(f"AniList 未能抽到合适的作品（{last_error}）")

    async def random_character(self, attempts: int = 3) -> Puzzle:
        last_error = ""
        for _ in range(attempts):
            page = await self._query(CHARACTER_QUERY)
            pool = [
                c
                for c in (page.get("characters") or [])
                if c and ((c.get("media") or {}).get("nodes") or [])
            ]
            if not pool:
                last_error = "结果为空"
                continue
            character = random.choice(pool)
            name = character.get("name") or {}
            # 正式名放在昵称之前：``alternative`` 里混着昵称（比如 Emma 的
            # 「Antenna」），没有中文名时应回退到正式名而不是昵称。
            # prefer_chinese 仍会把中文名提到最前，所以有中文时不受影响。
            names = prefer_chinese(
                [
                    name.get("native") or "",
                    name.get("full") or "",
                    *(name.get("alternative") or []),
                ]
            )
            if not names:
                last_error = "角色没有名称"
                continue

            nodes = (character.get("media") or {}).get("nodes") or []
            anime_nodes = [n for n in nodes if str(n.get("type") or "") == "ANIME"]
            media = (anime_nodes or nodes)[0]
            work_names = self._media_names(media)

            gender = str(character.get("gender") or "")
            facts = [
                ("性别", {"Male": "男", "Female": "女"}.get(gender, gender)),
                ("年龄", str(character.get("age") or "")),
                (
                    "出场作品首播",
                    str(media.get("seasonYear") or ""),
                ),
                ("作品题材", "、".join(str(g) for g in (media.get("genres") or []))),
            ]
            return Puzzle(
                kind=PUZZLE_CHARACTER,
                name=names[0],
                aliases=names,
                work_name=work_names[0] if work_names else "",
                work_aliases=work_names,
                summary=strip_markup(character.get("description") or "")[:600],
                facts=tuple((k, v) for k, v in facts if v),
                source=self.name,
            )
        raise SourceError(f"AniList 未能抽到合适的角色（{last_error}）")
