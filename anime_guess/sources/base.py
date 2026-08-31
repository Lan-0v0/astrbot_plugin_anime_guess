"""Base contract shared by every anime data source."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

import aiohttp

from ..models import Puzzle

# 单次 HTTP 请求超时（秒）。萌娘百科分类页较慢，留足余量。
REQUEST_TIMEOUT = 30

# 判定"是中文名"的正则：含汉字且不含日文假名。
_HAN = re.compile(r"[一-鿿㐀-䶿]")
_KANA = re.compile(r"[぀-ヿ]")


def looks_chinese(text: str) -> bool:
    """判断一个名字是否更像中文而非日文。

    AniList 的 ``synonyms`` 把各语言译名混在一起，需要挑出中文的那条。
    """
    return bool(text) and bool(_HAN.search(text)) and not _KANA.search(text)


def strip_markup(text: str) -> str:
    """清掉简介里的 HTML 标签与 wiki 记号，压平空白。"""
    if not text:
        return ""
    cleaned = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", cleaned)
    cleaned = re.sub(r"'''?|__|~!.*?!~", " ", cleaned)
    return " ".join(cleaned.split())


def dedupe(names) -> tuple[str, ...]:
    """去重并保序，同时丢掉空串。"""
    seen: dict[str, None] = {}
    for name in names:
        text = str(name or "").strip()
        if text and text not in seen:
            seen[text] = None
    return tuple(seen)


def prefer_chinese(names) -> tuple[str, ...]:
    """把中文名排到最前面，其余保持原顺序。

    用户选定的策略是「优先中文，缺失就用原名」，因此这里只做排序，
    不丢弃任何名字——非中文名仍要留给 LLM 裁判做跨语言判定。
    """
    ordered = dedupe(names)
    chinese = [name for name in ordered if looks_chinese(name)]
    others = [name for name in ordered if not looks_chinese(name)]
    return tuple(chinese + others)


class SourceError(RuntimeError):
    """数据源取数失败。调用方据此给出可读的提示。"""


class AnimeSource(ABC):
    """动漫数据来源库的统一接口。

    子类只需实现取作品和取角色两个方法，网络会话由基类持有。
    """

    #: 展示名称，与配置项选项文本一致。
    name = ""

    def __init__(self, session: aiohttp.ClientSession, token: str = "") -> None:
        self._session = session
        self._token = (token or "").strip()

    @property
    def session(self) -> aiohttp.ClientSession:
        return self._session

    @property
    def token(self) -> str:
        return self._token

    @abstractmethod
    async def random_work(self) -> Puzzle:
        """随机取一部作品作为谜底。"""

    @abstractmethod
    async def random_character(self) -> Puzzle:
        """随机取一个角色作为谜底。"""

    async def _get_json(self, url: str, *, headers=None, params=None):
        """发一个 GET 并解析 JSON，非 2xx 统一抛 :class:`SourceError`。"""
        try:
            async with self._session.get(
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if response.status >= 400:
                    raise SourceError(f"{url} 返回 HTTP {response.status}")
                return await response.json(content_type=None)
        except SourceError:
            raise
        except Exception as error:
            raise SourceError(f"请求 {url} 失败：{type(error).__name__}") from error

    async def _post_json(self, url: str, *, headers=None, json_body=None):
        """发一个 POST 并解析 JSON，非 2xx 统一抛 :class:`SourceError`。"""
        try:
            async with self._session.post(
                url,
                headers=headers,
                json=json_body,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if response.status >= 400:
                    raise SourceError(f"{url} 返回 HTTP {response.status}")
                return await response.json(content_type=None)
        except SourceError:
            raise
        except Exception as error:
            raise SourceError(f"请求 {url} 失败：{type(error).__name__}") from error

    async def _get_text(self, url: str, *, headers=None, params=None) -> str:
        """发一个 GET 并取回文本，用于需要解析 HTML 的来源。"""
        try:
            async with self._session.get(
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if response.status >= 400:
                    raise SourceError(f"{url} 返回 HTTP {response.status}")
                return await response.text()
        except SourceError:
            raise
        except Exception as error:
            raise SourceError(f"请求 {url} 失败：{type(error).__name__}") from error
