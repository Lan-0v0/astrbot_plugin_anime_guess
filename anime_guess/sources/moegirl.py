"""萌娘百科数据源。

萌娘百科开放了 MediaWiki ``api.php``，但只允许一部分模块：``prop=extracts``、
``prop=categories``、``prop=links``、``list=random`` 可用；``list=categorymembers``、
``list=search``、``list=allpages``、``action=parse`` 全部返回
``action-notallowed``。因此分类枚举只能实时抓取分类页 HTML，再用 API 补简介。

分类页翻页有个坑：页面上「下一页」链接写的是 ``pagefrom=``，但服务端只认
``from=``，照抄链接只会拿到空列表。好在排序键是拼音／拉丁，随便给个
``from=<字母>`` 就能跳进分类中段，一次请求即可随机取样，不必顺序翻页。

取数管线：
* 作品：抓 ``Category:日本动画作品``（约 1850 条）的随机一页，挑一条，
  再用 API 取摘要与分类。
* 角色：作品同名分类十有八九不存在（实测 10 部里只有 1 部能出角色），所以改
  走声优：从 ``Category:声优``（约 5200 人）随机抽一位，抓
  ``Category:<声优>配音角色``，再用 ``clcategories`` 批量确认角色所属作品确实
  在 ``Category:日本动画作品`` 里，把游戏／特摄角色挡掉。
"""

from __future__ import annotations

import asyncio
import random
import re
import string
import time
from urllib.parse import quote, urlencode

from ..models import PUZZLE_CHARACTER, PUZZLE_WORK, Puzzle
from .base import AnimeSource, SourceError, prefer_chinese, strip_markup

SITE = "https://mzh.moegirl.org.cn"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

#: 作品来源分类。实测约 1853 个页面。
WORK_CATEGORY = "日本动画作品"

#: 声优分类。实测约 5186 个页面，是角色抽取的入口。
SEIYUU_CATEGORY = "声优"

#: 分类页随机跳转用的排序键种子（分类按拼音／拉丁排序）。
_SEEDS = tuple(string.ascii_uppercase)

#: 角色信号：条目分类里带「XX配音角色」。
_VOICE_CATEGORY = re.compile(r"配音角色$")

#: 分类页列表项。
_LIST_ITEM = re.compile(r'<li><a href="/[^"]+" title="([^"]+)"')
#: 「共 N 个页面」。
_TOTAL = re.compile(r"共([\d,]+)个页面")

#: 分类页缓存有效期（秒）。萌娘百科较慢，缓存一小时。
_CACHE_TTL = 3600

#: 抓到这些后缀的条目不是动画作品本体，抽到就跳过。
_BAD_WORK_SUFFIX = ("(漫画)", "（漫画）", "(小说)", "（小说）", "(游戏)", "（游戏）",
                    "(轻小说)", "（轻小说）", "(音乐)", "（音乐）")

#: 分类页里混进来的非条目页面，前缀命中就丢掉。
_BAD_NAMESPACE = ("User:", "User talk:", "Talk:", "Category:", "Template:",
                  "Help:", "Project:", "File:", "MediaWiki:", "Module:")

#: 角色候选里要排除的：歌曲、专辑、声优本人等。
_BAD_CHAR_CATEGORY = ("日本音乐作品", "日本歌曲", "音乐专辑", "单曲", "配音演员", "日本女性配音员",
                      "日本男性配音员", "消歧义页", "声优", "配音员")

#: 维护性分类，不该出现在给裁判的事实里，也不该被当成作品名。
_MAINTENANCE_PREFIX = ("使用", "带有", "需要", "含有", "因冒号", "屏蔽", "缺少", "自动")

