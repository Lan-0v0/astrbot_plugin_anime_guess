"""数据源解析测试。全部用假响应，不打真网络。"""

from __future__ import annotations

import asyncio
import string

import pytest

from anime_guess.models import PUZZLE_CHARACTER, PUZZLE_WORK
from anime_guess.sources import DEFAULT_SOURCE, SOURCE_REGISTRY, build_source
from anime_guess.sources.anilist import AniListSource
from anime_guess.sources.bangumi import BangumiSource
from anime_guess.sources.base import (
    SourceError,
    dedupe,
    looks_chinese,
    prefer_chinese,
    strip_markup,
)
from anime_guess.sources.moegirl import MoegirlSource


# --------------------------------------------------------------------------- #
# 基础工具函数
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("命运石之门", True),
        ("进击的巨人", True),
        ("シュタインズ・ゲート", False),  # 假名
        ("進撃の巨人", False),  # 含假名「の」
        ("Steins;Gate", False),
        ("", False),
        ("雷姆", True),
    ],
)
def test_looks_chinese(text, expected):
    assert looks_chinese(text) is expected


def test_strip_markup_removes_html_and_wiki():
    assert strip_markup("a<br>b") == "a b"
    assert strip_markup("<i>斜体</i>文字") == "斜体 文字"
    assert strip_markup("[[链接|显示]]结束") == "显示结束"
    assert strip_markup("'''粗体'''") == "粗体"
    assert strip_markup("  多  空格 \n 换行 ") == "多 空格 换行"
    assert strip_markup("") == ""


def test_dedupe_preserves_order_and_drops_blanks():
    assert dedupe(["a", "b", "a", "", None, " ", "c"]) == ("a", "b", "c")


def test_prefer_chinese_moves_chinese_first_and_keeps_rest():
    result = prefer_chinese(["Shingeki no Kyojin", "進撃の巨人", "进击的巨人"])
    assert result[0] == "进击的巨人"
    # 非中文名不被丢弃，要留给 LLM 做跨语言判定
    assert set(result) == {"Shingeki no Kyojin", "進撃の巨人", "进击的巨人"}


def test_prefer_chinese_all_foreign_keeps_original_order():
    result = prefer_chinese(["Steins;Gate", "シュタインズ・ゲート"])
    assert result == ("Steins;Gate", "シュタインズ・ゲート")


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
def test_registry_has_three_sources():
    assert set(SOURCE_REGISTRY) == {"AniList", "萌娘百科", "Bangumi"}
    assert DEFAULT_SOURCE == "Bangumi"


def test_build_source_by_name_and_fallback():
    assert isinstance(build_source("AniList", None), AniListSource)
    assert isinstance(build_source("萌娘百科", None), MoegirlSource)
    assert isinstance(build_source("Bangumi", None), BangumiSource)
    # 未知名称回退到默认
    assert isinstance(build_source("不存在的库", None), BangumiSource)
    assert isinstance(build_source("", None), BangumiSource)


def test_build_source_passes_token():
    source = build_source("Bangumi", None, "  tok  ")
    assert source.token == "tok"


# --------------------------------------------------------------------------- #
# Bangumi
# --------------------------------------------------------------------------- #
SUBJECT = {
    "id": 10380,
    "name": "STEINS;GATE",
    "name_cn": "命运石之门",
    "date": "2011-04-06",
    "platform": "TV",
    "eps": 24,
    "rating": {"score": 9.1},
    "summary": "故事发生在秋叶原……",
    "tags": [{"name": "科幻"}, {"name": "中二病"}, {"name": "游戏改"}],
}

CHARACTER_DETAIL = {
    "id": 12393,
    "name": "牧瀬紅莉栖",
    "gender": "female",
    "summary": "维克多·孔多利亚大学脑科学研究所的研究员。",
    "infobox": [
        {"key": "简体中文名", "value": "牧濑红莉栖"},
        {
            "key": "别名",
            "value": [{"v": "克莉丝汀娜"}, {"k": "英文名", "v": "Kurisu Makise"}],
        },
        {"key": "性别", "value": "女"},
    ],
}


