"""Cloudflare Worker entrypoint that proxies S3-compatible storage with AWS SigV4 signing."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import parse_qsl, quote, urlparse

import js
from pyodide.ffi import to_js
from workers import WorkerEntrypoint, Response

SERVICE_NAME = "s3"
FORWARDED_HEADERS = (
    "range",
    "if-none-match",
    "if-modified-since",
    "accept",
    "accept-encoding",
    "cache-control",
    "content-type",
    "if-match",
)
DEFAULT_ALLOWED_METHODS = "GET,HEAD,OPTIONS"


@dataclass(slots=True)
class WorkerConfig:
    bucket: str
    endpoint: str
    region: str
    access_key: str
    secret_key: str
    session_token: Optional[str]
    strip_prefix: str
    allowed_origins: Optional[str]
    cache_ttl: Optional[int]
    virtual_hosted: bool
    user_agent: str

    @classmethod
    def from_env(cls, env: object) -> "WorkerConfig":
        def _require(key: str) -> str:
            value = _env_get(env, key)
            if value in (None, ""):
                raise ValueError(f"Missing required binding: {key}")
            return str(value)

        endpoint = _require("S3_ENDPOINT").rstrip("/")
        bucket = _require("S3_BUCKET")

        region = _require("AWS_REGION")
        access_key = _require("AWS_ACCESS_KEY_ID")
        secret_key = _require("AWS_SECRET_ACCESS_KEY")

        session_token = _env_get(env, "AWS_SESSION_TOKEN")
        allowed_origins = _env_get(env, "ALLOWED_ORIGINS")
        cache_ttl_raw = _env_get(env, "CACHE_TTL")
        strip_prefix = _env_get(env, "STRIP_PREFIX") or ""
        virtual_hosted_raw = _env_get(env, "VIRTUAL_HOSTED_STYLE")
        user_agent = _env_get(env, "UPSTREAM_USER_AGENT")

        cache_ttl = int(cache_ttl_raw) if cache_ttl_raw else None

        virtual_hosted = False
        if virtual_hosted_raw:
            virtual_hosted = str(virtual_hosted_raw).lower() in {"1", "true", "yes"}

        return cls(
            bucket=bucket,
            endpoint=endpoint,
            region=region,
            access_key=access_key,
            secret_key=secret_key,
            session_token=str(session_token) if session_token else None,
            strip_prefix=str(strip_prefix or ""),
            allowed_origins=str(allowed_origins) if allowed_origins else None,
            cache_ttl=cache_ttl,
            virtual_hosted=virtual_hosted,
            user_agent=str(user_agent or "wplace-worker/1.0"),
        )


_CONFIG: Optional[WorkerConfig] = None


async def main(request, env, ctx=None):  # type: ignore[invalid-annotation]
    """Cloudflare Worker entrypoint compatible with the Python runtime."""
    del ctx  # Unused but part of the signature.

    try:
        config = _get_config(env)
    except ValueError as exc:
        return _text_response(str(exc), status=500)

    method = (request.method or "GET").upper()
    if method == "OPTIONS":
        return _handle_options(request, config)

    if method not in {"GET", "HEAD"}:
        return _text_response("Method not allowed.", status=405, headers={"Allow": DEFAULT_ALLOWED_METHODS})

    parsed_url = urlparse(str(request.url))
    object_key = _resolve_object_key(parsed_url.path or "/", config.strip_prefix)
    query_string = parsed_url.query

    target_url, host_header = _build_target_url(config, object_key, query_string)

    request_headers = request.headers
    forward_headers = _gather_forward_headers(request_headers, config)

    payload_hash = hashlib.sha256(b"").hexdigest()

    signed_headers = _sign_request(
        method=method,
        url=target_url,
        host=host_header,
        headers=forward_headers,
        payload_hash=payload_hash,
        credentials=config,
    )

    js_headers = js.Headers.new()
    for key, value in signed_headers.items():
        js_headers.append(key, value)

    init: Dict[str, object] = {"method": method, "headers": js_headers}

    if config.cache_ttl and method == "GET":
        init["cf"] = {"cacheTtl": int(config.cache_ttl)}

    try:
        response = await js.fetch(target_url, to_js(init))
    except Exception as exc:  # pragma: no cover - dependent on Cloudflare runtime
        return _text_response(f"Upstream fetch failed: {exc}", status=502)

    cors_origin = _resolve_cors_origin(request_headers.get("Origin"), config.allowed_origins)
    if cors_origin:
        response = _with_cors_headers(response, cors_origin)
    return response


def _get_config(env) -> WorkerConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = WorkerConfig.from_env(env)
    return _CONFIG


def _resolve_object_key(pathname: str, strip_prefix: str) -> str:
    path = pathname or "/"
    if strip_prefix:
        normalized_prefix = strip_prefix if strip_prefix.startswith("/") else f"/{strip_prefix}"
        if path.startswith(normalized_prefix):
            path = path[len(normalized_prefix) :]
    path = path.lstrip("/")
    return path


def _build_target_url(config: WorkerConfig, object_key: str, query: str) -> tuple[str, str]:
    parsed_endpoint = urlparse(config.endpoint)
    host = parsed_endpoint.netloc

    if config.virtual_hosted:
        host = f"{config.bucket}.{host}"
        base = f"{parsed_endpoint.scheme}://{host}"
        path = quote(f"/{object_key}", safe="/-_.~") if object_key else "/"
    else:
        base = f"{parsed_endpoint.scheme}://{host}"
        path = quote(f"/{config.bucket}/{object_key}", safe="/-_.~") if object_key else f"/{config.bucket}"

    canonical_query = _canonical_querystring(query)
    if canonical_query:
        return f"{base}{path}?{canonical_query}", host
    return f"{base}{path}", host


def _canonical_querystring(query: str) -> str:
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    pairs.sort()
    encoded = [
        f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}"
        for key, value in pairs
    ]
    return "&".join(encoded)


def _gather_forward_headers(headers, config: WorkerConfig) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for name in FORWARDED_HEADERS:
        value = headers.get(name)
        if value:
            result[name.lower()] = str(value)
    result["user-agent"] = config.user_agent
    return result


def _sign_request(
    *,
    method: str,
    url: str,
    host: str,
    headers: Dict[str, str],
    payload_hash: str,
    credentials: WorkerConfig,
) -> Dict[str, str]:
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    parsed = urlparse(url)
    canonical_uri = quote(parsed.path or "/", safe="/-_.~")
    canonical_querystring = _canonical_querystring(parsed.query)

    canonical_headers = {
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        **{key.lower(): value.strip() for key, value in headers.items()},
    }
    if credentials.session_token:
        canonical_headers["x-amz-security-token"] = credentials.session_token

    sorted_headers = sorted(canonical_headers.items())
    canonical_headers_string = "".join(f"{key}:{value}\n" for key, value in sorted_headers)
    signed_headers = ";".join(key for key, _ in sorted_headers)

    canonical_request = "\n".join(
        [
            method,
            canonical_uri,
            canonical_querystring,
            canonical_headers_string,
            signed_headers,
            payload_hash,
        ]
    )

    hashed_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    credential_scope = f"{date_stamp}/{credentials.region}/{SERVICE_NAME}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashed_request,
        ]
    )

    signing_key = _generate_signature_key(credentials.secret_key, date_stamp, credentials.region, SERVICE_NAME)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={credentials.access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    signed = dict(headers)
    signed.update(
        {
            "Host": host,
            "X-Amz-Date": amz_date,
            "X-Amz-Content-SHA256": payload_hash,
            "Authorization": authorization,
        }
    )
    if credentials.session_token:
        signed["X-Amz-Security-Token"] = credentials.session_token
    return signed


def _generate_signature_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    key_date = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    key_region = _sign(key_date, region)
    key_service = _sign(key_region, service)
    return _sign(key_service, "aws4_request")


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _resolve_cors_origin(request_origin: Optional[str], allowed: Optional[str]) -> Optional[str]:
    if not allowed:
        return None
    candidates = [entry.strip() for entry in allowed.split(",") if entry.strip()]
    if not candidates:
        return None
    if "*" in candidates:
        return request_origin or "*"
    if request_origin and request_origin in candidates:
        return request_origin
    return None


def _handle_options(request, config: WorkerConfig):
    request_headers = request.headers
    cors_origin = _resolve_cors_origin(request_headers.get("Origin"), config.allowed_origins)
    requested_headers = request_headers.get("Access-Control-Request-Headers") or ",".join(FORWARDED_HEADERS)

    headers = {
        "Access-Control-Allow-Methods": DEFAULT_ALLOWED_METHODS,
        "Access-Control-Allow-Headers": requested_headers,
        "Access-Control-Max-Age": "86400",
    }
    if cors_origin:
        headers["Access-Control-Allow-Origin"] = cors_origin
        if cors_origin != "*":
            headers["Access-Control-Allow-Credentials"] = "true"
            headers["Vary"] = "Origin"
    else:
        headers["Access-Control-Allow-Origin"] = "*"

    return _text_response("", status=204, headers=headers)


def _with_cors_headers(response, cors_origin: str):
    mutable = _clone_response(response)
    headers = mutable.headers
    headers.set("Access-Control-Allow-Origin", cors_origin)
    if cors_origin != "*":
        headers.set("Vary", "Origin")
        headers.set("Access-Control-Allow-Credentials", "true")
    return mutable


def _clone_response(response):
    headers_copy = js.Headers.new()
    iterator = response.headers.entries()
    while True:
        entry = iterator.next()
        if bool(entry.done):
            break
        key, value = entry.value
        headers_copy.append(key, value)

    init = {
        "status": int(response.status),
    }
    status_text = getattr(response, "statusText", "")
    if status_text:
        init["statusText"] = str(status_text)
    init["headers"] = headers_copy

    return js.Response.new(response.body, to_js(init))


def _text_response(body: str, *, status: int, headers: Optional[Dict[str, str]] = None):
    init = {"status": status, "headers": headers or {"Content-Type": "text/plain"}}
    return js.Response.new(body, to_js(init))


def _env_get(env: object, key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        v = getattr(env, key)
        return None if v is None else str(v)
    except Exception:
        return default



from workers import WorkerEntrypoint

class Default(WorkerEntrypoint):
    # Newer shape (docs as of Oct 2025)
    async def fetch(self, request):
        return await main(request, getattr(self, "env", None))

    # Back-compat for runtimes/SDKs that require on_fetch
    async def on_fetch(self, request, env=None, ctx=None):
        if env is None:
            env = getattr(self, "env", None)
        return await main(request, env, ctx)


__all__ = ["Default", "main"]
