# PURPOSE: LAN access token validation for WebSocket and HTTP subscribers.
# DEPENDENCIES: none
"""Authentication helpers for LAN-exposed hub instances."""

from __future__ import annotations

from eeg_connection_hub.config import HubConfig


def token_required(config: HubConfig) -> bool:
    """Return True when clients must present an access token."""
    return config.allow_lan or config.bind_all


def validate_token(config: HubConfig, provided: str | None) -> bool:
    """Validate subscriber token; loopback-only mode skips auth."""
    if not token_required(config):
        return True
    if not provided or not config.access_token:
        return False
    return secrets_compare(provided, config.access_token)


def secrets_compare(a: str, b: str) -> bool:
    """Constant-time string comparison."""
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
