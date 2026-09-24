# QuotaNoa Bot

NoneBot2 + Alconna 插件，让管理员在聊天里操作 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 管理接口：探活、凭证巡检、OAuth 登录、额度/冷却查看与 `reset-quota`。

命令收发走 `UniMessage`，不绑定单一适配器。本仓库默认装了 OneBot V11 与 Telegram。

## 启动

```bash
uv sync
cp .env.example .env.prod   # Windows: Copy-Item .env.example .env.prod
# 编辑 .env.prod：SUPERUSERS、适配器；插件业务配置在 data/quotabot_config.json

# 要用额度卡片图时必须先装浏览器，否则自动回退文字
uv run playwright install chromium

uv run nb run
```

未执行 `uv run playwright install chromium` 时，`/quota` 会回退纯文字，也可设 `cpa.quota_image=false` 或加 `--text`。

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

### QuotaBot 插件

业务配置（CPA 实例、火山凭据、渲染主题、别名文件路径）全部放在 `data/quotabot_config.json`，首次启动自动生成，支持热重载。`.env` 里只有这一项可选覆盖：

```env
# QUOTABOT_CONFIG_FILE=data/quotabot_config.json
```

`quotabot_config.json` 结构：

```jsonc
{
  "cpa": {
    "admins": [],                    // 额外管理员 user id（全局）
    "codex_refresh_admin": [],       // 全局
    "instances": [                   // 每个实例独立连接与额度设置
      {
        "name": "Home",
        "base_url": "http://127.0.0.1:8317",
        "management_key": "",        // 明文管理密钥
        "timeout": 15.0,
        "oauth_poll_interval": 3.0,
        "oauth_timeout": 1800.0,
        "quota_timeout": 25.0,
        "quota_concurrency": 4,
        "quota_cache_ttl": 60.0,
        "quota_image": true
      }
    ]
  },
  "volcengine": {
    "accounts": [
      { "name": "火山主号", "access_key_id": "AKLT…", "secret_access_key": "…", "region": "cn-beijing" }
    ]
  },
  "render": { "theme": "default", "cards_per_row": 4 },
  "aliases_file": "data/quota_aliases.json"
}
```

| 段 | 说明 |
| --- | --- |
| `cpa.instances[]` | 每个 CLIProxyAPI 实例一项，自带 `base_url` / `management_key` / 超时 / 并发 / 缓存 / 图片开关。`base_url` 可写 `http://host:8317` 或带 `/v0/management` 的完整前缀 |
| `cpa.admins` / `cpa.codex_refresh_admin` | 全局权限名单（与实例无关） |
| `volcengine.accounts` | 火山方舟 Coding Plan 查询凭据（控制面 AccessKey，需 `ArkReadOnlyAccess`） |
| `render` | 额度图主题与每行卡片数（1..6），`/quota theme` `/quota card row` 可改 |
| `aliases_file` | 分渠道别名文件，默认 `data/quota_aliases.json` |

修改配置后**自动热重载**（也可 `/quota config reload` 强制）；`/quota config show` 查看当前生效值。旧的 `CPA_*` 环境变量与 `data/cpa_aliases.json` / `data/cpa_render_settings.json` 不再生效（启动时会告警，不做自动迁移）。旧的单实例 `cpa.base_url` 字段不再读取，请改为 `cpa.instances[]`。

Bot 与 CPA 不在同一台机器时，CPA 需要 `remote-management.allow-remote: true`，或设置环境变量 `MANAGEMENT_PASSWORD`（会强制允许远程）。未配置任何管理密钥时，`/v0/management` 会 404。

## 命令

仅超级用户 / `cpa.admins` 可用。额度查询用 `/quota`（必须带指令头 `/`）；CPA 管理用 `/cpa`（前缀可有可无）。

### 额度查询 `/quota`

