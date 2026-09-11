# CliProxyAPI Bot

NoneBot2 + Alconna 插件，让管理员在聊天里操作 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 管理接口：探活、凭证巡检、OAuth 登录、额度/冷却查看与 `reset-quota`。

命令收发走 `UniMessage`，不绑定单一适配器。本仓库默认装了 OneBot V11 与 Telegram。

## 启动

```bash
uv sync
cp .env.example .env.prod   # Windows: Copy-Item .env.example .env.prod
# 编辑 .env.prod：SUPERUSERS、适配器、CPA_BASE_URL、CPA_MANAGEMENT_KEY

# 要用额度卡片图时必须先装浏览器，否则自动回退文字
uv run playwright install chromium

uv run nb run
```

未执行 `uv run playwright install chromium` 时，`cpa quota` 会回退纯文字，也可设 `CPA_QUOTA_IMAGE=false` 或加 `--text`。

## 配置

写在 NoneBot 的 `.env` / `.env.prod` 里。仓库已提供 `.env.example`，复制后改占位符即可。管理密钥、Bot Token、`data/` 不要提交到 git。

### 通用

```env
DRIVER=~fastapi+~httpx+~websockets+~aiohttp
SUPERUSERS=["12345678"]
COMMAND_START=["/"]
```

`SUPERUSERS` 填各平台的 user id（QQ 号 / Telegram 数字 id）。可同时写多个。

### OneBot V11

反向 WebSocket：NoneBot 主动连接协议端（NapCat / Lagrange / LLOneBot 等）。

```env
ONEBOT_WS_URLS=["ws://127.0.0.1:3001"]
ONEBOT_ACCESS_TOKEN=your_onebot_token
```

协议端需开启反向 WS，地址与 token 和这里一致。

### Telegram