class FakeBangumi(BangumiSource):
    """把 HTTP 层换成固定响应。"""

    def __init__(self, *, subject=None, cast=None, detail=None, total=5):
        super().__init__(None, "")
        self._subject = subject if subject is not None else SUBJECT
        self._cast = cast
        self._detail = detail if detail is not None else CHARACTER_DETAIL
        self._total = total

    async def _post_json(self, url, *, headers=None, json_body=None):
        if "offset=0" in url and "limit=1" in url:
            return {"total": self._total, "data": [self._subject]}
        return {"total": self._total, "data": [self._subject]}

    async def _get_json(self, url, *, headers=None, params=None):
        if "/characters" in url and "/subjects/" in url:
            if self._cast is None:
                raise SourceError("no cast")
            return self._cast
        return self._detail


def test_bangumi_work_prefers_chinese_name():
    puzzle = asyncio.run(FakeBangumi().random_work())
    assert puzzle.kind == PUZZLE_WORK
    assert puzzle.name == "命运石之门"
    assert "STEINS;GATE" in puzzle.aliases
    assert puzzle.source == "Bangumi"
    facts = dict(puzzle.facts)
    assert facts["放送日期"] == "2011-04-06"
    assert facts["评分"] == "9.1"
    assert "科幻" in facts["标签"]
    assert puzzle.summary.startswith("故事发生在秋叶原")


def test_bangumi_work_falls_back_to_original_name():
    subject = dict(SUBJECT, name_cn="")
    puzzle = asyncio.run(FakeBangumi(subject=subject).random_work())
    assert puzzle.name == "STEINS;GATE"


def test_bangumi_rejects_western_animation():
    subject = dict(SUBJECT, tags=[{"name": "欧美动画"}, {"name": "搞笑"}])
    with pytest.raises(SourceError, match="非日本动画"):
        asyncio.run(FakeBangumi(subject=subject).random_work())


def test_bangumi_character_uses_infobox_chinese_name():
    cast = [
        {"id": 12393, "relation": "主角"},
        {"id": 999, "relation": "客串"},
    ]
    puzzle = asyncio.run(FakeBangumi(cast=cast).random_character())
    assert puzzle.kind == PUZZLE_CHARACTER
    assert puzzle.name == "牧濑红莉栖"
    assert "牧瀬紅莉栖" in puzzle.aliases
    assert "Kurisu Makise" in puzzle.aliases
    assert puzzle.work_name == "命运石之门"
    assert puzzle.reveal_text() == "《命运石之门》中的 牧濑红莉栖"
    assert dict(puzzle.facts)["性别"] == "女"


def test_bangumi_character_prefers_main_over_supporting():
    cast = [
        {"id": 1, "relation": "配角"},
        {"id": 12393, "relation": "主角"},
    ]
    puzzle = asyncio.run(FakeBangumi(cast=cast).random_character())
    assert dict(puzzle.facts)["在作品中的定位"] == "主角"


def test_bangumi_character_errors_when_no_cast():
    with pytest.raises(SourceError, match="角色"):
        asyncio.run(FakeBangumi(cast=[]).random_character())


def test_bangumi_headers_include_token_when_set():
    assert "Authorization" not in FakeBangumi()._headers()
    source = BangumiSource(None, "abc")
    assert source._headers()["Authorization"] == "Bearer abc"
    assert "User-Agent" in source._headers()


# --------------------------------------------------------------------------- #
# AniList
# --------------------------------------------------------------------------- #
MEDIA = {
    "title": {
        "romaji": "Shingeki no Kyojin",
        "english": "Attack on Titan",
        "native": "進撃の巨人",
    },
    "synonyms": ["进击的巨人", "L'Attacco dei Giganti"],
    "genres": ["Action", "Drama"],
    "seasonYear": 2013,
    "season": "SPRING",
    "format": "TV",
    "episodes": 25,
    "averageScore": 85,
    "studios": {"nodes": [{"name": "Wit Studio"}]},
    "description": "Several hundred years ago...<br>humans were...",
}

ANILIST_CHARACTER = {
    "name": {
        "full": "Levi",
        "native": "リヴァイ",
        "alternative": ["利威尔", "Captain Levi"],
    },
    "gender": "Male",
    "age": "30s",
    "favourites": 43018,
    "description": "__Height:__ 160 cm",
    "media": {"nodes": [dict(MEDIA, type="ANIME")]},
}


