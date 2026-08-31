"""让测试能在没有 AstrBot 运行时的环境下导入插件模块。

``anime_guess.judge`` 会 ``from astrbot.api import logger``，这里塞一个最小的
假模块进 ``sys.modules``，避免测试必须跑在完整的 AstrBot 里。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def _install_astrbot_stub() -> None:
    """只在真的没有 astrbot 时安装桩模块。"""
    try:
        import astrbot.api

        return
    except ImportError:
        pass

    class _NullLogger:
        def _noop(self, *args, **kwargs):
            return None

        info = warning = error = debug = exception = _noop

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = _NullLogger()
    api.AstrBotConfig = dict
    astrbot.api = api

    sys.modules.setdefault("astrbot", astrbot)
    sys.modules.setdefault("astrbot.api", api)


_install_astrbot_stub()
