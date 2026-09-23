from __future__ import annotations

from dataclasses import dataclass

from .model import is_channel_name, normalize_channel
from .protocol import normalize_client_name, valid_client_name

ALL_FLAGS = {"--all", "-all", "-a"}
CLIENT_FLAGS = {"--client", "-c"}
FRESH_FLAGS = {"--fresh", "--refresh", "-f"}
TEXT_FLAGS = {"--text", "-t"}


@dataclass
class QuotaSelection:
    all_clients: bool = False
    client_name: str | None = None
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
    known_clients: set[str],
    default_client: str,
) -> QuotaSelection:
    return parse_quota_parts(
        strip_quota_head(tokenize(text)),
        known_clients=known_clients,
        default_client=default_client,
    )


def parse_quota_parts(
    parts: list[str],
    *,
    known_clients: set[str],
    default_client: str,
) -> QuotaSelection:
    known = {normalize_client_name(name) for name in known_clients if valid_client_name(name)}
    default = normalize_client_name(default_client)
    all_clients = False
    explicit_client: str | None = None
    fresh = False
    text_mode = False
    positional: list[str] = []
    index = 0
    while index < len(parts):
        token = parts[index]
        lowered = token.lower()
        if lowered in {"cooling", "reset"} and index == 0:
            return QuotaSelection(error="")
        if lowered in ALL_FLAGS:
            all_clients = True
            index += 1
            continue
        if lowered in CLIENT_FLAGS:
            if index + 1 >= len(parts):
                return QuotaSelection(error="--client 需要客户端名称。")
            name = normalize_client_name(parts[index + 1])
            if not valid_client_name(name):
                return QuotaSelection(error=f"客户端名称非法：{parts[index + 1]}")
            explicit_client = name
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
    clients: list[str] = []
    accounts: list[str] = []
    ambiguous: list[str] = []
    for token in positional:
        name = normalize_client_name(token)
        platform = normalize_channel(token) if is_channel_name(token) else ""
        is_known_client = name in known
        if platform and is_known_client:
            ambiguous.append(token)
            continue
        if platform:
            platforms.append(platform)
            continue
        if is_known_client:
            clients.append(name)
            continue
        accounts.append(token)

    if ambiguous:
        shown = "、".join(ambiguous)
        return QuotaSelection(
            error=(
                f"「{shown}」同时是平台名称和客户端名称。"
                f"\n查询平台：/quota {ambiguous[0]} --client {default}"
                f"\n查询客户端：/quota --client {ambiguous[0]}"
            )
        )
    if len(platforms) > 1:
        return QuotaSelection(error="一次只能查询一个平台。")
    if len(clients) > 1:
        return QuotaSelection(error="一次只能指定一个客户端；查看全部请用 --all。")
    if len(accounts) > 1:
        return QuotaSelection(error="一次只能指定一个账号查询词。")
    client_from_pos = clients[0] if clients else None
    if all_clients and (explicit_client or client_from_pos):
        return QuotaSelection(error="不要同时指定客户端和 --all。")
    if explicit_client and client_from_pos and explicit_client != client_from_pos:
        return QuotaSelection(error="指定了多个不同的客户端。")

    selected = explicit_client or client_from_pos
    if not all_clients and selected is None:
        selected = default

    return QuotaSelection(
        all_clients=all_clients,
        client_name=None if all_clients else selected,
        platform=platforms[0] if platforms else None,
        account=accounts[0] if accounts else None,
        fresh=fresh,
        text=text_mode,
    )
