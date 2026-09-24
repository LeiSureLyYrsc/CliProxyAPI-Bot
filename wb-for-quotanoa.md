# wb-for-quotanoa — 额度查询机器人对接规范

> 面向「额度查询机器人」的接口对接文档。
> 目标：**批量检索所有账号各自的剩余 / 总量积分与套餐数**，并可直接用于告警、报表、看板。

本文档描述的是 **WorkBuddy2API 统一版**（Python/FastAPI，单端口）已实现的接口。
所有端点与返回体均与代码实现严格一致（`src/wb2api/routers/openai_api.py`、`src/wb2api/ops.py`）。

---

## 1. 基本信息

| 项 | 值 |
|---|---|
| 服务基址 | `http://<host>:7863`（默认端口 `7863`，单端口同时提供 API 与控制台） |
| 传输 | HTTP / JSON（UTF-8）；聊天为 SSE |
| 时间 | 服务器本地时区；`resetAt` 为上游原样字符串 |
| 额度单位 | 积分（Credits，整数） |

### 鉴权

| 端点类别 | 鉴权方式 |
|---|---|
| `/v1/*`、`/status` | `Authorization: Bearer <api_key>` |
| `/healthz` | **无鉴权** |
| `/api/*`（控制台） | 会话 Cookie 或 `Authorization: Bearer <session-token>`（见 §7） |

> `api_key` 为空时，`/v1/*` 与 `/status` **直接放行**（不鉴权）。生产环境务必设置。

鉴权失败（401）响应体：

```json
{
  "error": {
    "message": "missing or invalid API key",
    "type": "api_error",
    "code": "invalid_api_key"
  }
}
```

---

## 2. 端点总览

| 方法 | 路径 | 用途 | 是否实时探测上游 |
|---|---|---|---|
| `GET` | `/v1/quota` | **【推荐】全账号批量套餐配额** | 是（30s 缓存） |
| `GET` | `/v1/a/{uid}/quota` | 单账号套餐配额（复用全量缓存） | 否（读缓存） |
| `GET` | `/v1/accounts` | 账号清单（含冷却状态，无配额明细） | 否 |
| `GET` | `/status` | 账号池运行态快照（无配额明细） | 否 |
| `GET` | `/healthz` | 健康检查（无鉴权） | 否 |
| `POST` | `/api/tasks/credits` | 控制台异步批量积分任务（聚合口径，无套餐明细） | 是 |
| `GET` | `/api/accounts/{uid}/credits` | 控制台单账号积分（聚合口径） | 是 |

> **机器人首选 `/v1/quota`**：一次请求即返回**所有账号 + 每个账号的套餐明细**，无需逐账号调用。

---

## 3. `GET /v1/quota` — 批量额度查询（核心）

### 3.1 请求

```http
GET /v1/quota HTTP/1.1
Host: <host>:7863
Authorization: Bearer <api_key>
```

无查询参数、无请求体。

### 3.2 响应体

```json
{
  "provider": "workbuddy",
  "accounts": [
    {
      "uid": "232b76a9-2f49-42f7-97f1-deec3df5cbb5",
      "nickname": "星际猫",
      "credits": 2013,
      "cooling": false,
      "disabled": false,
      "quotas": {
        "个人标准版": {
          "packageName": "个人标准版",
          "total": 2000,
          "used": 800,
          "remaining": 1200,
          "resetAt": "2026-10-01 00:00:00",
          "recurring": true
        },
        "赠送积分": {
          "packageName": "赠送积分",
          "total": 300,
          "used": 100,
          "remaining": 200,
          "resetAt": "",
          "recurring": false
        }
      }
    },
    {
      "uid": "9f1c0b7e-1111-2222-3333-444455556666",
      "nickname": "备用号",
      "credits": 0,
      "cooling": true,
      "cool_kind": "soft_rate",
      "cool_until": "2026-09-24T14:22:10Z",
      "cool_remaining": "2h 13m 05s",
      "reason": "429 rate limit",
      "disabled": false,
      "quotas": null,
      "error": "cooling: quota probe skipped to avoid extending rate limit"
    }
  ]
}
```

### 3.3 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `provider` | string | 恒为 `"workbuddy"` |
| `accounts` | array | 账号配额行数组（顺序为池内 uid 升序） |

### 3.4 `accounts[]` 字段