| 命令 | 作用 |
| --- | --- |
| `/quota` | **全部 CPA 实例**全平台额度汇总；无参数时显示帮助。多实例时按 `[实例名]` 前缀区分 |
| `/quota <平台>` | 只看一个平台：`claude` / `codex`(gpt, openai) / `antigravity`(反重力) / `kimi` / `xai` |
| `/quota 火山` | 查询火山方舟 Coding Plan 额度（同义：`volc` / `volcengine` / `ark` / `火山方舟`） |
| `/quota <实例>` | 只查指定 CPA 实例。例：`/quota Home` |
| `/quota <平台> <实例>` | 例：`/quota antigravity Home` 或 `/quota Home antigravity` |
| `/quota <查询词>` | 单个账号的额度卡（跨全部实例搜索） |
| `/quota --fresh` | 忽略缓存，强制重查 |
| `/quota --text` | 只发文字总览（排障 / 无浏览器时） |
| `/quota --instance <实例>` | 显式指定实例，避免与平台名冲突 |
| `/quota cooling` | 只看冷却中的凭证（全部实例） |
| `/quota reset <查询词>` | `POST /reset-quota`（使用完整 `auth_index`，跨实例搜索） |
| `/quota volc list` | 列出火山方舟账号 |
| `/quota volc add <名称> <AK> <SK> [region]` | 新增火山方舟账号（写入配置） |
| `/quota volc remove <名称> --yes` | 删除火山方舟账号 |
| `/quota alias list [--disabled]` | 列出账号显示别名（分渠道） |
| `/quota alias set <渠道> <查询词> <别名>` | 为指定渠道账号设置别名 |
| `/quota alias del <查询词>` | 删除别名（跨渠道全部删除） |
| `/quota theme [set <主题>]` | 查看 / 设置额度图主题（`default` / `mac` / `md3` / `winxp` / `win7`） |
| `/quota card [row <1..6>]` | 查看 / 设置每行卡片数量 |
| `/quota config show` | 查看当前生效配置（密钥脱敏）与最近解析错误 |
| `/quota config reload` | 强制从磁盘重载配置 |

### CPA 管理 `/cpa`

除登录回调外，所有子命令都要在第一个位置写 CPA 实例名。

| 命令 | 作用 |
| --- | --- |
| `cpa instance list` | 列出已配置实例 |
| `cpa instance add <名称> <base_url> [--key K] [--timeout N] [--quota-timeout N] [--concurrency N] [--cache-ttl N] [--no-image]` | 新增实例（写入配置） |
| `cpa instance show <名称>` | 查看实例详情（密钥脱敏） |
| `cpa instance remove <名称> --yes` | 删除实例 |
| `cpa status <实例>` | 探活：版本头、凭证 ready / 禁用 / 冷却计数。不回传配置正文 |
| `cpa auth list <实例> [provider] [--disabled]` | 凭证摘要。默认隐藏已禁用账号 |
| `cpa auth show <实例> <查询词>` | 单条详情与近期请求桶 |
| `cpa auth on\|off <实例> <查询词>` | 启用 / 禁用（`enable` / `disable` 同义） |
| `cpa auth models <实例> <查询词>` | 该凭证支持的模型 |
| `cpa auth delete <实例> <查询词> --yes` | 删除磁盘凭证；无 `--yes` 只预告 |
| `cpa codex refresh <实例> <查询词>` | 消耗 1 次 Codex 官方重置次数并刷新额度。仅 `cpa.codex_refresh_admin` |
| `cpa login <实例> <渠道>` | 启动 OAuth / 设备码。授权完成后把浏览器回调链接发回聊天（自动归属该实例） |
| `cpa login callback <回调链接>` | 手动提交 localhost 回调 URL |
| `cpa login cancel` | 取消当前登录 |
| `cpa quota [平台] [实例] [--instance <实例>] [--fresh] [--text]` | 与 `/quota` 同义：默认查询**全部实例**。例：`cpa quota xai JP-AI` 只查 JP-AI 的 xAI 额度 |

查询词可以是 email、文件名、label、别名或 `auth_index`（含前缀）。列表和额度图优先显示别名；未设别名时用 `渠道-短索引`，避免把邮箱发到聊天。同邮箱出现在多个渠道时用 `/quota alias set antigravity user@example.com AG-1`。详情 `cpa auth show` 仍会列出原始字段，便于对照。

内置登录渠道：`claude` / `anthropic`、`codex`、`antigravity`、`kimi`、`xai`。若 CPA 插件声明了 `supports_oauth`，还会动态发现 `/{provider}-auth-url`。不要写死已从 core 移除的 `gemini-cli` / `qwen` / `iflow`。

## 额度说明

CLIProxyAPI **没有**账号池额度聚合接口。`GET /auth-files` 只有健康 / 冷却状态。`/quota` 的 CPA 部分和管理台 Quota 页同一思路：按 `provider` 分组后，用内部白名单 `POST /v0/management/api-call` 打各平台用量接口（`$TOKEN$` 由 CPA 替换）。聊天里**不会**开放通用代发。火山方舟部分则直接用控制面 OpenAPI（SigV4 签名）查询 `GetCodingPlanUsage`。

默认跳过 `disabled` 凭证，与管理台「8 个文件 / 6 个参与额度」一致。

**合计不是百分比相加。** `86% + 91%` 不会写成 `177%`。每个窗口先换成剩余比例（0–1），再按账号求和：

