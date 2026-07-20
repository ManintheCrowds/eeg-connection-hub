# PURPOSE: Validate Muse channel mapping and LSL chunk timestamps per stream.
# DEPENDENCIES: numpy, lsl_metadata, features
"""Live stream mapping and timestamp validation."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from eeg_connection_hub.features import BANDS, EEG_CHANNELS
from eeg_connection_hub.lsl_metadata import (
    stream_channel_count,
    stream_channel_names,
    stream_sample_rate,
)


@dataclass(frozen=True)
class LiveStreamConfig:
    """Validated live stream sampling and Muse channel mapping."""

    sample_rate: float
    indices: tuple[int, int, int, int]
    channel_count: int


def build_channel_mapping(stream_info: object) -> LiveStreamConfig:
    """Validate stream metadata and map advertised labels to Muse EEG order."""
    sample_rate = stream_sample_rate(stream_info)
    highest_band_edge = max(hi for _, hi in BANDS.values())
    if not math.isfinite(sample_rate) or sample_rate <= 2.0 * highest_band_edge:
        raise ValueError("invalid LSL sample rate for EEG band extraction")
    channel_count = stream_channel_count(stream_info)
    names = stream_channel_names(stream_info)
    if channel_count <= 0 or len(names) != channel_count:
        raise ValueError("LSL channel labels do not match advertised channel count")
    normalized = [name.strip().upper() for name in names]
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate LSL channel labels")
    missing = [name for name in EEG_CHANNELS if name not in normalized]
    if missing:
        raise ValueError(f"missing Muse EEG channels: {', '.join(missing)}")
    return LiveStreamConfig(
        sample_rate=sample_rate,
        indices=tuple(normalized.index(name) for name in EEG_CHANNELS),  # type: ignore[arg-type]
        channel_count=channel_count,
    )


def next_resolve_delay(attempt: int, base: float = 0.5, cap: float = 5.0) -> float:
    """Return bounded exponential delay between LSL resolve attempts."""
    return min(cap, base * (2 ** max(0, attempt)))


def validate_lsl_chunk_timestamps(
    timestamps: list[float],
    *,
    sample_count: int,
    now: float,
    max_age: float = 1.0,
    max_future: float = 0.25,
) -> float:
    """Reject empty, stale, future, mismatched, or unordered LSL sample times."""
    if sample_count <= 0 or len(timestamps) != sample_count:
        raise ValueError("LSL timestamp count does not match sample count")
    values = np.asarray(timestamps, dtype=np.float64)
    if values.ndim != 1 or not np.all(np.isfinite(values)):
        raise ValueError("LSL sample timestamps must be finite")
    if np.any(np.diff(values) < 0):
        raise ValueError("LSL sample timestamps must be ordered")
    latest = float(values[-1])
    if now - latest > max_age:
        raise ValueError("stale LSL sample timestamps")
    if latest - now > max_future:
        raise ValueError("future LSL sample timestamps")
    return latest
