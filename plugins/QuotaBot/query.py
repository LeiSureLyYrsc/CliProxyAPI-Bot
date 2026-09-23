"""``/quota`` 查询参数解析。

位置参数三选消歧（顺序无关）：
1. 渠道关键字（claude / codex / 火山 …）→ 平台；
2. 已配置的 CPA 实例名 → 限定实例；
3. 其它 → 账号查询词（跨实例搜索）。

``--instance <名称>`` 可显式指定实例，避免与渠道名冲突。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import normalize_name, valid_name
from .model import is_channel_name, normalize_channel

INSTANCE_FLAGS = {"--instance", "-i"}
ALL_FLAGS = {"all", "--all", "-all", "-a"}
FRESH_FLAGS = {"--fresh", "--refresh", "-f"}
TEXT_FLAGS = {"--text", "-t"}


@dataclass
class QuotaSelection:
    instance: str | None = None
    platform: str | None = None
    account: str | None = None
    fresh: bool = False
    text: bool = False
    error: str | None = None


def tokenize(text: str) -> list[str]:
    return [item for item in (text or "").replace("\u3000", " ").split() if item]


def strip_quota_head(parts: list[str]) -> list[str]:
    leftover = list(parts)
    if leftover and leftover[0].lstrip("/").lower() in {"cpa", "quota"}:
        leftover = leftover[1:]
    if leftover and leftover[0].lower() == "quota":
        leftover = leftover[1:]
    return leftover


def parse_quota_command(
    text: str,
    *,
    known_instances: set[str],
) -> QuotaSelection:
    return parse_quota_parts(
        strip_quota_head(tokenize(text)),
        known_instances=known_instances,
    )


def parse_quota_parts(
    parts: list[str],
    *,
    known_instances: set[str],
) -> QuotaSelection:
    known = {normalize_name(name) for name in known_instances if valid_name(name)}
    fresh = False
    text_mode = False
    explicit_instance: str | None = None
    positional: list[str] = []
    index = 0
    while index < len(parts):
        token = parts[index]
        lowered = token.lower()
        if lowered in {"cooling", "reset"} and index == 0:
            return QuotaSelection(error="")
        if lowered in ALL_FLAGS:
            # 显式 all = 查询全部实例（与“不指定实例”等价，默认即全部）。
            index += 1
            continue
        if lowered in INSTANCE_FLAGS:
            if index + 1 >= len(parts):
                return QuotaSelection(error="--instance 需要实例名称。")
            name = normalize_name(parts[index + 1])
            if not valid_name(name):
                return QuotaSelection(error=f"实例名称非法：{parts[index + 1]}")
            explicit_instance = name
            index += 2
            continue
        if lowered in FRESH_FLAGS:
            fresh = True
            index += 1
            continue
        if lowered in TEXT_FLAGS:
            text_mode = True
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        positional.append(token)
        index += 1

    platforms: list[str] = []
    instances: list[str] = []
    accounts: list[str] = []
    ambiguous: list[str] = []
    for token in positional:
        name = normalize_name(token)
        platform = normalize_channel(token) if is_channel_name(token) else ""
        # 已用 --instance 显式指定实例时，位置参数不再当实例名，直接落到账号查询词。
        is_known_instance = (name in known) if not explicit_instance else False
        if platform and is_known_instance:
            ambiguous.append(token)
            continue
        if platform:
            platforms.append(platform)
            continue
        if is_known_instance:
            instances.append(name)
            continue
        accounts.append(token)

    if ambiguous:
        shown = "、".join(ambiguous)
        return QuotaSelection(
            error=(
                f"「{shown}」同时是渠道名称和实例名称。"
                f"\n查询该渠道全部实例：/quota {ambiguous[0]}"
                f"\n只查某个实例：/quota --instance {ambiguous[0]}"
                f"\n（提示：实例名不应与渠道名相同，建议重命名实例）"
            )
        )
    if len(platforms) > 1:
        return QuotaSelection(error="一次只能查询一个平台。")
    if len(instances) > 1:
        return QuotaSelection(error="一次只能指定一个实例。")
    if len(accounts) > 1:
        return QuotaSelection(error="一次只能指定一个账号查询词。")

    instance_from_pos = instances[0] if instances else None
    if explicit_instance and instance_from_pos and explicit_instance != instance_from_pos:
        return QuotaSelection(error="指定了多个不同的实例。")

    return QuotaSelection(
        instance=explicit_instance or instance_from_pos,
        platform=platforms[0] if platforms else None,
        account=accounts[0] if accounts else None,
        fresh=fresh,
        text=text_mode,
    )
