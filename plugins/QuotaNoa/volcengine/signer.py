"""火山引擎（Volcengine）OpenAPI 签名：SigV4（HMAC-SHA256）。

依据实测文档 ``tests/volcengine.md``：

- 额度查询走控制面 ``https://open.volcengineapi.com/``，Action=``GetCodingPlanUsage``。
- 使用 AccessKey ID + SecretAccessKey 做 Volcengine Signature V4。
- **直接使用控制台字面 AK/SK 字符串，不要自行 base64 解码**（实测解 base64 会验签失败）。
- canonical headers 固定为 ``host;x-date;x-content-sha256;content-type``。
- 空 body 的 SHA-256 为固定常量。
- credential_scope = ``{YYYYMMDD}/{region}/ark/request``。
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import quote

SERVICE = "ark"
EMPTY_BODY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

_DEFAULT_REGION = "cn-beijing"


def sha256_hex(text: str | bytes) -> str:
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()


def _hmac_sha256(key: bytes, msg: str | bytes) -> bytes:
    if isinstance(msg, str):
        msg = msg.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).digest()


def _percent_encode(text: str) -> str:
    """RFC 3986 百分号编码（保留字符不转义）。"""
    return quote(text, safe="-_.~")


def _canonical_query_string(params: Mapping[str, str]) -> str:
    """按 key 字典序拼接 canonical query（RFC 3986 编码）。"""
    items = sorted(params.items())
    return "&".join(f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in items)


def canonical_query_string(params: Mapping[str, str]) -> str:
    """公开的 canonical query 构造，供签名与真实请求 URL 共用，确保同源。"""
    return _canonical_query_string(params)


def sign_request(
    *,
    access_key_id: str,
    secret_access_key: str,
    method: str = "POST",
    host: str = "open.volcengineapi.com",
    path: str = "/",
    query: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    body: bytes | str = b"",
    region: str = _DEFAULT_REGION,
    x_date: datetime | None = None,
) -> dict[str, str]:
    """返回可直接作为 HTTP 请求头使用的 ``Authorization`` 等字段。

    返回的 dict 包含 ``Authorization``、``X-Date``、``X-Content-Sha256``。
    """
    if isinstance(body, str):
        body_bytes = body.encode("utf-8")
    else:
        body_bytes = body

    now = x_date or datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    body_hash = sha256_hex(body_bytes)
    x_content_sha256 = body_hash

    query = query or {}
    canonical_query = _canonical_query_string(query)

    req_headers = dict(headers or {})
    req_headers.setdefault("content-type", "application/json; charset=utf-8")
    canonical_headers = (
        f"host:{host}\n"
        f"x-date:{amz_date}\n"
        f"x-content-sha256:{x_content_sha256}\n"
        f"content-type:{req_headers['content-type']}\n"
    )
    signed_headers = "host;x-date;x-content-sha256;content-type"

    canonical_request = "\n".join(
        [
            method.upper(),
            path,
            canonical_query,
            canonical_headers,
            signed_headers,
            body_hash,
        ]
    )

    credential_scope = f"{date_stamp}/{region}/{SERVICE}/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            amz_date,
            credential_scope,
            sha256_hex(canonical_request),
        ]
    )

    k_date = _hmac_sha256(secret_access_key.encode("utf-8"), date_stamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, SERVICE)
    k_signing = _hmac_sha256(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"HMAC-SHA256 Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    return {
        "Authorization": authorization,
        "X-Date": amz_date,
        "X-Content-Sha256": x_content_sha256,
        "Content-Type": req_headers["content-type"],
    }
