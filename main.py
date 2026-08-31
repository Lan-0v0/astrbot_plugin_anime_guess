"""Anime Guess —— 猜动漫作品／角色的 AstrBot 插件。

玩法：开局后 bot 抽一个谜底，玩家自由提问，LLM 裁判只答「是／否／不清楚」，
任何人都可以用「猜 xxx」抢答；猜对的人进排行榜。
"""

from __future__ import annotations

import asyncio

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

try:
    from .anime_guess import (
        HELP_TEXT,
        START_TEXT,
        GameManager,
        Judge,
        Leaderboard,
        Puzzle,
    )
    from .anime_guess.models import PUZZLE_CHARACTER
    from .anime_guess.parsing import (
        is_command_like,
        looks_like_question,
        mentions_bot,
        parse_action,
        parse_guess,
    )
    from .anime_guess.sources import DEFAULT_SOURCE, SourceError, build_source
except ImportError:  # pragma: no cover - AstrBot 有时把插件目录直接加进 sys.path。
    from anime_guess import (
        HELP_TEXT,
        START_TEXT,
        GameManager,
        Judge,
        Leaderboard,
        Puzzle,
    )
    from anime_guess.models import PUZZLE_CHARACTER
    from anime_guess.parsing import (
        is_command_like,
        looks_like_question,
        mentions_bot,
        parse_action,
        parse_guess,
    )
    from anime_guess.sources import DEFAULT_SOURCE, SourceError, build_source


