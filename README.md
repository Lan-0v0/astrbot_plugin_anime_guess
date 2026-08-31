# Anime Guess 动漫猜谜

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.11.0-blue)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/version-v0.0.2-green)](https://github.com/Lan-0v0/astrbot_plugin_anime_guess/releases)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

群聊里的多人动漫猜谜游戏。bot 抽一个动漫作品或角色当谜底，玩家自由提问，
LLM 裁判只回答「是／否／不清楚」，群里任何人都能用「猜 xxx」抢答。

> 这不是「谁开局谁猜」的单人游戏：开局后本群所有人都能抢答，谁猜对算谁的战绩。

## 玩法示例

```
玩家A：/ag 角色
 bot ：游戏开始，请开始提问，我会回答“是/否/不清楚”直至谜底揭晓
玩家A：是女生吗
 bot ：是
玩家B：会魔法吗
 bot ：是
玩家C：猜 惠惠
 bot ：🎉 恭喜 玩家C 猜对了！
       谜底：《为美好的世界献上祝福》中的 惠惠
       你已累计猜对 1 次
```

## 指令

| 指令 | 说明 |
| --- | --- |
| `/ag` | 显示指令帮助 |
| `/ag 作品` | 以作品名为谜底开启游戏 |
| `/ag 角色` | 以角色名为谜底开启游戏 |
| `/ag 随机` | 以作品／角色名为谜底开启游戏 |
| `猜 <作品/角色名>` | 对谜底进行猜测，比如 `猜 蕾姆` |
| `/ag 结束` | 结束游戏并揭晓谜底 |
| `/ag 排行榜` | 查看 AGのKing～ |

指令别名：`/animeguess`、`/动漫猜谜`。

开启了「启用自然语言」后，也可以直接对 bot 说「来局动漫猜谜，猜作品」开局、
说「结束游戏」收摊，无需记指令。这依赖 AstrBot 的函数工具，需要所用聊天模型支持工具调用。

## 安装

在 AstrBot WebUI 的「插件市场」搜索安装，或手动克隆到插件目录：

```bash
cd AstrBot/data/plugins
git clone https://github.com/Lan-0v0/astrbot_plugin_anime_guess.git
```

安装后在 WebUI 重载插件即可。依赖只有 `aiohttp`，AstrBot 本体已自带。

## 配置

在 WebUI 的插件配置面板里设置：

| 配置项 | 说明 |
| --- | --- |
| 启用自然语言 | 允许用自然语言开局／结束，默认开启 |
| 动漫数据来源库 | `Bangumi`（默认）／`AniList`／`萌娘百科` |
| APIkey | 所选来源的 Access Token，两个来源都可留空 |
| 仅在被@时回答提问 | 活跃群里可开启以减少 LLM 调用，默认关闭 |
| LLM裁判 | 选一个已配置的模型提供商，留空则跟随当前聊天模型 |

「仅在被@时回答提问」有两个例外不受影响：`猜 xxx` 抢答始终无需 @（抢答讲究快），
私聊也始终无需 @（私聊没有 @ 的概念）。

## 数据来源库怎么选

| 来源 | 中文覆盖 | 是否需要 Key | 抽取耗时 | 说明 |
| --- | --- | --- | --- | --- |
| **Bangumi** | 最好，作品与角色都有简体中文名 | 不需要 | ~1–2 秒 | 默认，中文环境下体验最好 |
| AniList | 约一半条目有中文译名 | 不需要 | <1 秒 | 最快，但缺中文时谜底会显示日文原名或罗马音 |
| 萌娘百科 | 中文原生，萌属性标签丰富 | 无 API Key 概念 | ~2–8 秒 | 实时抓网页，最慢；站点限流时可能更久 |

三个来源都优先使用中文名，缺失时回退原名；LLM 裁判会做跨语言与别名判定，
所以玩家用中文、日文原名或罗马音猜都算命中。

关于萌娘百科的实现细节：它的 MediaWiki API 只放开了少数模块（`categorymembers`、
`search`、`allpages`、`parse` 全部返回 `action-notallowed`），所以分类枚举只能抓
HTML。另外分类页上的「下一页」链接写的是 `pagefrom=`，但服务端只认 `from=`，
照抄链接会拿到空列表——插件用的是 `from=` 加随机排序键，一次请求就能随机取样。

## 排行榜

猜对一次记一分，数据持久化在 `data/plugin_data/astrbot_plugin_anime_guess/leaderboard.json`，
插件更新或重装都不会丢。榜单固定显示前五名，空缺的名次显示「虚以待位」：

```
👑AGのKing👑：小岚 猜对12次
🥈第二名：路人甲 猜对8次
🥉第三名：虚以待位
第四名：虚以待位
第五名：虚以待位
```

## 开发

```bash
py -m pytest tests -q
py -m ruff check .
```

190 个单元测试覆盖谜底解析、三个数据源的响应解析、裁判答案收敛、排行榜持久化与
消息解析；数据源的实网抽取与逐功能验收另有脚本，不在仓库内。

## License

[MIT](LICENSE)