#: 认作「日系 ACG 作品」的分类。
#:
#: 只认 ``日本动画作品`` 会误杀：萌娘百科分类很不统一，《孤独摇滚！》整篇把动画
#: 写在漫画条目里，只挂着 ``日本漫画作品``；《杀戮的天使》是游戏原作动画，只挂
#: ``日本游戏作品``。放宽到这一组既能收回它们，也仍然挡得住特摄（``假面骑士系列
#: 作品``）和国产游戏（``中国游戏作品``）。
_ACG_WORK_CATEGORIES = (
    "日本动画作品",
    "日本漫画作品",
    "日本轻小说作品",
    "日本小说作品",
    "日本游戏作品",
)

#: 一次 ``clcategories`` 校验最多带多少个标题。
_VERIFY_LIMIT = 40

#: 每个角色最多推几个作品名候选去校验。
_WORK_GUESS_PER_CHARACTER = 3

#: 每轮并发试几位声优。单抽失败率不低，并发能把整轮失败的概率压下去；
#: 但萌娘百科会限流，所以并发宽度压到 2，别一次打太多请求。
_PARALLEL_SEIYUU = 2


class MoegirlSource(AnimeSource):
    """从萌娘百科抽取作品或角色。"""

    name = "萌娘百科"

    def __init__(self, session, token: str = "") -> None:
        super().__init__(session, token)
        # 分类页缓存：{(分类名, 种子): (抓取时刻, 标题元组)}
        self._cache: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}
        self._lock = asyncio.Lock()

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": BROWSER_UA}

    async def _api(self, **params) -> dict:
        """调用允许的 MediaWiki API 模块。"""
        params.setdefault("action", "query")
        params.setdefault("format", "json")
        data = await self._get_json(
            f"{SITE}/api.php", headers=self._headers(), params=params
        )
        if isinstance(data, dict) and data.get("error"):
            code = (data["error"] or {}).get("code", "unknown")
            raise SourceError(f"萌娘百科 API 拒绝了请求：{code}")
        return data

    async def _category_page(self, category: str, start: str = ""):
        """抓一页分类列表，返回 ``(标题元组, 总数)``。

        ``start`` 走 ``from=``（而不是页面上写的 ``pagefrom=``，那个服务端不认），
        给个字母就能跳到分类中段。
        """
        query = {"title": f"Category:{category}"}
        if start:
            query["from"] = start
        html = await self._get_text(
            f"{SITE}/index.php?{urlencode(query)}", headers=self._headers()
        )
        anchor = html.find('id="mw-pages"')
        segment = html[anchor:] if anchor >= 0 else html
        titles = tuple(_LIST_ITEM.findall(segment))
        if not titles:
            raise SourceError(f"分类 Category:{category} 没有页面")
        total_match = _TOTAL.search(segment)
        total = int(total_match.group(1).replace(",", "")) if total_match else len(titles)
        return titles, total

    async def _random_from_category(self, category: str, seeded: bool = True):
        """在一个分类里随机取一页成员。

        随机种子决定落在分类的哪一段，一次请求就够；结果按 ``(分类, 种子)`` 缓存。
        """
        seed = random.choice(_SEEDS) if seeded else ""
        key = (category, seed)
        async with self._lock:
            cached = self._cache.get(key)
            if cached and time.time() - cached[0] < _CACHE_TTL:
                return cached[1]

        try:
            titles, _total = await self._category_page(category, seed)
        except SourceError:
            if not seed:
                raise
            # 种子落到了分类末尾之后，退回不带种子的首页。
            titles, _total = await self._category_page(category)
            key = (category, "")

        async with self._lock:
            self._cache[key] = (time.time(), titles)
        return titles

    async def _page_detail(self, titles) -> dict:
        """批量取条目的摘要与分类。``titles`` 最多 20 条以免请求过大。"""
        joined = "|".join(list(titles)[:20])
        data = await self._api(
            prop="extracts|categories",
            titles=joined,
            exintro=1,
            explaintext=1,
            exlimit=20,
            cllimit=500,
        )
        return (data.get("query") or {}).get("pages") or {}

    async def _verify_anime_works(self, names) -> set[str]:
        """从一批名字里挑出确实是日系 ACG 作品的。

        用 ``clcategories`` 只回关心的那几个分类，一次请求判完所有候选，
        特摄和国产游戏角色的作品名会被挡在外面。
        """
        pool = [n for n in dict.fromkeys(names) if n][:_VERIFY_LIMIT]
        if not pool:
            return set()
        data = await self._api(
            prop="categories",
            titles="|".join(pool),
            clcategories="|".join(f"Category:{c}" for c in _ACG_WORK_CATEGORIES),
            cllimit=500,
            redirects=1,
        )
        query = data.get("query") or {}
        verified = {
            str(page.get("title") or "")
            for page in (query.get("pages") or {}).values()
            if page.get("categories")
        }
        # 重定向后标题会变，把原名也算进去（《XX》→《XX (动画)》这类）。
        for hop in query.get("redirects") or []:
            if str(hop.get("to") or "") in verified:
                verified.add(str(hop.get("from") or ""))
        return {name for name in pool if name in verified}

    @staticmethod
    def _categories_of(page: dict) -> tuple[str, ...]:
        return tuple(
            str(c.get("title") or "").replace("Category:", "")
            for c in (page.get("categories") or [])
        )

    @classmethod
    def _informative_categories(cls, cats) -> list[str]:
        """去掉维护性分类和配音分类，剩下的才值得给裁判看。"""
        return [
            c
            for c in cats
            if not c.startswith(_MAINTENANCE_PREFIX) and not _VOICE_CATEGORY.search(c)
        ]

    @staticmethod
    def _clean_title(title: str) -> tuple[str, str]:
        """拆掉消歧义后缀，返回 ``(展示名, 后缀内容)``。

        ``拉姆(Re:从零开始的异世界生活)`` → ``("拉姆", "Re:从零开始的异世界生活")``
        """
        match = re.match(r"^(.*?)[（(]([^（）()]+)[）)]\s*$", title.strip())
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return title.strip(), ""

    @classmethod
    def _usable_titles(cls, titles) -> list[str]:
        """滤掉命名空间页面和子页面，只留正常条目。"""
        clean = []
        for title in titles:
            if title.startswith(_BAD_NAMESPACE):
                continue
            # ``明日方舟/电视动画`` 这类子页面当谜底很怪，取主条目名。
            base = title.split("/", 1)[0].strip()
            if base:
                clean.append(base)
        return list(dict.fromkeys(clean))

    async def random_work(self, attempts: int = 5) -> Puzzle:
        titles = await self._random_from_category(WORK_CATEGORY)
        pool = [
            t
            for t in self._usable_titles(titles)
            if not any(t.endswith(suffix) for suffix in _BAD_WORK_SUFFIX)
        ]
        if not pool:
            pool = self._usable_titles(titles) or list(titles)

        last_error = ""
        for _ in range(attempts):
            title = random.choice(pool)
            try:
                pages = await self._page_detail([title])
            except SourceError as error:
                last_error = str(error)
                continue
            page = next(iter(pages.values()), {})
            if "missing" in page:
                last_error = "条目不存在"
                continue
            extract = strip_markup(page.get("extract") or "")
            if "可以指" in extract[:30] or "消歧义页" in self._categories_of(page):
                last_error = "抽到消歧义页"
                continue

            display, suffix = self._clean_title(title)
            names = prefer_chinese([display, title])
            cats = [
                c
                for c in self._categories_of(page)
                if not c.startswith(_MAINTENANCE_PREFIX)
            ]
            facts = [
                ("萌娘百科分类", "、".join(cats[:12])),
                ("条目标题限定", suffix),
            ]
            return Puzzle(
                kind=PUZZLE_WORK,
                name=names[0],
                aliases=names,
                summary=extract[:600],
                facts=tuple((k, v) for k, v in facts if v),
                source=self.name,
            )
        raise SourceError(f"萌娘百科未能抽到合适的作品（{last_error}）")

    def _work_guesses(self, title: str, cats) -> list[str]:
        """猜这个角色属于哪部作品：先看标题后缀，再看分类。"""
        _display, suffix = self._clean_title(title)
        guesses = [suffix] if suffix else []
        guesses += self._informative_categories(cats)
        return [g for g in dict.fromkeys(guesses) if g][:_WORK_GUESS_PER_CHARACTER]

    async def random_character(self, attempts: int = 4) -> Puzzle:
        """从声优的「配音角色」分类里抽角色，再确认作品是日本动画。

        ``Category:声优`` 里混着中文配音员和真人演员（站内没有「日本声优」这种
        更窄的分类可用），单抽一位失败率不低，所以每轮并发试几位，
        谁能凑出「有配音角色分类 + 作品在日本动画作品里」就用谁。
        """
        last_error = "没有声优能凑出日系 ACG 角色"
        for _ in range(attempts):
            # 每轮换一个排序键种子重取声优名单：一个种子只覆盖 200 人，若这一段
            # 恰好都是中文配音员，同一份名单里再抽几次也还是失败。
            pool = self._usable_titles(
                await self._random_from_category(SEIYUU_CATEGORY)
            )
            if not pool:
                last_error = "声优分类为空"
                continue
            picks = random.sample(pool, min(_PARALLEL_SEIYUU, len(pool)))
            results = await asyncio.gather(
                *(self._draw_from_seiyuu(who) for who in picks),
                return_exceptions=True,
            )
            hits = [r for r in results if isinstance(r, Puzzle)]
            if hits:
                return random.choice(hits)
            for result in results:
                if isinstance(result, Exception):
                    last_error = str(result) or type(result).__name__
        raise SourceError(f"萌娘百科未能抽到合适的角色（{last_error}）")

    async def _draw_from_seiyuu(self, seiyuu: str) -> Puzzle:
        """抓一位声优的配音角色，挑出属于日本动画作品的那个。"""
        try:
            members = await self._random_from_category(
                f"{seiyuu}配音角色", seeded=False
            )
        except SourceError as error:
            raise SourceError(f"{seiyuu} 没有配音角色分类") from error

        sample = self._usable_titles(members)
        random.shuffle(sample)
        pages = await self._page_detail(sample[:20])

        # 先攒候选角色和它们可能的作品名，再一次性校验作品。
        candidates: list[tuple[dict, list[str]]] = []
        for page in pages.values():
            if "missing" in page:
                continue
            cats = self._categories_of(page)
            if not any(_VOICE_CATEGORY.search(c) for c in cats):
                continue
            if any(bad in cats for bad in _BAD_CHAR_CATEGORY):
                continue
            guesses = self._work_guesses(str(page.get("title") or ""), cats)
            if guesses:
                candidates.append((page, guesses))
        if not candidates:
            raise SourceError(f"{seiyuu} 的角色条目都不可用")

        verified = await self._verify_anime_works(
            [g for _page, guesses in candidates for g in guesses]
        )
        usable = [
            (page, [g for g in guesses if g in verified])
            for page, guesses in candidates
        ]
        usable = [(page, works) for page, works in usable if works]
        if not usable:
            raise SourceError(f"{seiyuu} 的角色都不属于日系 ACG 作品")

        page, works = random.choice(usable)
        return self._build_character(page, works[0])

    def _build_character(self, page: dict, work_name: str) -> Puzzle:
        title = str(page.get("title") or "")
        display, _suffix = self._clean_title(title)
        names = prefer_chinese([display, title])
        cats = self._categories_of(page)
        seiyuu = [c.replace("配音角色", "") for c in cats if _VOICE_CATEGORY.search(c)]
        traits = [c for c in self._informative_categories(cats) if c != work_name]
        facts = [
            ("配音", "、".join(seiyuu[:6])),
            ("萌属性／分类", "、".join(traits[:14])),
        ]
        return Puzzle(
            kind=PUZZLE_CHARACTER,
            name=names[0],
            aliases=names,
            work_name=work_name,
            work_aliases=(work_name,),
            summary=strip_markup(page.get("extract") or "")[:600],
            facts=tuple((k, v) for k, v in facts if v),
            source=self.name,
        )


__all__ = ["MoegirlSource", "quote"]
