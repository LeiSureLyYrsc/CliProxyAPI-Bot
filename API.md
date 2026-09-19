# CLIProxyAPI Management API

基路径：`/v0/management`  
默认地址：`http://localhost:8317`

本文对齐 CLIProxyAPI GitHub `main` 的路由注册（`internal/api/server_management.go`）与官方文档（[Management API](https://help.router-for.me/management/api)），供 Bot 插件对接使用。

---

## 鉴权

所有已注册管理接口（含 localhost）都要带管理密钥，二选一：

```http
Authorization: Bearer <plaintext-key>
X-Management-Key: <plaintext-key>
```

密钥来源：

| 来源 | 说明 |
| --- | --- |
| `remote-management.secret-key` | 配置文件中的管理密钥。明文会在启动时 bcrypt 哈希并写回配置。请求必须发送**明文**，不能发送哈希。 |
| `MANAGEMENT_PASSWORD` | 额外明文密钥，不落盘，并强制允许远程访问。 |
| TUI / SDK 本机密码 | 仅 `127.0.0.1` / `::1` 可用，只存在内存中。 |

要点：

| 项 | 行为 |
| --- | --- |
| 未配置任何密钥 | 整组 `/v0/management` 返回 **404** |
| `allow-remote: false` | 非本机返回 **403** `remote management disabled` |
| 连续 5 次鉴权失败 | 该 IP 封禁约 **30 分钟** |
| Home 模式开启 | 管理接口返回 **404** |
| 响应头 | `X-CPA-VERSION` / `X-CPA-COMMIT` / `X-CPA-BUILD-DATE` / `X-CPA-SUPPORT-PLUGIN`（`1` 表示当前二进制支持动态库插件） |
| 不能经 API 修改 | `remote-management.allow-remote`、`remote-management.secret-key` |

例外：`GET` / `POST /v0/management/oauth-callback` 不走管理密钥中间件，靠 pending `state` + provider 校验。

---

## 请求 / 响应约定

- Content-Type 默认 `application/json`（YAML 整文件替换除外）。
- 标量（bool / int / string）更新：`{ "value": ... }`，成功返回 `{ "status": "ok" }`。
- 数组 PUT：原始数组，或 `{ "items": [ ... ] }`。
- 数组 PATCH：`{ "old": "k1", "new": "k2" }` 或 `{ "index": 0, "value": "k2" }`。
- 对象数组 PATCH：按 `index` 或 `match`（通常匹配 `api-key` / `name`）。
- 对象数组 DELETE：`?index=` 或 `?api-key=`；同一 key 出现多次时再加 `&base-url=`。

---

## 错误码

| HTTP | 含义 |
| --- | --- |
| 400 | 非法 body，例如 `{ "error": "invalid body" }` |
| 401 | 缺密钥 / 密钥错误 |
| 403 | 禁止远程，或 IP 被 ban |
| 404 | 管理 API 未启用、Home 模式、或资源不存在 |
| 409 | 插件卸载需要重启（`restart_required: true`） |
| 422 | YAML / 配置校验失败 |
| 500 | 写配置失败 |
| 503 | core auth manager 不可用 |

---

## 内置接口

路径均相对 `/v0/management`。

### 配置

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/config` | 完整配置 JSON。尚未加载时返回 `{}`。 |
| GET | `/config.yaml` | 原始 YAML（保留注释与格式） |
| PUT | `/config.yaml` | 整文件替换（校验后热加载）。`Content-Type: application/yaml` |
| GET | `/latest-version` | 最新 GitHub release 版本号，不下载资源 |

```bash
curl -H 'Authorization: Bearer <MANAGEMENT_KEY>' \
  http://localhost:8317/v0/management/config
```

### 运行时开关

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET / PUT / PATCH | `/debug` | debug |
| GET / PUT / PATCH | `/logging-to-file` | 写文件日志 |
| GET / PUT / PATCH | `/logs-max-total-size-mb` | 日志总大小上限（MiB）。负值按 `0` 存储 |
| GET / PUT / PATCH | `/error-logs-max-files` | 请求错误日志保留数。负值回退为 `10` |
| GET / PUT / PATCH | `/usage-statistics-enabled` | 用量采集 |
| GET / PUT / PATCH | `/proxy-url` | 全局代理 URL |
| DELETE | `/proxy-url` | 清空代理 |
| GET / PUT / PATCH | `/request-log` | 请求日志开关 |
| GET / PUT / PATCH | `/ws-auth` | WebSocket 网关鉴权 |
| GET / PUT / PATCH | `/request-retry` | 重试次数 |
| GET / PUT / PATCH | `/max-retry-interval` | 最大重试间隔（秒） |
| GET / PUT / PATCH | `/force-model-prefix` | 强制模型前缀 |
| GET / PUT / PATCH | `/routing/strategy` | 凭证选择策略：`round-robin`（也接受 `roundrobin` / `rr`）或 `fill-first`（也接受 `fillfirst` / `ff`）。GET 返回 `{ "strategy": "..." }` |

```bash
curl -X PUT -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <MANAGEMENT_KEY>' \
  -d '{"value":true}' \
  http://localhost:8317/v0/management/debug
```

### 配额

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET / PUT / PATCH | `/quota-exceeded/switch-project` | 超额切项目 |
| GET / PUT / PATCH | `/quota-exceeded/switch-preview-model` | 超额切 preview 模型 |
| POST | `/reset-quota` | 清单个凭证的配额 / 冷却，并立即恢复路由 |

`POST /reset-quota` 只接受 `GET /auth-files` 返回的 `auth_index`，不要传文件名或 auth ID：

```json
{ "auth_index": "<AUTH_INDEX>" }
```

成功示例：

```json
{
  "status": "ok",
  "auth_index": "<AUTH_INDEX>",
  "models": ["gpt-5"]
}
```

### 代理入口 API Key

这些接口更新 `auth.providers` 里的 inline `config-api-key`。旧的顶层 `api-keys` 会自动同步。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api-keys` | 列表 |
| PUT | `/api-keys` | 整表替换 |
| PATCH | `/api-keys` | 改一项（`old/new` 或 `index/value`） |
| DELETE | `/api-keys` | `?value=` 或 `?index=` |
| GET | `/api-key-usage` | 按 provider / key 的近期请求桶 |
| GET | `/usage-queue?count=10` | **弹出**最多 `count` 条用量记录 |

`GET /usage-queue` 说明：

- `count` 可选，默认 `1`，必须是正整数。
- 响应始终是数组；空队列返回 `[]`。
- 返回的记录会从队列中移除。
- 同端口还有 Redis 兼容 Usage Queue；`LPOP` / `RPOP` 同样出队。

旧的聚合用量接口 `/usage`、`/usage/export`、`/usage/import` **已移除**。用 `/usage-queue` 和 `/usage-statistics-enabled`。

用量记录示例：

```json
[
  {
    "timestamp": "2026-05-05T12:00:00Z",
    "latency_ms": 1234,
    "source": "user@example.com",
    "auth_index": "0",
    "tokens": {
      "input_tokens": 10,
      "output_tokens": 20,
      "reasoning_tokens": 0,
      "cached_tokens": 0,
      "total_tokens": 30
    },
    "failed": false,
    "provider": "openai",
    "model": "gpt-5.4",
    "alias": "gpt-5.4",
    "endpoint": "POST /v1/chat/completions",
    "auth_type": "api_key",
    "api_key": "sk-...",
    "request_id": "req_..."
  }
]
```

### 上游 Provider Key 集合

形状都是 GET / PUT / PATCH / DELETE。PATCH 用 `index` 或 `match`；DELETE 用 `?index=` 或 `?api-key=`（重复 key 再加 `&base-url=`）。GET 返回以端点名为键的对象，并在适用时附带运行时 `auth-index`。

| 路径 | 说明 |
| --- | --- |
| `/gemini-api-key` | Gemini。字段：`api-key`、`priority`、`prefix`、`base-url`、`proxy-url`、`models`、`headers`、`excluded-models`、`disable-cooling` |
| `/interactions-api-key` | Google Interactions，形状同 Gemini |
| `/claude-api-key` | Claude。可含 `models`（`name` / `alias`） |
| `/codex-api-key` | Codex |
| `/xai-api-key` | 原生 xAI。Codex 形状外加 `priority`、`websockets`、`disable-cooling`；保留项必须有 `base-url` |
| `/vertex-api-key` | Vertex 兼容。PUT 项必须有 `api-key`；模型项可用 `name`、`alias`、`display-name`、`force-mapping` |
| `/openai-compatibility` | OpenAI 兼容上游。PATCH / DELETE 按 `name` 或 `index` |

```bash
curl -H 'Authorization: Bearer <MANAGEMENT_KEY>' \
  http://localhost:8317/v0/management/claude-api-key

curl -X PATCH -H 'Authorization: Bearer <MANAGEMENT_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{"index":0,"value":{"proxy-url":"socks5://127.0.0.1:1080"}}' \
  http://localhost:8317/v0/management/gemini-api-key
```

### 日志

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/logs?limit=&cursor=` | 增量读日志 |
| DELETE | `/logs` | 删除轮转文件并截断当前日志 |
| GET | `/request-error-logs` | 错误请求日志文件列表 |
| GET | `/request-error-logs/:name` | 下载单个错误日志 |
| GET | `/request-log-by-id/:id` | 按 request ID 下载（ID 不能含路径分隔符） |

`GET /logs`：

- 带 `limit`、不带 `cursor`：返回最新 N 行。
- 把返回的 `next-cursor` 作为下次 `cursor` 做增量读取。
- 游标失效时返回 `cursor-reset: true`。

```json
{
  "lines": ["2026-05-05 12:00:00 info request accepted"],
  "line-count": 1,
  "latest-timestamp": 1777982400,
  "next-cursor": "<OPAQUE_CURSOR>"
}
```

### OAuth 模型映射

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET / PUT / PATCH / DELETE | `/oauth-excluded-models` | 按 provider 排除模型 |
| GET / PUT / PATCH / DELETE | `/oauth-model-alias` | 按 channel 配别名。PATCH：`{ "channel": "codex", "aliases": [...] }`（`provider` 可作为 `channel` 别名）。DELETE：`?channel=codex` |
| GET / PUT / PATCH / DELETE | `/oauth-request-scoped-errors` | 请求级错误作用域（源码已注册） |

`GET /oauth-model-alias` 示例：

```json
{
  "oauth-model-alias": {
    "codex": [
      {
        "name": "gpt-5",
        "alias": "gpt-5-fast",
        "fork": true,
        "display-name": "GPT-5 Fast",
        "force-mapping": true
      }
    ]
  }
}
```

### 凭证文件

管理 `auth-dir` 下的 JSON token 文件。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/auth-files?name=&auth_index=` | 列表（支持 `name` 或 `auth_index` 过滤；含 `auth_index`、status、用量桶及安全 claims） |
| GET | `/auth-files/models?name=` | 某凭证支持的模型 |
| GET | `/model-definitions/:channel` | 静态模型目录。未知 channel 返回 400 |
| GET | `/auth-files/download?name=` | 下载单个 JSON |
| POST | `/auth-files` | 上传（multipart 或 raw JSON） |
| DELETE | `/auth-files?name=` | 删除单个文件 |
| DELETE | `/auth-files?all=true` | 清空 `auth-dir` 下所有 `.json` |
| PATCH | `/auth-files/status` | `{ "name": "<file>", "disabled": true }` |
| PATCH | `/auth-files/fields` | 改元数据。支持点路径，例如 `headers.X-Team` |
| POST | `/vertex/import` | 导入 Vertex service account |

列表响应示例（Claude / Codex）：

```json
{
  "files": [
    {
      "id": "user@example.com",
      "auth_index": "a1b2c3d4e5f67890",
      "name": "user@example.com.json",
      "provider": "claude",
      "label": "Claude Prod",
      "status": "ready",
      "status_message": "ok",
      "disabled": false,
      "unavailable": false,
      "runtime_only": false,
      "source": "file",
      "path": "/abs/path/auths/user@example.com.json",
      "size": 2345,
      "modtime": "2025-08-30T12:34:56Z",
      "success": 12,
      "failed": 1,
      "email": "user@example.com"
    },
    {
      "id": "codex-user@example.com",
      "auth_index": "b2c3d4e5f6a17890",
      "name": "codex-user@example.com.json",
      "provider": "codex",
      "status": "ready",
      "id_token": {
        "chatgpt_subscription_active_until": 1788800000,
        "plan_type": "pro"
      }
    }
  ]
}
```

> **额度与订阅到期字段说明**：
> - **Codex 套餐到期**：`chatgpt_subscription_active_until`（或 `subscription_active_until`）表示 ChatGPT 订阅的当前有效截止/续期节点；它不同于 OAuth Access Token 的过期时间 `expires_at`，也不同于 WHAM 5h/周额度刷新时间 `reset_at`。CPA 在 `/auth-files` 列表的条目中可能直接暴露 `id_token` 安全 claims，Bot 直接读取此类字段即可提取套餐到期和静态计划，无需下载原始凭证或自行解析原始 JWT。
> - **xAI / Grok 配额**：周总额度来自 `creditUsagePercent`（对应 ID `billing`），刷新倒计时由 `billingPeriodEnd` 提供并仅属于周总额度；`products`/`usages` 中的子项（如 `GrokBuild`、`GrokChat`、`GrokImagine` 以及未来动态项）为细分产品用量明细，属于其他额度且无独立 reset 刷新节点。

配置型 API-key 记录通过各自的 `excluded-models` 禁用；插件虚拟子项不能独立改状态。

### 内置登录（OAuth）

当前 **main 内置**只有下列登录入口。Gemini CLI / Qwen / iFlow 等已从核心路由移除，改由插件动态挂 `*-auth-url`。

| 方法 | 路径 | 流程 |
| --- | --- | --- |
| GET | `/anthropic-auth-url` | Claude |
| GET | `/codex-auth-url` | Codex |
| GET | `/antigravity-auth-url` | Antigravity |
| GET | `/kimi-auth-url` | Kimi 设备码（`flow: "device"`） |
| GET | `/xai-auth-url` | xAI 设备码 |
| GET | `/get-auth-status?state=` | 轮询：`wait` / `ok` / `error` |
| DELETE | `/oauth-session?state=` | 取消 pending，不落凭证 |
| GET / POST | `/oauth-callback` | 回调（无管理密钥） |

启动登录时加 `?is_webui=true` 会在 `51121` 起临时 callback 转发。Session TTL 约 30 分钟。完成后状态会短暂保留，便于客户端观察到 `ok`。

设备码响应示例：

```json
{
  "status": "ok",
  "url": "https://...",
  "state": "xai-...",
  "flow": "device",
  "user_code": "ABCD-EFGH",
  "expires_in": 1800
}
```

轮询：

```json
{ "status": "wait" }
```

```json
{ "status": "ok" }
```

```json
{ "status": "error", "error": "Authentication failed" }
```

回调（不带管理密钥）：

```bash
curl 'http://localhost:8317/v0/management/oauth-callback?provider=codex&state=codex-...&code=AUTHORIZATION_CODE'

curl -X POST -H 'Content-Type: application/json' \
  -d '{"provider":"codex","state":"codex-...","code":"AUTHORIZATION_CODE"}' \
  http://localhost:8317/v0/management/oauth-callback
```

POST 也可用 `redirect_url` 携带 callback query。`provider` 必须与 pending session 匹配。

### 代发上游请求

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api-call` | 用 `auth_index`（也接受 `authIndex` / `AuthIndex`）选凭证发任意 HTTP |

请求字段：`method`、绝对 `url`、可选字符串 map `header`、可选原始字符串 `data`。Header 值可用 `$TOKEN$` 替换所选凭证的 access token / API key。凭证级代理优先于全局 `proxy-url`。

```json
{
  "status_code": 200,
  "header": { "Content-Type": ["application/json"] },
  "body": "{\"ok\":true}"
}
```

权限等同管理密钥，Bot 侧应严格限制谁能调用。

### 插件生命周期

这是宿主管理插件的接口，不是插件自有路由。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/plugins` | 已发现 / 已配置 / 已注册列表 |
| GET | `/plugins/:id/config` | 该插件保存的配置（无则 `{}`） |
| PUT | `/plugins/:id/config` | 整对象替换 |
| PATCH | `/plugins/:id/config` | 浅合并；字段为 `null` 时删除该字段 |
| PATCH | `/plugins/:id/enabled` | `{ "enabled": true }`。只改该插件开关，不改全局 `plugins.enabled` |
| DELETE | `/plugins/:id` | 删除本地插件文件和已保存配置。无法卸载时返回 409 + `restart_required: true` |
| GET | `/plugin-store` | 商店列表、来源错误、安装状态、是否有更新 |
| POST | `/plugin-store/:id/install` | 从商店安装或更新。多来源同 ID 时用 `?source=`；版本可用 query 或 `{ "version": "1.2.3" }` |

`GET /plugins` 示例：

```json
{
  "plugins_enabled": true,
  "plugins_dir": "/abs/path/plugins",
  "plugins": [
    {
      "id": "example-plugin",
      "path": "/abs/path/plugins/example-plugin.so",
      "configured": true,
      "registered": true,
      "enabled": true,
      "effective_enabled": true,
      "supports_oauth": false,
      "oauth_provider": "",
      "logo": "",
      "config_fields": [],
      "menus": [],
      "metadata": {
        "name": "Example",
        "version": "1.0.0",
        "author": "Example"
      }
    }
  ]
}
```

商店安装会下载可执行插件产物。使用前应配置并信任 store sources。插件 ID 必须符合宿主规则：

```text
[A-Za-z0-9][A-Za-z0-9._-]{0,127}
```

---

## Bot 对接建议

### 外部 Bot 调用 Management API

优先封装：

1. **探活**：`GET /config`（同时看 `X-CPA-SUPPORT-PLUGIN`）。
2. **账号**：`GET /auth-files`，后续操作一律用 `auth_index`。
3. **登录**：`GET /*-auth-url` → 把 `url` / `user_code` 发给用户 → 轮询 `GET /get-auth-status?state=`。
4. **用量**：打开 `usage-statistics-enabled`，周期调用 `GET /usage-queue`（会出队，适合 Bot 消费后推送）。
5. **超额恢复**：`POST /reset-quota`。
6. **开关凭证**：`PATCH /auth-files/status`。
7. **入口密钥**：`/api-keys`。

不要依赖已删除的 `/usage`、`/usage/export`、`/usage/import`。也不要把 `gemini-cli-auth-url`、`qwen-auth-url`、`iflow-auth-url`、`ampcode/*` 写死进 Bot；当前 main 已不注册它们。

### CLIProxyAPI 原生插件再挂自己的管理接口

能力声明：

```json
{ "capabilities": { "management_api": true } }
```

| 类型 | 注册字段 | 最终路径 | 鉴权 |
| --- | --- | --- | --- |
| 插件自有管理 API | `Routes` | `/v0/management/...` | 需要管理密钥 |
| 浏览器资源页 | `Resources` | `/v0/resource/plugins/<plugin-id>/...` | GET **不**走管理鉴权 |

ABI：`management.register` 注册路由，`management.handle` 处理请求。插件路由**不能覆盖**宿主已有 `/v0/management` 路径。未命中内置路由时，宿主会走 `ServePluginAuthURL` / `ServeManagementHTTP`，因此插件可以挂自己的 `*-auth-url`。

敏感操作不要绑在未鉴权的 resource GET 上。页面可从同源 Management Center 的 `localStorage` 读密钥，再调自己的 `/v0/management/...`。需要凭证 / 模型时用 host callback（`host.auth.*`、`host.http.do`、`host.model.*`），不要把密钥渲到 HTML。

参考：

- [Management API Capability](https://help.router-for.me/plugin/management-api)
- [Plugin Development](https://help.router-for.me/plugin/development)
- 示例：`examples/plugin/management-api/go/main.go`

---

## 配置片段

```yaml
remote-management:
  allow-remote: false
  secret-key: "your-strong-management-password"
  disable-control-panel: false

plugins:
  enabled: true
  dir: "plugins"
  configs: {}
```

环境变量覆盖：

```bash
export MANAGEMENT_PASSWORD="your-runtime-secret"
```
