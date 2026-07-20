# PURPOSE: Muse EEG bandpower feature extraction compatible with enthea.muse.features/v1.
# DEPENDENCIES: numpy
"""Feature frames for Muse EEG (no raw samples in default output)."""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

EEG_CHANNELS = ("TP9", "AF7", "AF8", "TP10")
BANDS = {
    "delta": (1.0, 4.0),
    "theta": (4.0, 8.0),
    "alpha": (8.0, 12.0),
    "beta": (12.0, 30.0),
}


def welch_bandpower(x: np.ndarray, sfreq: float, fmin: float, fmax: float) -> float:
    """Mean PSD in band via rFFT (Hann window)."""
    if x.size < 32:
        return 0.0
    window = np.hanning(x.size)
    xw = (x - np.mean(x)) * window
    spec = np.abs(np.fft.rfft(xw)) ** 2
    freqs = np.fft.rfftfreq(x.size, d=1.0 / sfreq)
    mask = (freqs >= fmin) & (freqs <= fmax)
    if not np.any(mask):
        return 0.0
    return float(np.mean(spec[mask]))


def feature_frame(
    *,
    source: str,
    status: str,
    artifact: bool,
    bands: dict[str, float],
    controls: dict[str, float],
    sample_count: int,
    required_samples: int,
    message: str = "",
    raw_eeg: list[list[float]] | None = None,
) -> dict[str, Any]:
    """Build one finite, range-bounded features frame."""
    frame: dict[str, Any] = {
        "schema": "enthea.muse.features/v1",
        "ts": time.time(),
        "source": source,
        "artifact": artifact,
        "drive_safe": status == "safe" and not artifact,
        "quality": {
            "status": status,
            "sample_count": sample_count,
            "required_samples": required_samples,
            "message": message[:160],
        },
        "bands": {name: round(float(bands[name]), 4) for name in BANDS},
        "controls": {
            "dose": round(float(controls["dose"]), 4),
            "speed": round(float(controls["speed"]), 4),
            "cplx": round(float(controls["cplx"]), 4),
            "pulse": round(float(controls["pulse"]), 4),
        },
    }
    if raw_eeg is not None:
        frame["raw_eeg"] = raw_eeg
    return frame


def features_from_buffer(buf: np.ndarray, sfreq: float) -> dict[str, Any]:
    """
    buf shape (n_samples, n_channels). Returns agent-safe scalars only unless raw requested upstream.
    """
    if buf.ndim != 2 or buf.shape[1] != len(EEG_CHANNELS):
        raise ValueError("Muse feature buffer must have four mapped EEG channels")
    if buf.size == 0:
        return warming_features(status="empty", message="no live samples")
    if not np.all(np.isfinite(buf)):
        raise ValueError("Muse feature buffer must be finite")
    per_band: dict[str, float] = {}
    for name, (lo, hi) in BANDS.items():
        vals = [welch_bandpower(buf[:, c], sfreq, lo, hi) for c in range(buf.shape[1])]
        per_band[name] = float(np.mean(vals))
    total = sum(per_band.values()) + 1e-12
    rel = {k: v / total for k, v in per_band.items()}
    alpha = rel["alpha"]
    theta = rel["theta"]
    dose = float(np.clip(0.25 + alpha * 0.9, 0.0, 1.0))
    speed = float(np.clip(0.4 + rel["beta"] * 1.2, 0.0, 3.0))
    cplx = float(np.clip(0.3 + theta * 1.1, 0.0, 1.0))
    pulse = float(np.clip(alpha * 1.4, 0.0, 1.0))
    recent = buf[-min(64, buf.shape[0]) :, :]
    channel_std = np.std(recent, axis=0)
    artifact = bool(
        np.any(channel_std < 0.05)
        or np.max(channel_std) > 200.0
        or np.max(np.abs(recent)) > 250.0
    )
    return feature_frame(
        source="lsl-live",
        status="artifact" if artifact else "safe",
        artifact=artifact,
        bands=rel,
        controls={"dose": dose, "speed": speed, "cplx": cplx, "pulse": pulse},
        sample_count=buf.shape[0],
        required_samples=64,
        message="artifact or flatline threshold exceeded" if artifact else "",
    )


def warming_features(
    sample_count: int = 0,
    required_samples: int = 64,
    *,
    status: str = "warming",
    message: str = "",
) -> dict[str, Any]:
    """Return a non-driving live frame while input is unavailable or warming."""
    return feature_frame(
        source="lsl-live",
        status=status,
        artifact=False,
        bands={name: 0.0 for name in BANDS},
        controls={"dose": 0.45, "speed": 1.0, "cplx": 0.55, "pulse": 0.0},
        sample_count=sample_count,
        required_samples=required_samples,
        message=message,
    )


def synth_features(t: float, *, stream_index: int = 0) -> dict[str, Any]:
    """Deterministic breathing synth for dry-run (no headset)."""
    phase = stream_index * 1.7
    alpha = 0.35 + 0.25 * (0.5 + 0.5 * math.sin(t * 0.4 + phase))
    theta = 0.20 + 0.10 * (0.5 + 0.5 * math.sin(t * 0.15 + 1.0 + phase))
    beta = 0.15 + 0.08 * (0.5 + 0.5 * math.sin(t * 0.9 + phase))
    delta = max(0.05, 1.0 - alpha - theta - beta)
    total = alpha + theta + beta + delta
    rel = {
        "delta": delta / total,
        "theta": theta / total,
        "alpha": alpha / total,
        "beta": beta / total,
    }
    return feature_frame(
        source="synth",
        status="safe",
        artifact=False,
        bands=rel,
        controls={
            "dose": float(np.clip(0.25 + rel["alpha"] * 0.9, 0.0, 1.0)),
            "speed": float(np.clip(0.4 + rel["beta"] * 1.2, 0.0, 3.0)),
            "cplx": float(np.clip(0.3 + rel["theta"] * 1.1, 0.0, 1.0)),
            "pulse": float(np.clip(rel["alpha"] * 1.4, 0.0, 1.0)),
        },
        sample_count=1,
        required_samples=1,
        message="synthetic dry-run features",
    )