| 字段 | 类型 | 必有 | 说明 |
|---|---|---|---|
| `uid` | string | ✅ | 账号唯一 ID（亦为凭证文件名 `workbuddy-<uid>.json` 中的 `<uid>`） |
| `nickname` | string | ❌ | 昵称；仅非空时出现 |
| `credits` | int | ✅ | **池内缓存**的积分（非实时，可能为 0/滞后）；实时数据看 `quotas` |
| `cooling` | bool | ✅ | 是否处于冷却/熔断期 |
| `disabled` | bool | ✅ | 是否被永久禁用（session 失效，需重新登录） |
| `quotas` | object \| null | ✅ | 套餐明细；`null` = 本次未探测（见 §3.6） |
| `cool_kind` | string | ❌ | `hard_credit`（余额不足）/ `soft_rate`（限流） |
| `cool_until` | string(ISO8601) | ❌ | 冷却截止时刻（UTC，带 `Z`） |
| `cool_remaining` | string | ❌ | 冷却剩余可读串，如 `"2h 13m 05s"` |
| `reason` | string | ❌ | 冷却/禁用原因 |
| `success_count` | int | ❌ | 累计成功请求数（>0 时出现） |
| `err_total` | int | ❌ | 累计错误数（>0 时出现） |
| `error` | string | ❌ | 本行探测失败原因（见 §3.6） |

### 3.5 `quotas` 对象（套餐明细）

`quotas` 是 **`套餐名 → 套餐对象`** 的映射。同一账号若出现**同名套餐**，
第 2、3… 个的**键名**会追加序号（`"个人标准版 2"`），但 `packageName` 字段始终保留原始名。

| 字段 | 类型 | 说明 |
|---|---|---|
| `packageName` | string | 套餐名（原始名，未加序号；上游缺名时为 `"Credit Package"`） |
| `total` | int | 该套餐总量（积分） |
| `used` | int | 该套餐已用（积分） |
| `remaining` | int | 该套餐剩余（积分，已钳制 ≥ 0） |
| `resetAt` | string | 周期重置时间（上游 `CycleEndTime` 原样；可能为 `""`） |
| `recurring` | bool | 是否周期套餐（`true` = 按周期重置；`false` = 一次性/赠送包） |

### 3.6 何时 `quotas` 为 `null` / 何时带 `error`

为保护上游配额与避免放大限流，以下情况**不发起探测**，该行 `quotas: null` 并带 `error`：

| 场景 | `error` 文案 |
|---|---|
| 账号处于冷却/熔断期 | `cooling: quota probe skipped to avoid extending rate limit` |
| 池内有该账号但无可用凭证 | `account not in pool; no credential available for a quota probe` |
| 整体探测超时（15s 预算耗尽） | `quota probe timed out` |
| 上游调用失败（网络/业务错误） | 上游错误原文，如 `upstream client (http 403): code=10085 msg=...` |

> 机器人应把 `quotas == null` 视为「本次无数据」，**不要当作 0**；建议重试或跳过该账号。

### 3.7 缓存与超时语义（重要）

| 项 | 值 | 说明 |
|---|---|---|
| 缓存 TTL | **30 秒** | 30s 内的重复请求直接返回缓存，**不触发上游探测** |
| 探测总预算 | **15 秒** | 所有账号并发探测，共享该 deadline；超时账号标记 `quota probe timed out` |
| 并发 | 全账号并发 | 单账号失败不影响其他账号 |

**机器人轮询建议**：轮询间隔 **≥ 60 秒**（避免频繁打满上游）；服务端已做 30s 去重，
即使多机器人同时调用也不会放大上游请求。

---

## 4. `GET /v1/a/{uid}/quota` — 单账号额度

```http
GET /v1/a/232b76a9-2f49-42f7-97f1-deec3df5cbb5/quota
Authorization: Bearer <api_key>
```

响应：

```json
{ "provider": "workbuddy", "accounts": [ { "uid": "…", "quotas": { … } } ] }
```

- 复用 `/v1/quota` 的**同一份缓存**（不会额外触发全量探测）。
- 账号不存在 → `404`：

```json
{ "error": { "message": "account not found: <uid>", "type": "api_error", "code": "not_found" } }
```

---

## 5. `GET /v1/accounts` — 账号清单（轻量，无配额）

用于获取**账号集合与状态**（不需要实时额度时用，零上游调用）：

```json
{
  "provider": "workbuddy",
  "accounts": [
    {
      "uid": "232b76a9-…",
      "nickname": "星际猫",
      "credits": 2013,
      "cooling": false,
      "disabled": false,
      "chat_path": "/v1/a/232b76a9-…/chat/completions",
      "quota_path": "/v1/a/232b76a9-…/quota",
      "cool_until": "2026-09-24T14:22:10Z",
      "cool_remaining": "2h 13m 05s"
    }
  ]
}
```