class FakeAniList(AniListSource):
    def __init__(self, payload):
        super().__init__(None, "")
        self._payload = payload

    async def _post_json(self, url, *, headers=None, json_body=None):
        return self._payload


def test_anilist_work_prefers_chinese_synonym():
    puzzle = asyncio.run(FakeAniList({"data": {"Page": {"media": [MEDIA]}}}).random_work())
    assert puzzle.name == "进击的巨人"
    assert "進撃の巨人" in puzzle.aliases
    assert "Shingeki no Kyojin" in puzzle.aliases
    facts = dict(puzzle.facts)
    assert facts["首播"] == "2013 年春季"
    assert facts["制作公司"] == "Wit Studio"
    assert facts["均分"] == "85/100"
    assert "<br>" not in puzzle.summary


def test_anilist_work_without_chinese_uses_native():
    media = dict(MEDIA, synonyms=[])
    puzzle = asyncio.run(FakeAniList({"data": {"Page": {"media": [media]}}}).random_work())
    # 无中文时回退原名，交给 LLM 跨语言判定
    assert puzzle.name == "進撃の巨人"
    assert "Shingeki no Kyojin" in puzzle.aliases


def test_anilist_character_prefers_chinese_alternative():
    payload = {"data": {"Page": {"characters": [ANILIST_CHARACTER]}}}
    puzzle = asyncio.run(FakeAniList(payload).random_character())
    assert puzzle.kind == PUZZLE_CHARACTER
    assert puzzle.name == "利威尔"
    assert "リヴァイ" in puzzle.aliases
    assert puzzle.work_name == "进击的巨人"
    assert dict(puzzle.facts)["性别"] == "男"


def test_anilist_character_without_chinese_falls_back_to_canonical_not_nickname():
    """回归：``alternative`` 里混着昵称，无中文名时不能让昵称当谜底。

    实测抽到《约定的梦幻岛》的 Emma 时，首选名曾变成她的昵称「Antenna」。
    """
    character = {
        "name": {
            "full": "Emma",
            "native": "エマ",
            # Antenna 是昵称，且排在正式名之前
            "alternative": ["Antenna", "Emma"],
        },
        "gender": "Female",
        "media": {"nodes": [dict(MEDIA, type="ANIME")]},
    }
    payload = {"data": {"Page": {"characters": [character]}}}
    puzzle = asyncio.run(FakeAniList(payload).random_character())
    assert puzzle.name == "エマ"
    # 昵称仍要保留在别名里，供 LLM 裁判判定
    assert "Antenna" in puzzle.aliases
    assert "Emma" in puzzle.aliases


def test_anilist_work_without_chinese_falls_back_to_native_not_synonym():
    """回归：``synonyms`` 里混着各国译名，无中文名时不能让外语译名当谜底。"""
    media = dict(
        MEDIA,
        title={
            "native": "あっちこっち 第13話",
            "romaji": "Acchi Kocchi Episode 13",
            "english": None,
        },
        synonyms=["Acchi Kocchi: PLACE=PRINCESS", "Place to Place OVA"],
    )
    puzzle = asyncio.run(FakeAniList({"data": {"Page": {"media": [media]}}}).random_work())
    assert puzzle.name == "あっちこっち 第13話"
    assert "Place to Place OVA" in puzzle.aliases


def test_anilist_chinese_still_wins_over_canonical():
    """中文名即使排在 synonyms 末尾，也要优先于日文原题。"""
    media = dict(MEDIA, synonyms=["L'Attacco dei Giganti", "进击的巨人"])
    puzzle = asyncio.run(FakeAniList({"data": {"Page": {"media": [media]}}}).random_work())
    assert puzzle.name == "进击的巨人"
    assert "進撃の巨人" in puzzle.aliases


def test_anilist_surfaces_graphql_errors():
    payload = {"errors": [{"message": "Too Many Requests"}]}
    with pytest.raises(SourceError, match="Too Many Requests"):
        asyncio.run(FakeAniList(payload).random_work())


def test_anilist_errors_on_empty_page():
    with pytest.raises(SourceError, match="作品"):
        asyncio.run(FakeAniList({"data": {"Page": {"media": []}}}).random_work())


