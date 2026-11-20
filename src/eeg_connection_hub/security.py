# PURPOSE: Browser-facing hardening — Origin allowlist, Host check (DNS rebinding), token extraction,
#          and log redaction for access tokens.
# DEPENDENCIES: ipaddress, logging, re, urllib.parse, config
"""Request-level security helpers shared by the HTTP and WebSocket front doors."""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from urllib.parse import urlsplit

from eeg_connection_hub.config import LOOPBACK_HOSTS, HubConfig

_TOKEN_QUERY_RE = re.compile(r"(?i)([?&]token=)[A-Za-z0-9._~+/%=-]+")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
REDACTED = "***"


def _strip_brackets(host: str) -> str:
    return host[1:-1] if host.startswith("[") and host.endswith("]") else host


def parse_host_header(value: str | None) -> str | None:
    """Return the lowercased hostname from a Host header (port stripped), or None if malformed."""
    if not value:
        return None
    value = value.strip().lower()
    if not value or any(ch in value for ch in " /\\@?#"):
        return None
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return None
        host, rest = value[1:end], value[end + 1 :]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return None
        return host
    if value.count(":") == 1:
        host, port = value.split(":", 1)
        if not port.isdigit():
            return None
        return host or None
    if ":" in value:
        # Bare IPv6 without brackets is not a valid Host header.
        return None
    return value


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(_strip_brackets(host))
    except ValueError:
        return False
    return True


def host_allowed(config: HubConfig, host_header: str | None) -> bool:
    """Defeat DNS rebinding: only accept Host values we actually answer to.

    Loopback mode: localhost / 127.0.0.1 / [::1] (any port).
    LAN mode: loopback names, the bound host, any IP literal (rebinding needs a DNS name), and
    any ``--allow-host`` names.
    """
    host = parse_host_header(host_header)
    if host is None:
        return False
    if host in LOOPBACK_HOSTS:
        return True
    extra = {h.lower() for h in config.allow_hosts}
    if host in extra:
        return True
    if not config.allow_lan:
        return False
    if host == _strip_brackets(config.host.lower()):
        return True
    return _is_ip_literal(host)


def normalize_origin(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def is_loopback_origin(origin: str) -> bool:
    try:
        parts = urlsplit(origin)
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        return False
    return (parts.hostname or "") in LOOPBACK_HOSTS


def origin_allowed(config: HubConfig, origin: str | None) -> bool:
    """No Origin (non-browser client) and loopback origins pass; others need ``--allow-origin``."""
    if origin is None:
        return True
    norm = normalize_origin(origin)
    if norm in {normalize_origin(o) for o in config.allow_origins}:
        return True
    return is_loopback_origin(norm)


def check_request(config: HubConfig, host_header: str | None, origin: str | None) -> str | None:
    """Return a short rejection reason, or None when the request may proceed."""
    if not host_allowed(config, host_header):
        return "forbidden host"
    if not origin_allowed(config, origin):
        return "forbidden origin"
    return None


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def extract_token(
    get_header: Callable[[str], str | None],
    query: Mapping[str, list[str]],
) -> tuple[str | None, bool]:
    """Return (token, via_query). Authorization: Bearer wins over the deprecated ``?token=``."""
    token = bearer_token(get_header("Authorization"))
    if token:
        return token, False
    q = (query.get("token") or [None])[0]
    return (q, True) if q else (None, False)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Remove access tokens from free text / URLs."""
    out = _TOKEN_QUERY_RE.sub(lambda m: m.group(1) + REDACTED, text)
    out = _BEARER_RE.sub(lambda m: m.group(1) + REDACTED, out)
    for secret in secrets:
        if secret:
            out = out.replace(secret, REDACTED)
    return out


class RedactingFilter(logging.Filter):
    """Handler filter that scrubs tokens from every formatted record (incl. websockets debug logs)."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact(message, self.secrets)
        if cleaned != message:
            record.msg = cleaned
            record.args = None
        return True


def install_log_redaction(secrets: Iterable[str] = ()) -> RedactingFilter:
    """Attach a RedactingFilter to every root handler (filters on handlers see propagated records)."""
    flt = RedactingFilter(secrets)
    for handler in logging.getLogger().handlers:
        handler.addFilter(flt)
    return flt
