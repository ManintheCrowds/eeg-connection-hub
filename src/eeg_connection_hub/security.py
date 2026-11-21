# PURPOSE: Browser-facing hardening — Origin allowlist, Host check (DNS rebinding), token extraction,
#          and log redaction for access tokens.
# DEPENDENCIES: ipaddress, logging, re, urllib.parse, config
"""Request-level security helpers shared by the HTTP and WebSocket front doors."""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from urllib.parse import quote, quote_plus, urlsplit

from eeg_connection_hub.config import LOOPBACK_HOSTS, HubConfig

_TOKEN_QUERY_RE = re.compile(r"(?i)([?&]token=)[A-Za-z0-9._~+/%=-]+")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_PERCENT_ESC = re.compile(r"%[0-9A-Fa-f]{2}")
REDACTED = "***"
# Mixed-case percent-encoding blows up as 2**n. Past this, keep the fully
# upper and fully lower hex spellings, which are what encoders actually emit.
_MAX_PERCENT_VARIANTS = 6


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


def _percent_case_variants(encoded: str) -> list[str]:
    """Upper/lower hex spellings of one percent-encoded string."""
    pieces = _PERCENT_ESC.split(encoded)
    escapes = _PERCENT_ESC.findall(encoded)
    if not escapes:
        return [encoded]
    if len(escapes) > _MAX_PERCENT_VARIANTS:
        lower = _PERCENT_ESC.sub(lambda match: match.group(0).lower(), encoded)
        upper = _PERCENT_ESC.sub(lambda match: match.group(0).upper(), encoded)
        return list(dict.fromkeys((encoded, lower, upper)))
    variants: list[str] = []
    for mask in range(1 << len(escapes)):
        parts: list[str] = []
        for index, piece in enumerate(pieces[:-1]):
            parts.append(piece)
            esc = escapes[index]
            parts.append(esc.upper() if mask & (1 << index) else esc.lower())
        parts.append(pieces[-1])
        variants.append("".join(parts))
    return variants


def configured_token_forms(secret: str) -> list[str]:
    """Raw configured token, then its percent-encoded forms, longest first.

    Encodings cover ``quote`` / ``quote_plus`` (with and without ``/`` left
    intact), a second encoding pass, and both hex cases. The raw value is
    always included so a later pattern pass cannot leave a suffix behind.
    """
    if not secret:
        return []
    found: dict[str, None] = {secret: None}
    seeds = (
        quote(secret, safe=""),
        quote(secret, safe="/"),
        quote_plus(secret, safe=""),
        quote_plus(secret, safe="/"),
    )
    encoded_seeds = (*seeds, quote(seeds[0], safe=""))
    for seed in encoded_seeds:
        for variant in _percent_case_variants(seed):
            found.setdefault(variant, None)
    rest = [item for item in found if item != secret]
    rest.sort(key=len, reverse=True)
    return [secret, *rest]


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Remove access tokens from free text / URLs.

    The exact configured token and its percent-encoded forms are replaced
    before the ``token=`` and ``Bearer`` patterns. Patterns alone stop at
    characters such as ``!``, ``:``, and ``,``, which would leave the rest of
    the token in a header dump or a ``?token=`` line.
    """
    forms: list[str] = []
    seen: set[str] = set()
    for secret in secrets:
        for form in configured_token_forms(secret):
            if form not in seen:
                seen.add(form)
                forms.append(form)
    forms.sort(key=len, reverse=True)
    out = text
    for form in forms:
        if form:
            out = out.replace(form, REDACTED)
    out = _TOKEN_QUERY_RE.sub(lambda m: m.group(1) + REDACTED, out)
    out = _BEARER_RE.sub(lambda m: m.group(1) + REDACTED, out)
    return out


# Formatter used only to render exc_info before it is discarded. The logging
# Formatter caches that rendering on exc_text and will not rebuild it while
# exc_info is cleared, so the token cannot reappear from the traceback.
_EXC_FORMATTER = logging.Formatter()


class RedactingFilter(logging.Filter):
    """Handler filter that scrubs tokens from every formatted record (incl. websockets debug logs).

    The message, ``exc_info`` / ``exc_text``, and ``stack_info`` are all cleaned.
    websockets logs a malformed header as ``logger.debug(..., exc_info=True)``;
    the token then sits in the ``ValueError`` traceback, not in the message.
    """

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        self._redact_message(record)
        self._redact_exception(record)
        self._redact_stack(record)
        return True

    def _redact_message(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            message = record.msg if isinstance(record.msg, str) else ""
        cleaned = redact(message, self.secrets)
        if cleaned != message:
            record.msg = cleaned
            record.args = None

    def _redact_exception(self, record: logging.LogRecord) -> None:
        text = record.exc_text or ""
        exc_info = record.exc_info
        if not text and isinstance(exc_info, tuple) and exc_info[0] is not None:
            text = _EXC_FORMATTER.formatException(exc_info)
        if text:
            record.exc_text = redact(text, self.secrets)
        # Drop the live exception. A later Formatter.formatException(exc_info)
        # would rebuild the traceback from the original ValueError, token and all.
        if exc_info:
            record.exc_info = None

    def _redact_stack(self, record: logging.LogRecord) -> None:
        if isinstance(record.stack_info, str) and record.stack_info:
            record.stack_info = redact(record.stack_info, self.secrets)


def install_log_redaction(secrets: Iterable[str] = ()) -> RedactingFilter:
    """Attach a RedactingFilter to every root handler (filters on handlers see propagated records)."""
    flt = RedactingFilter(secrets)
    for handler in logging.getLogger().handlers:
        handler.addFilter(flt)
    return flt