def test_anilist_character_skips_entries_without_media():
    payload = {
        "data": {
            "Page": {
                "characters": [
                    {"name": {"full": "NoMedia"}, "media": {"nodes": []}},
                    ANILIST_CHARACTER,
                ]
            }
        }
    }
    puzzle = asyncio.run(FakeAniList(payload).random_character())
    assert puzzle.name == "利威尔"


# --------------------------------------------------------------------------- #
# 萌娘百科
# --------------------------------------------------------------------------- #
CATEGORY_HTML = """
<div id="mw-pages">
<h2>分类“日本动画作品”中的页面</h2>
<p>以下200个页面属于本分类，共1,853个页面。</p>
（上一页）（<a href="/index.php?title=Category:X&amp;pagefrom=%E8%8B%8D#mw-pages">下一页</a>）
<div class="mw-category">
<ul><li><a href="/A" title="孤独摇滚！">孤独摇滚！</a></li>
<li><a href="/B" title="艾玛(漫画)">艾玛(漫画)</a></li>
<li><a href="/C" title="命运石之门">命运石之门</a></li></ul>
</div></div>
"""


class FakeMoegirl(MoegirlSource):
    def __init__(self, *, html=CATEGORY_HTML, pages=None, verified=()):
        super().__init__(None, "")
        self._html = html
        self._pages = pages or {}
        self._verified = set(verified)
        self.api_calls = 0
        self.urls: list[str] = []

    async def _get_text(self, url, *, headers=None, params=None):
        self.urls.append(url)
        return self._html

    async def _api(self, **params):
        self.api_calls += 1
        if "clcategories" in params:
            titles = str(params.get("titles") or "").split("|")
            return {
                "query": {
                    "pages": {
                        str(i): {
                            "title": t,
                            "categories": (
                                [{"title": "Category:日本动画作品"}]
                                if t in self._verified
                                else []
                            ),
                        }
                        for i, t in enumerate(titles)
                    },
                    "redirects": [],
                }
            }
        return {"query": {"pages": self._pages}}


def test_moegirl_parses_category_html():
    source = FakeMoegirl()
    titles, total = asyncio.run(source._category_page("日本动画作品"))
    assert titles == ("孤独摇滚！", "艾玛(漫画)", "命运石之门")
    assert total == 1853


def test_moegirl_category_paging_uses_from_not_pagefrom():
    """服务端只认 ``from=``，页面上写的 ``pagefrom=`` 会返回空列表。"""
    source = FakeMoegirl()
    asyncio.run(source._category_page("日本动画作品", "M"))
    assert "from=M" in source.urls[-1]
    assert "pagefrom" not in source.urls[-1]


def test_moegirl_category_error_when_no_pages():
    source = FakeMoegirl(html="<html>nothing</html>")
    with pytest.raises(SourceError, match="没有页面"):
        asyncio.run(source._category_page("空分类"))


def test_moegirl_skips_namespace_and_subpages():
    titles = ("User:Someone/地球外少年少女", "明日方舟/电视动画", "Category:X", "迷宫饭")
    assert MoegirlSource._usable_titles(titles) == ["明日方舟", "迷宫饭"]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("拉姆(Re:从零开始的异世界生活)", ("拉姆", "Re:从零开始的异世界生活")),
        ("惠惠（为美好的世界献上祝福）", ("惠惠", "为美好的世界献上祝福")),
        ("命运石之门", ("命运石之门", "")),
    ],
)
def test_moegirl_clean_title(title, expected):
    assert MoegirlSource._clean_title(title) == expected


def test_moegirl_work_skips_manga_suffix_and_disambiguation():
    pages = {
        "1": {
            "title": "命运石之门",
            "extract": "《命运石之门》是由5pb.制作的游戏改编动画。",
            "categories": [
                {"title": "Category:日本动画作品"},
                {"title": "Category:使用标题格式化的页面"},
            ],
        }
    }
    source = FakeMoegirl(pages=pages)
    puzzle = asyncio.run(source.random_work())
    assert puzzle.kind == PUZZLE_WORK
    # 「艾玛(漫画)」应被后缀过滤掉，剩下的两条都可接受
    assert puzzle.name in ("孤独摇滚！", "命运石之门")
    assert puzzle.source == "萌娘百科"
    # 「使用标题格式化的页面」这类维护分类不该进事实
    assert "使用标题格式化的页面" not in dict(puzzle.facts).get("萌娘百科分类", "")