@register(
    "astrbot_plugin_anime_guess",
    "Lan-0v0",
    "猜动漫作品／角色的多人问答游戏，支持 AniList、萌娘百科、Bangumi 三个数据源与 LLM 裁判。",
    "v0.0.2",
    "https://github.com/Lan-0v0/astrbot_plugin_anime_guess",
)
class AnimeGuessPlugin(Star):
    """插件主体。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self.games = GameManager()
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

        data_dir = StarTools.get_data_dir("astrbot_plugin_anime_guess")
        self.leaderboard = Leaderboard(data_dir / "leaderboard.json")

    async def initialize(self) -> None:
        self.leaderboard.load()
        logger.info(
            "Anime Guess 已加载。数据来源库=%s，自然语言开局=%s，排行榜=%s",
            self._source_name(),
            "开" if self._natural_language_enabled() else "关",
            self.leaderboard.path,
        )

    async def terminate(self) -> None:
        await self.games.clear()
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # ------------------------------------------------------------------ #
    # 配置读取
    # ------------------------------------------------------------------ #
    def _source_name(self) -> str:
        return str(self.config.get("data_source") or DEFAULT_SOURCE).strip()

    def _natural_language_enabled(self) -> bool:
        return bool(self.config.get("enable_natural_language", True))

    def _question_allowed(self, event: AstrMessageEvent) -> bool:
        """开了「仅在被@时回答提问」时，群聊提问需要 @bot 才受理。

        私聊没有 @ 的概念，始终放行。
        """
        if not bool(self.config.get("answer_only_when_mentioned", False)):
            return True
        message_object = getattr(event, "message_obj", None)
        if not str(getattr(message_object, "group_id", "") or ""):
            return True
        return mentions_bot(
            getattr(message_object, "message", None),
            getattr(message_object, "self_id", ""),
        )

    def _source_token(self) -> str:
        """取当前来源对应的密钥。两个来源各自一个字段。"""
        source = self._source_name()
        if source == "AniList":
            return str(self.config.get("anilist_token") or "").strip()
        if source == "Bangumi":
            return str(self.config.get("bangumi_token") or "").strip()
        return ""

    def _judge(self) -> Judge:
        return Judge(self.context, str(self.config.get("judge_provider") or "").strip())

    async def _http_session(self) -> aiohttp.ClientSession:
        """懒建并复用一个 aiohttp 会话。"""
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession()
            return self._session

    async def _draw_puzzle(self, kind: str) -> Puzzle:
        """按配置的来源抽一个谜底。"""
        session = await self._http_session()
        source = build_source(self._source_name(), session, self._source_token())
        if kind == PUZZLE_CHARACTER:
            return await source.random_character()
        return await source.random_work()

    # ------------------------------------------------------------------ #
    # 开局／结束的公共实现，供指令与函数工具复用
    # ------------------------------------------------------------------ #
    async def _begin_game(self, event: AstrMessageEvent, requested_kind: str) -> str:
        """开一局，返回要发给用户的文本。"""
        origin = event.unified_msg_origin
        if self.games.has_active(origin):
            return "本群已经有一局在进行了，先“/ag 结束”再开新的吧~"

        kind = self.games.pick_kind(requested_kind)
        try:
            puzzle = await self._draw_puzzle(kind)
        except SourceError as error:
            logger.warning("抽取谜底失败：%s", error)
            return f"从「{self._source_name()}」取数据失败了：{error}\n换个数据来源库或稍后再试。"
        except Exception as error:  # noqa: BLE001 - 网络层异常类型很多。
            logger.error(
                "抽取谜底出现未预期的错误。错误类型：%s，错误：%s",
                type(error).__name__,
                error,
            )
            return "抽取谜底时出错了，稍后再试吧。"

        await self.games.start(
            origin, puzzle, event.get_sender_id(), event.get_sender_name()
        )
        logger.info(
            "对局开始。会话=%s，类型=%s，谜底=%s，来源=%s",
            origin,
            puzzle.kind_label,
            puzzle.name,
            puzzle.source,
        )
        return START_TEXT

    async def _finish_game(self, event: AstrMessageEvent) -> str:
        """结束当前对局，返回揭晓文案。"""
        session = await self.games.stop(event.unified_msg_origin)
        if session is None:
            return "现在没有进行中的游戏哦，用“/ag 随机”开一局？"
        return f"游戏结束，谜底：{session.puzzle.reveal_text()}"

    # ------------------------------------------------------------------ #
    # /ag 指令
    # ------------------------------------------------------------------ #
    @filter.command("ag", alias={"animeguess", "动漫猜谜"})
    async def ag_command(self, event: AstrMessageEvent, action: str = ""):
        """Anime Guess 主指令。不带参数时显示帮助。

        用一个指令自行分派子命令，而不是 command_group：指令组在只输入 `/ag`
        时会抛 ValueError 打印指令树，无法输出规定的帮助文案。
        """
        choice = parse_action(action)

        if choice == "work":
            yield event.plain_result(await self._begin_game(event, "作品"))
        elif choice == "character":
            yield event.plain_result(await self._begin_game(event, "角色"))
        elif choice == "random":
            yield event.plain_result(await self._begin_game(event, "随机"))
        elif choice == "stop":
            yield event.plain_result(await self._finish_game(event))
        elif choice == "rank":
            yield event.plain_result(self.leaderboard.render())
        else:
            yield event.plain_result(HELP_TEXT)

    # ------------------------------------------------------------------ #
    # 对局中的全群监听：抢答与提问
    # ------------------------------------------------------------------ #
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def on_message(self, event: AstrMessageEvent):
        """对局进行中时，监听本会话的所有消息。

        群里任何人都可以「猜 xxx」抢答，猜对算抢答者的成绩，而不是房主的。
        """
        origin = event.unified_msg_origin
        session = self.games.get(origin)
        if session is None:
            return

        text = str(event.message_str or "").strip()
        if not text or is_command_like(text):
            return

        guess = parse_guess(text)
        if guess:
            async for result in self._handle_guess(event, session, guess):
                yield result
            return

        if not looks_like_question(text) or not self._question_allowed(event):
            return

        async for result in self._handle_question(event, session, text):
            yield result

    async def _handle_guess(self, event: AstrMessageEvent, session, guess: str):
        """处理一次抢答。"""
        if not guess:
            yield event.plain_result("要猜什么呢？格式：猜 蕾姆")
            return

        session.guess_count += 1
        try:
            correct = await self._judge().check_guess(
                event.unified_msg_origin, session.puzzle, guess
            )
        except Exception as error:  # noqa: BLE001 - provider 异常类型不一。
            logger.warning(
                "判断猜测失败。错误类型：%s，错误：%s", type(error).__name__, error
            )
            yield event.plain_result("裁判走神了，判不了这一次，稍后再试或检查 LLM 裁判配置。")
            return

        if not correct:
            yield event.plain_result(f"“{guess}”不对哦，继续猜~")
            return

        await self.games.stop(session.origin)
        winner_id = event.get_sender_id()
        winner_name = event.get_sender_name() or winner_id
        wins = await self.leaderboard.add_win(winner_id, winner_name)
        logger.info(
            "对局结束。会话=%s，猜对者=%s，谜底=%s",
            session.origin,
            winner_name,
            session.puzzle.name,
        )
        yield event.plain_result(
            f"🎉 恭喜 {winner_name} 猜对了！\n"
            f"谜底：{session.puzzle.reveal_text()}\n"
            f"你已累计猜对 {wins} 次"
        )

    async def _handle_question(self, event: AstrMessageEvent, session, question: str):
        """由 LLM 裁判回答一次提问。"""
        session.question_count += 1
        try:
            answer = await self._judge().answer_question(
                event.unified_msg_origin, session.puzzle, question
            )
        except Exception as error:  # noqa: BLE001 - provider 异常类型不一。
            logger.warning(
                "回答提问失败。错误类型：%s，错误：%s", type(error).__name__, error
            )
            return
        yield event.plain_result(answer)

    # ------------------------------------------------------------------ #
    # 函数工具：自然语言开局
    # ------------------------------------------------------------------ #
    async def _answer_as_judge(self, event: AstrMessageEvent, session) -> str:
        """把玩家这句话当成对谜底的提问，交给裁判回答。

        兜底用：正常情况下 on_message 会先把提问接走，走不到这里。只有当
        提问识别漏判、消息又落到 LLM 主链路并被误判成「开局」时才会用上。
        """
        question = str(event.message_str or "").strip()
        try:
            return await self._judge().answer_question(
                event.unified_msg_origin, session.puzzle, question
            )
        except Exception as error:  # noqa: BLE001 - provider 异常类型不一。
            logger.warning(
                "兜底回答提问失败。错误类型：%s，错误：%s", type(error).__name__, error
            )
            return "裁判走神了，稍后再问一次吧。"

    @filter.llm_tool(name="start_anime_guess")
    async def start_anime_guess_tool(self, event: AstrMessageEvent, mode: str = "随机"):
        """开启一局动漫猜谜游戏（Anime Guess）。当用户想玩猜动漫、猜番、猜作品或猜角色的游戏时调用。

        Args:
            mode(string): 谜底类型。猜作品填「作品」，猜角色填「角色」，用户没指定或说随便就填「随机」。
        """
        if not self._natural_language_enabled():
            return "自然语言开启游戏已被管理员关闭，请改用 /ag 指令。"

        # 对局进行中时，模型很容易把「是2020年之前的番还是之后的」这类对谜底的
        # 提问误判成开局请求。这时绝不能回「已经有一局在进行了」——那对一句提问
        # 来说是答非所问。看着像提问就交给裁判，否则只回一句工具结果让模型自己说。
        session = self.games.get(event.unified_msg_origin)
        if session is not None:
            text = str(event.message_str or "").strip()
            if looks_like_question(text) and not parse_guess(text):
                await event.send(
                    event.plain_result(await self._answer_as_judge(event, session))
                )
                event.stop_event()
                return None
            return "本群已经有一局动漫猜谜在进行中，不要重复开局。"

        normalized = str(mode or "").strip()
        if any(word in normalized for word in ("作品", "番", "动画", "work")):
            requested = "作品"
        elif any(word in normalized for word in ("角色", "人物", "character")):
            requested = "角色"
        else:
            requested = "随机"

        text = await self._begin_game(event, requested)
        await event.send(event.plain_result(text))
        event.stop_event()

    @filter.llm_tool(name="stop_anime_guess")
    async def stop_anime_guess_tool(self, event: AstrMessageEvent):
        """结束当前的动漫猜谜游戏（Anime Guess）并揭晓谜底。当用户想结束、放弃或退出这个游戏时调用。"""
        if not self._natural_language_enabled():
            return "自然语言控制游戏已被管理员关闭，请改用 /ag 结束。"
        await event.send(event.plain_result(await self._finish_game(event)))
        event.stop_event()