可与 OneBot 同时启用。Token 从 [@BotFather](https://t.me/BotFather) 获取。

```env
telegram_bots=[{"token": "123456:ABC-DEF"}]
# TELEGRAM_PROXY=http://127.0.0.1:7890
```

走系统 / 本地代理访问 Telegram API 时取消注释 `TELEGRAM_PROXY`。

### CPA 插件

```env
CPA_BASE_URL=http://127.0.0.1:8317
CPA_MANAGEMENT_KEY=plaintext-management-password
# 可选：额外管理员，值为各平台的 user id
CPA_ADMINS=["87654321"]
# 可选：允许执行 cpa codex refresh。SUPERUSERS 不能代替该权限
# CODEX_REFRESH_ADMIN=["87654321"]
# 可选
# CPA_TIMEOUT=15
# CPA_OAUTH_POLL_INTERVAL=3
# CPA_OAUTH_TIMEOUT=1800
# CPA_QUOTA_TIMEOUT=25
# CPA_QUOTA_CONCURRENCY=4
# CPA_QUOTA_CACHE_TTL=60
# CPA_QUOTA_IMAGE=true
# CPA_QUOTA_IMAGE_WIDTH=520
# CPA_ALIAS_FILE=data/cpa_aliases.json
# CPA_ALIASES={"user@example.com":"AG-1"}
```

| 配置项 | 说明 |
| --- | --- |
| `CPA_BASE_URL` | CPA 地址。可写 `http://host:8317` 或带 `/v0/management` 的完整前缀 |
| `CPA_MANAGEMENT_KEY` | 管理密钥**明文**，对应 `Authorization: Bearer` / `X-Management-Key` |
| `CPA_ADMINS` | 除 `SUPERUSERS` 外允许使用 `cpa` 的用户 ID |
| `CODEX_REFRESH_ADMIN` | 允许执行 `cpa codex refresh` 的用户 ID。空名单则任何人（含 SUPERUSERS）都不能刷新 |
| `CPA_TIMEOUT` | HTTP 超时（秒） |
| `CPA_OAUTH_POLL_INTERVAL` | 登录状态轮询间隔（秒） |
| `CPA_OAUTH_TIMEOUT` | 登录等待上限（秒），默认 1800，与 CPA session TTL 接近 |
| `CPA_QUOTA_TIMEOUT` | 单次上游额度查询超时（秒） |
| `CPA_QUOTA_CONCURRENCY` | 同时查询的账号数，默认 4 |
| `CPA_QUOTA_CACHE_TTL` | 额度结果缓存秒数，默认 60；`cpa quota --fresh` 可绕过 |
| `CPA_QUOTA_IMAGE` | 是否把额度渲染成卡片图，默认 true |
| `CPA_QUOTA_IMAGE_WIDTH` | 出图宽度（px），默认 520 |
| `CPA_ALIAS_FILE` | 账号别名 JSON，默认 `data/cpa_aliases.json`（不要提交） |
| `CPA_ALIASES` | 可选的初始别名表，`{"邮箱或文件名":"显示名"}`；运行时 `cpa alias set` 会写进文件并覆盖 |

Bot 与 CPA 不在同一台机器时，CPA 需要 `remote-management.allow-remote: true`，或设置环境变量 `MANAGEMENT_PASSWORD`（会强制允许远程）。未配置任何管理密钥时，`/v0/management` 会 404。

## 命令

仅超级用户 / `CPA_ADMINS` 可用。`COMMAND_START=["/"]` 时发 `/cpa` 或 `cpa` 会输出完整命令帮助。

| 命令 | 作用 |
| --- | --- |
| `cpa status` | 探活：版本头、凭证 ready / 禁用 / 冷却计数。不回传配置正文 |
| `cpa auth list [provider] [--disabled]` | 凭证摘要。默认隐藏已禁用账号 |
| `cpa auth show <查询词>` | 单条详情与近期请求桶 |
| `cpa auth on\|off <查询词>` | 启用 / 禁用（`enable` / `disable` 同义） |
| `cpa auth models <查询词>` | 该凭证支持的模型 |
| `cpa auth delete <查询词> --yes` | 删除磁盘凭证；无 `--yes` 只预告 |
| `cpa alias list [--disabled]` | 列出账号显示别名。默认隐藏已禁用账号 |
| `cpa alias set <渠道> <邮箱> <别名>` | 为指定渠道账号设置别名；同邮箱跨渠道必须带渠道 |
| `cpa alias del <查询词>` | 删除别名 |
| `cpa quota` | 按平台分组查上游额度，每个平台发一张合并卡片图 |
| `cpa quota <平台>` | 只出该平台的合并图：`claude` / `codex` / `antigravity` / `kimi` / `xai` |
| `cpa quota <查询词>` | 单个账号的额度卡片 |
| `cpa quota --fresh` | 忽略缓存，强制重查 |
| `cpa quota --text` | 只发文字总览（排障 / 无浏览器时） |
| `cpa quota cooling` | 只看冷却（本地 CPA 状态，不打上游） |
| `cpa quota reset <查询词>` | `POST /reset-quota`（使用完整 `auth_index`） |
| `cpa codex refresh <查询词>` | 消耗 1 次 Codex 官方重置次数并刷新额度。仅 `CODEX_REFRESH_ADMIN` |
| `cpa login <渠道>` | 启动 OAuth / 设备码。授权完成后把浏览器回调链接发回聊天 |
| `cpa login callback <回调链接>` | 手动提交 localhost 回调 URL |
| `cpa login cancel` | 取消当前登录 |

查询词可以是 email、文件名、label、别名或 `auth_index`（含前缀）。列表和额度图优先显示别名；未设别名时用 `渠道-短索引`，避免把邮箱发到聊天。同邮箱出现在多个渠道时用 `cpa alias set antigravity user@example.com AG-1`。详情 `cpa auth show` 仍会列出原始字段，便于对照。

内置登录渠道：`claude` / `anthropic`、`codex`、`antigravity`、`kimi`、`xai`。若 CPA 插件声明了 `supports_oauth`，还会动态发现 `/{provider}-auth-url`。不要写死已从 core 移除的 `gemini-cli` / `qwen` / `iflow`。

## 额度说明

CLIProxyAPI **没有**账号池额度聚合接口。`GET /auth-files` 只有健康 / 冷却状态。`cpa quota` 和管理台 Quota 页同一思路：按 `provider` 分组后，用内部白名单 `POST /v0/management/api-call` 打各平台用量接口（`$TOKEN$` 由 CPA 替换）。聊天里**不会**开放通用代发。

默认跳过 `disabled` 凭证，与管理台「8 个文件 / 6 个参与额度」一致。

**合计不是百分比相加。** `86% + 91%` 不会写成 `177%`。每个窗口先换成剩余比例（0–1），再按账号求和：

```text
【Antigravity】4 账号
  合计：Gemini 5h 3.44/4 (86%) · Gemini 周 3.44/4 (86%) · Claude/GPT 周 1.64/4 (41%)
  account-a (Pro)  Gemini 5h 剩 86% →19m · Gemini 周 剩 86% →18h
```

`3.44/4 (86%)` 表示：该窗口剩余当量 3.44 个满额号，4 个账号均剩 86%。1.00 = 满额一个号。

默认用 Playwright 把同一平台的账号卡合并成一张图发送（视觉对齐管理台 Quota 页，不含 Refresh / 时间轴）。超过 8 个账号会拆成多张。出图函数 `render_platform_images` / `render_board_images` 不依赖聊天会话，以后做定时推送可以直接复用。

未安装 Chromium 时会自动回退文字，并提示执行 `playwright install chromium`（推荐：`uv run playwright install chromium`）。`CPA_QUOTA_IMAGE=false` 或 `cpa quota --text` 可强制只要文字。

支持的上游：Claude OAuth usage、Codex WHAM usage、Antigravity `retrieveUserQuotaSummary` + `loadCodeAssist`（套餐）、Kimi usages、xAI billing credits。Antigravity 的 `account_type=oauth` 只是登录方式，套餐来自 `paidTier`（Pro / Plus / Ultra）。未知 / API-key 渠道只显示本地健康状态。

不要用 `GET /usage-queue` 当「查用量」——它会把记录从队列里弹出，会和 WebUI / Redis `LPOP` 抢数据。全量刷新会打上游，群里连刷请用缓存或 `cpa quota antigravity` 只查一个平台。

## OAuth 注意

- 机器人**不会**加 `is_webui=true`。该参数会在 CPA 本机 `51121` 起 callback，聊天场景通常不可达。
- 授权链接 / 设备码优先私聊下发；私聊失败才回当前会话并警告。
- 浏览器常会跳到 `localhost`。把地址栏完整回调链接发回当前聊天，或 `cpa login callback <url>`。插件会 `POST /oauth-callback`（`redirect_url`）转给 CPA。
- Session 约 30 分钟；超时或 `cpa login cancel` 会 `DELETE /oauth-session`。

## 刻意不暴露的接口

聊天里不会做这些操作（避免把管理密钥能力扩成任意写配置 / 泄密）：

- 整份 `GET /config`、`config.yaml` 读写
- 上传 / 下载 auth JSON、Vertex import
- 通用 `/api-call` 代发（额度巡检只用白名单 URL，且不把上游 body 回传到聊天）
- `/usage-queue` 出队查询
- 插件商店安装（会下载可执行文件）

## 鉴权失败

同一 IP 连续 5 次管理密钥错误会被 CPA 封禁约 30 分钟。插件在 401 / 403 后会暂停请求一段时间，避免把 Bot 所在 IP 打进黑名单。