def test_moegirl_work_rejects_disambiguation_page():
    pages = {
        "1": {
            "title": "惠惠",
            "extract": "惠惠可以指：",
            "categories": [{"title": "Category:消歧义页"}],
        }
    }
    source = FakeMoegirl(pages=pages)
    with pytest.raises(SourceError, match="作品"):
        asyncio.run(source.random_work())


def test_moegirl_character_needs_voice_category():
    pages = {
        "1": {
            "title": "波奇酱(孤独摇滚！)",
            "extract": "后藤一里是《孤独摇滚！》的主角。",
            "categories": [
                {"title": "Category:青山吉能配音角色"},
                {"title": "Category:粉发"},
                {"title": "Category:使用标题格式化的页面"},
            ],
        }
    }
    source = FakeMoegirl(pages=pages, verified={"孤独摇滚！"})
    puzzle = asyncio.run(source.random_character())
    assert puzzle.kind == PUZZLE_CHARACTER
    assert puzzle.name == "波奇酱"
    assert puzzle.work_name == "孤独摇滚！"
    facts = dict(puzzle.facts)
    assert facts["配音"] == "青山吉能"
    assert "粉发" in facts["萌属性／分类"]
    assert "使用标题格式化的页面" not in facts["萌属性／分类"]


def test_moegirl_character_rejects_song_pages():
    pages = {
        "1": {
            "title": "Sincerely",
            "extract": "《Sincerely》是TV动画的片头曲。",
            "categories": [{"title": "Category:日本音乐作品"}],
        }
    }
    source = FakeMoegirl(pages=pages)
    with pytest.raises(SourceError, match="角色"):
        asyncio.run(source.random_character())


def test_moegirl_character_rejects_unverified_work():
    """游戏／特摄角色：配音分类齐全，但作品不在日本动画作品分类里。"""
    pages = {
        "1": {
            "title": "凑壬晴",
            "extract": "凑壬晴是《假面骑士OOO》的角色。",
            "categories": [
                {"title": "Category:钱文青配音角色"},
                {"title": "Category:假面骑士OOO"},
            ],
        }
    }
    source = FakeMoegirl(pages=pages, verified=set())
    with pytest.raises(SourceError, match="角色"):
        asyncio.run(source.random_character())


def test_moegirl_verify_asks_for_all_acg_categories():
    """只认「日本动画作品」会误杀《孤独摇滚！》这类只挂漫画分类的条目。"""
    captured = {}

    class Capturing(FakeMoegirl):
        async def _api(self, **params):
            captured.update(params)
            return await super()._api(**params)

    source = Capturing(verified={"孤独摇滚！"})
    verified = asyncio.run(source._verify_anime_works(["孤独摇滚！", "灰烬战线"]))
    assert verified == {"孤独摇滚！"}
    asked = captured["clcategories"]
    for name in ("日本动画作品", "日本漫画作品", "日本游戏作品", "日本轻小说作品"):
        assert f"Category:{name}" in asked


def test_moegirl_verify_empty_input_skips_request():
    source = FakeMoegirl()
    assert asyncio.run(source._verify_anime_works([])) == set()
    assert source.api_calls == 0


def test_moegirl_caches_category_pages():
    source = FakeMoegirl()

    async def scenario():
        first = await source._random_from_category("日本动画作品", seeded=False)
        second = await source._random_from_category("日本动画作品", seeded=False)
        return first, second

    first, second = asyncio.run(scenario())
    assert first == second
    assert ("日本动画作品", "") in source._cache
    # 缓存命中就不该再发第二次 HTTP 请求
    assert len(source.urls) == 1


def test_moegirl_seeded_draw_caches_per_seed():
    source = FakeMoegirl()
    asyncio.run(source._random_from_category("声优"))
    (category, seed), _entry = next(iter(source._cache.items()))
    assert category == "声优"
    assert seed in string.ascii_uppercase
