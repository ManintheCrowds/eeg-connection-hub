# PURPOSE: Hub runtime configuration with safe defaults (loopback, no raw EEG).
# DEPENDENCIES: dataclasses, secrets
"""Configuration for eeg-connection-hub."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_loopback_host(host: str) -> bool:
    return host.strip().lower().strip("[]") in LOOPBACK_HOSTS


@dataclass(frozen=True)
class HubConfig:
    """Validated hub server configuration."""

    host: str = "127.0.0.1"
    ws_port: int = 8765
    http_port: int = 8766
    feature_hz: float = 10.0
    window_sec: float = 2.0
    allow_lan: bool = False
    access_token: str = ""
    enable_raw_eeg: bool = False
    synth_streams: int = 0
    session_label_prefix: str = "participant"
    allow_origins: tuple[str, ...] = ()
    allow_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.allow_lan and not self.access_token:
            object.__setattr__(self, "access_token", secrets.token_urlsafe(32))
        if not is_loopback_host(self.host) and not self.allow_lan:
            raise ValueError(
                f"non-loopback bind {self.host!r} requires --allow-lan (and an access token); "
                "use the default 127.0.0.1 for local-only use"
            )
        if self.enable_raw_eeg and not is_loopback_host(self.host):
            raise ValueError("raw EEG export is loopback-only")
        if self.feature_hz <= 0:
            raise ValueError("feature_hz must be positive")
        if self.window_sec <= 0:
            raise ValueError("window_sec must be positive")

    @property
    def bind_all(self) -> bool:
        return self.host in ("0.0.0.0", "::")

    @classmethod
    def loopback_synth(cls, *, streams: int = 2) -> HubConfig:
        """Factory for local demo with synthetic streams."""
        return cls(synth_streams=max(1, streams))
