"""动漫数据来源库。"""

from __future__ import annotations

from .anilist import AniListSource
from .bangumi import BangumiSource
from .base import AnimeSource, SourceError
from .moegirl import MoegirlSource

#: 配置项 ``data_source`` 的选项文本 → 实现类。
SOURCE_REGISTRY: dict[str, type[AnimeSource]] = {
    BangumiSource.name: BangumiSource,
    AniListSource.name: AniListSource,
    MoegirlSource.name: MoegirlSource,
}

#: 默认来源。Bangumi 中文覆盖最好。
DEFAULT_SOURCE = BangumiSource.name


def build_source(name: str, session, token: str = "") -> AnimeSource:
    """按配置里的来源名构造数据源实例。

    Args:
        name: 配置项里的来源名称，未知值回退到默认来源。
        session: 复用的 aiohttp 会话。
        token: 该来源的 API 密钥，可为空。
    """
    source_class = SOURCE_REGISTRY.get((name or "").strip())
    if source_class is None:
        source_class = SOURCE_REGISTRY[DEFAULT_SOURCE]
    return source_class(session, token)


__all__ = [
    "DEFAULT_SOURCE",
    "SOURCE_REGISTRY",
    "AniListSource",
    "AnimeSource",
    "BangumiSource",
    "MoegirlSource",
    "SourceError",
    "build_source",
]
