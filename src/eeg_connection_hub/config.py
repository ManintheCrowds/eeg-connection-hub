# PURPOSE: Hub runtime configuration with safe defaults (loopback, no raw EEG).
# DEPENDENCIES: dataclasses, secrets
"""Configuration for eeg-connection-hub."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field


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

    def __post_init__(self) -> None:
        if self.allow_lan and not self.access_token:
            object.__setattr__(self, "access_token", secrets.token_urlsafe(32))
        if self.host not in ("127.0.0.1", "localhost", "0.0.0.0") and not self.allow_lan:
            raise ValueError("non-loopback bind requires allow_lan=True")
        if self.enable_raw_eeg and not self.host in ("127.0.0.1", "localhost"):
            raise ValueError("raw EEG export is loopback-only")
        if self.feature_hz <= 0:
            raise ValueError("feature_hz must be positive")
        if self.window_sec <= 0:
            raise ValueError("window_sec must be positive")

    @property
    def bind_all(self) -> bool:
        return self.host == "0.0.0.0"

    @classmethod
    def loopback_synth(cls, *, streams: int = 2) -> HubConfig:
        """Factory for local demo with synthetic streams."""
        return cls(synth_streams=max(1, streams))