> `credits` 为池内缓存值；`cool_until` / `cool_remaining` 仅在 `cooling=true` 时出现。

---

## 6. `GET /status` 与 `GET /healthz`

### `GET /status`（需鉴权，无配额明细）

```json
{
  "accounts": [ { "uid": "…", "credits": 2013, "cooling": false, "disabled": false, "in_flight": 0, "breaker_fails": 0 } ],
  "total": 2,
  "healthy": 1,
  "cooling": 1,
  "disabled": 0,
  "in_flight_full": 0,
  "sticky_sessions": 0,
  "redis_mode": "noop"
}
```

### `GET /healthz`（**无鉴权**）

```json
{ "healthy": 1, "total": 2, "service": "workbuddy2api" }
```

- HTTP `200` = 有可服务账号；`503` = 无可用账号。
- 响应头 `X-Service: workbuddy2api`（可用于确认打到的确实是本网关）。

---

## 7. 控制台备选接口（会话鉴权）

若机器人已接入控制台（而非网关 api_key），可用以下端点。它们返回**聚合口径**
（剩余/已用/总量/套餐数），**不含逐套餐明细**。

### 7.1 异步批量查询（推荐给「批量」场景）

```http
POST /api/tasks/credits
Authorization: Bearer <session-token>
Content-Type: application/json

{ "uids": [] }
```

`uids` 为空数组 = 全量账号。响应（`202 Accepted`）：

```json
{ "id": "credits-7", "kind": "credits", "title": "批量查询积分（2 个账号）", "running": true,
  "started_at": "2026-09-24T14:00:00Z", "total": 0, "done": 0, "ok": 0, "failed": 0, "items": [] }
```

轮询进度：

```http
GET /api/tasks/credits-7
Authorization: Bearer <session-token>
```

完成后的 `items[]`：

```json
{
  "uid": "232b76a9-…", "nickname": "星际猫", "action": "credits", "ok": true,
  "message": "剩余 2013 / 总量 3708（58 个套餐）",
  "started_at": "…", "ended_at": "…"
}
```

> `message` 为**人类可读串**；需要结构化数字请用 `/v1/quota`。
> 账号间隔 200ms 串行执行，避免上游风控。

### 7.2 单账号积分（聚合）

```http
GET /api/accounts/{uid}/credits
Authorization: Bearer <session-token>
```

```json
{ "remain": 2013, "used": 1695, "size": 3708, "packages": 58 }
```

| 字段 | 说明 |
|---|---|
| `remain` | 剩余积分（所有套餐聚合） |
| `used` | 已用积分 |
| `size` | 总量积分 |
| `packages` | 套餐数 |

---

## 8. 聚合口径（机器人如何算「剩余 / 总量 / 套餐数」）

基于 `/v1/quota` 的 `quotas` 对象，对单个账号：

```text
套餐数   packageCount = len(quotas)                       # 键的个数
剩余     remaining    = Σ quotas[*].remaining
总量     total        = Σ quotas[*].total
已用     used         = Σ quotas[*].used
```

全池汇总：

```text
总剩余 = Σ(每个账号 remaining)
总总量 = Σ(每个账号 total)
总套餐 = Σ(每个账号 packageCount)
```

**参考实现（Python）**：

```python
import httpx

def fetch_all_quotas(base: str, api_key: str) -> list[dict]:
    r = httpx.get(
        f"{base}/v1/quota",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    )
    r.raise_for_status()
    rows = []
    for a in r.json()["accounts"]:
        q = a.get("quotas") or {}
        rows.append({
            "uid": a["uid"],
            "nickname": a.get("nickname", ""),
            "cooling": a["cooling"],
            "disabled": a["disabled"],
            "ok": a.get("quotas") is not None,
            "error": a.get("error", ""),
            "remaining": sum(p["remaining"] for p in q.values()),
            "total": sum(p["total"] for p in q.values()),
            "used": sum(p["used"] for p in q.values()),
            "packages": len(q),
            "detail": q,          # 逐套餐明细（可选保留）
        })
    return rows
```

---

## 9. 调用示例

### curl

```bash
# 批量额度（推荐）
curl -s http://127.0.0.1:7863/v1/quota \
  -H "Authorization: Bearer your-api-key"

# 单账号
curl -s "http://127.0.0.1:7863/v1/a/232b76a9-2f49-42f7-97f1-deec3df5cbb5/quota" \
  -H "Authorization: Bearer your-api-key"

# 健康检查（无鉴权）
curl -s http://127.0.0.1:7863/healthz
```