```text
【Antigravity】4 账号
  合计：Gemini 5h 3.44/4 (86%) · Gemini 周 3.44/4 (86%) · Claude/GPT 周 1.64/4 (41%)
  account-a (Pro)  Gemini 5h 剩 86% →19m · Gemini 周 剩 86% →18h
```

`3.44/4 (86%)` 表示：该窗口剩余当量 3.44 个满额号，4 个账号均剩 86%。1.00 = 满额一个号。

默认用 Playwright 把同一平台的账号卡合并成一张图发送（视觉对齐管理台 Quota 页，不含 Refresh / 时间轴）。超过 8 个账号会拆成多张。出图函数 `render_platform_images` / `render_board_images` 不依赖聊天会话，以后做定时推送可以直接复用。

多实例下，账号卡标题与文字总览都会带 **`[CPA 实例名]` 前缀**（如 `[JP-AI] Murasame…`），便于区分额度来自哪个实例；实例标签限长 8 字符、账号名限长 16 字符，超长以 `…` 截断，完整名称保留在悬浮提示里。火山方舟账号为本地渠道（无 CPA 实例），不加前缀，其卡片会额外显示 **套餐档位徽章**（`Lite` / `Pro`，来自 `GetPersonalPlan`）。

## 主题资源包

所有额度图主题均存放在独立资源目录中，渲染器会自动扫描：

```text
plugins/QuotaBot/render/assets/
├─ quota.html
├─ base.css
├─ brands/
└─ themes/
   ├─ default/
   ├─ mac/
   ├─ md3/
   ├─ winxp/
   └─ win7/
```

每个主题目录包含：

```text
themes/<主题名>/
├─ theme.json
├─ theme.css
├─ wrapper.html
└─ SOURCES.md
```

- `theme.json`：主题名称、显示名称、根 CSS class、可选别名和说明。
- `theme.css`：该主题独有的视觉样式；公共网格、卡片和额度组件样式位于 `base.css`。
- `wrapper.html`：主题窗口外壳，只能使用 `__TITLE__`、`__GRID__`、`__PAGE_NOTE__` 三个占位符，其中网格和分页占位符必须存在。
- `SOURCES.md`：素材来源、许可证与商标声明。

新增主题时只需复制一个现有目录、修改目录名及上述四个文件，然后重启 Bot。主题目录名必须与 `theme.json` 中的 `name` 相同，并使用小写字母、数字、下划线或连字符。无需修改 Python 注册表或命令代码。运行时 CSS 和 wrapper 禁止脚本、事件处理器、`@import` 和远程 HTTP(S) 资源。

默认主题的 canonical 名称为 `default`。旧配置中的 `"theme": "shadcn"` 会自动兼容并解析为 `default`。主题和卡片布局保存在 `data/quotabot_config.json` 的 `render` 段（`/quota theme`、`/quota card row` 修改），不使用主题相关环境变量。

未安装 Chromium 时会自动回退文字，并提示执行 `playwright install chromium`（推荐：`uv run playwright install chromium`）。`cpa.quota_image=false` 或 `/quota --text` 可强制只要文字。

支持的上游：Claude OAuth usage、Codex WHAM usage、Antigravity `retrieveUserQuotaSummary` + `loadCodeAssist`（套餐）、Kimi usages、xAI billing credits、**火山方舟 Coding Plan**（控制面 `GetCodingPlanUsage`）。Antigravity 的 `account_type=oauth` 只是登录方式，套餐来自 `paidTier`（Pro / Plus / Ultra）。未知 / API-key 渠道只显示本地健康状态。

火山方舟凭据请用**控制面 OpenAPI** 的 AccessKey ID + SecretAccessKey（`volcengine.accounts[]`，需子账户具备 `ArkReadOnlyAccess` 权限），**不是**推理 Key（`ark-...` 查不了额度）。Bot 直接对 `open.volcengineapi.com` 做 SigV4 签名请求。

不要用 `GET /usage-queue` 当「查用量」——它会把记录从队列里弹出，会和 WebUI / Redis `LPOP` 抢数据。全量刷新会打上游，群里连刷请用缓存或 `/quota antigravity` 只查一个平台。

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

管理密钥错误时 CPA 会返回 401，插件直接把错误转达给管理员，**不做本地暂停/冷却**：修好 `management_key` 后下一条命令即可生效。若 CPA 与 Bot 不在同一台机器，403 通常表示需要在 CPA 侧开启 `remote-management.allow-remote`（或设置 `MANAGEMENT_PASSWORD`）。
