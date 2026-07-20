# PURPOSE: Synthetic LSL-equivalent sources for demo and integration tests.
# DEPENDENCIES: numpy, features, stream_mapping
"""In-memory EEG sources that mimic LSL inlets without pylsl."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from eeg_connection_hub.features import EEG_CHANNELS
from eeg_connection_hub.lsl_metadata import (
    stream_channel_count,
    stream_name,
    stream_sample_rate,
    stream_source_id,
    stream_type,
)


@dataclass
class SyntheticStreamInfo:
    """Minimal stream metadata object for discovery tests."""

    name: str
    stype: str = "EEG"
    sfreq: float = 256.0
    n_channels: int = 4
    source_id: str = ""

    def get_channel_names(self) -> list[str]:
        return list(EEG_CHANNELS)


@dataclass
class SyntheticInlet:
    """
    Generates sinusoidal Muse-like EEG chunks with independent phase per stream.

    Implements pull_chunk(timeout) compatible with pylsl StreamInlet usage in hub.
    """

    stream_info: SyntheticStreamInfo
    stream_index: int = 0
    phase_offset: float = 0.0
    _clock: float = field(default_factory=time.time)
    _sample_index: int = 0
    _stopped: bool = False
    _stale_mode: bool = False

    def pull_chunk(
        self,
        timeout: float = 0.0,
        max_samples: int = 64,
    ) -> tuple[list[list[float]], list[float]]:
        if self._stopped or self._stale_mode:
            return [], []
        sfreq = stream_sample_rate(self.stream_info)
        n = min(max_samples, 32)
        rows: list[list[float]] = []
        timestamps: list[float] = []
        base_freq = 10.0 + self.stream_index * 2.0
        for i in range(n):
            t = (self._sample_index + i) / sfreq
            phase = self.phase_offset + self.stream_index * 0.9
            sample = [
                40.0 * math.sin(2 * math.pi * base_freq * t + phase + ch * 0.1)
                for ch in range(4)
            ]
            rows.append(sample)
            timestamps.append(self._clock + t)
        self._sample_index += n
        self._clock = time.time()
        return rows, timestamps

    def close_stream(self) -> None:
        self._stopped = True

    def set_stale(self, stale: bool = True) -> None:
        self._stale_mode = stale


def make_synthetic_discovery(count: int) -> list[SyntheticStreamInfo]:
    """Create N distinct Muse-named EEG stream infos."""
    streams: list[SyntheticStreamInfo] = []
    for i in range(count):
        streams.append(
            SyntheticStreamInfo(
                name=f"MuseS-DEMO-{i + 1:02d}",
                source_id=f"demo-headset-{i + 1}",
            )
        )
    return streams


def local_clock() -> float:
    """Stand-in for pylsl.local_clock in tests."""
    return time.time()