### JavaScript

```js
const res = await fetch(`${base}/v1/quota`, {
  headers: { Authorization: `Bearer ${apiKey}` },
});
if (!res.ok) throw new Error(`HTTP ${res.status}`);
const { accounts } = await res.json();
for (const a of accounts) {
  const q = a.quotas ?? {};
  const remaining = Object.values(q).reduce((s, p) => s + p.remaining, 0);
  const total = Object.values(q).reduce((s, p) => s + p.total, 0);
  console.log(a.uid, `${remaining}/${total}`, `${Object.keys(q).length} 个套餐`);
}
```

---

## 10. 错误码与容错

| HTTP | 场景 | 处理建议 |
|---|---|---|
| `200` | 正常 | — |
| `401` | api_key 缺失/错误 | 检查 `Authorization` 头 |
| `404` | 单账号 uid 不存在 | 跳过该 uid（可能已被删除） |
| `503` | 聊天无可用账号（`/v1/chat/completions`） | 与额度查询无关 |
| `500` | 服务内部错误 | 退避重试 |

**行级容错**：`/v1/quota` 恒返回 `200`（除非鉴权失败），单账号失败通过行内
`quotas: null` + `error` 表达，**不会**让整个响应失败。机器人应逐行判断。

---

## 11. 上游来源与区域差异（背景）

额度数据来自上游计费接口（只读）：

| 区域 | 计费基址 | 路径 |
|---|---|---|
| 国内版 `cn` | `https://www.codebuddy.cn` | `POST /v2/billing/meter/get-user-resource` |
| 国际版 `global` | `https://www.workbuddy.ai` | `POST /billing/meter/get-user-resource`（404 时回退 `/v2/billing/meter/...`） |

- 请求体：`{"PageNumber":1,"PageSize":200}`（配额探测不带上限过滤，以覆盖赠送包）。
- 需携带**网页客户端指纹**（`User-Agent` / `X-Client-Platform: web` / `Origin` / `Referer`），
  否则上游网关会以业务码 `10085 请求不合法` 拒绝（该问题已修复）。
- 本网关已把上述细节封装，机器人**无需**关心区域与指纹，直接调 `/v1/quota` 即可。

---

## 12. 给机器人的实现建议

1. **轮询间隔 ≥ 60s**：服务端 30s 缓存 + 15s 探测预算，高频轮询无收益且可能触发上游限流。
2. **用 `/v1/quota` 一次拿全量**，不要逐账号调 `/v1/a/{uid}/quota`（后者虽走缓存，但无必要）。
3. **`quotas == null` 不是 0**：代表本次未探测（冷却/超时/失败），应标注为「未知」而非「耗尽」。
4. **`credits` 字段不可信**：它是池内缓存值，实时额度以 `quotas` 聚合为准。
5. **字段容错**：`nickname`/`error`/`cool_*` 均为可选字段，解析时按缺省处理。
6. **套餐数取 `len(quotas)`**；同名套餐会以 `"名字 2"` 形式出现，按 `packageName` 归类时可合并。
7. **告警阈值**：对 `remaining / total` 比例设阈值；`disabled=true` 或长期 `cooling=true` 单独告警。

---

## 13. 机器可读摘要（供代码生成/校验）

```jsonc
{
  "endpoint": "GET /v1/quota",
  "auth": { "type": "bearer", "header": "Authorization", "scheme": "Bearer <api_key>", "optional_if_api_key_empty": true },
  "response": {
    "provider": "workbuddy",
    "accounts": [{
      "uid": "string",
      "nickname": "string?",
      "credits": "int (pool cache, unreliable)",
      "cooling": "bool",
      "disabled": "bool",
      "quotas": "object|null  // null = not probed",
      "cool_kind": "hard_credit|soft_rate?",
      "cool_until": "iso8601?",
      "cool_remaining": "string?",
      "reason": "string?",
      "success_count": "int?",
      "err_total": "int?",
      "error": "string?"
    }],
    "quotas_entry": {
      "packageName": "string",
      "total": "int",
      "used": "int",
      "remaining": "int",
      "resetAt": "string (may be empty)",
      "recurring": "bool"
    }
  },
  "aggregation": {
    "per_account_remaining": "sum(quotas[*].remaining)",
    "per_account_total": "sum(quotas[*].total)",
    "per_account_package_count": "len(quotas)",
    "pool_remaining": "sum(per_account_remaining)"
  },
  "cache_ttl_seconds": 30,
  "probe_budget_seconds": 15,
  "recommended_poll_interval_seconds": 60
}
```
