# PURPOSE: Stable stream registry and pseudonymous session assignment.
# DEPENDENCIES: dataclasses, secrets, time
"""Stream catalog and subscriber session routing."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamState:
    """Runtime state for one discovered EEG stream."""

    stream_id: str
    lsl_name: str
    session_id: str
    session_label: str
    status: str = "warming"
    last_frame_ts: float = 0.0
    last_publish_ts: float = 0.0
    stale: bool = False
    message: str = ""

    def to_catalog_entry(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "lsl_name": self.lsl_name,
            "session_id": self.session_id,
            "session_label": self.session_label,
            "status": self.status,
            "stale": self.stale,
            "last_frame_ts": self.last_frame_ts,
            "message": self.message[:160],
        }


@dataclass
class StreamRegistry:
    """Assign stable ids and sessions; track catalog for HTTP/WS."""

    label_prefix: str = "participant"
    stale_after_sec: float = 5.0
    _streams: dict[str, StreamState] = field(default_factory=dict)
    _session_counter: int = 0

    def register(
        self,
        stream_id: str,
        lsl_name: str,
        *,
        session_label: str | None = None,
    ) -> StreamState:
        if stream_id in self._streams:
            return self._streams[stream_id]
        self._session_counter += 1
        session_id = secrets.token_hex(8)
        label = session_label or f"{self.label_prefix}-{self._session_counter:03d}"
        state = StreamState(
            stream_id=stream_id,
            lsl_name=lsl_name,
            session_id=session_id,
            session_label=label,
        )
        self._streams[stream_id] = state
        return state

    def get(self, stream_id: str) -> StreamState | None:
        return self._streams.get(stream_id)

    def update_frame(
        self,
        stream_id: str,
        *,
        status: str,
        frame_ts: float,
        message: str = "",
    ) -> None:
        state = self._streams.get(stream_id)
        if state is None:
            return
        now = time.time()
        state.status = status
        state.last_frame_ts = frame_ts
        state.last_publish_ts = now
        state.message = message[:160]
        state.stale = False

    def mark_stale(self, stream_id: str, message: str = "stream stopped delivering samples") -> None:
        state = self._streams.get(stream_id)
        if state is None:
            return
        state.stale = True
        state.status = "error"
        state.message = message[:160]

    def refresh_stale_flags(self) -> None:
        now = time.time()
        for state in self._streams.values():
            if state.last_publish_ts <= 0:
                continue
            if now - state.last_publish_ts > self.stale_after_sec:
                state.stale = True
                if state.status not in ("error", "empty"):
                    state.status = "error"
                    state.message = "no recent frames"

    def catalog(self) -> list[dict[str, Any]]:
        self.refresh_stale_flags()
        return [s.to_catalog_entry() for s in sorted(self._streams.values(), key=lambda x: x.stream_id)]

    def stream_ids(self) -> list[str]:
        return sorted(self._streams.keys())
