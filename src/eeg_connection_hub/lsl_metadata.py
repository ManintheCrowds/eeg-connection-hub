# PURPOSE: Normalize pylsl/mne-lsl stream metadata for discovery and mapping.
# DEPENDENCIES: none
# MODIFICATION NOTES: Generalized from local-proto BMI helpers; hub-local copy.
"""Lightweight LSL metadata adapters."""

from __future__ import annotations

from typing import Any


def metadata_value(stream_info: object, *names: str) -> Any:
    """Return the first available method or property value."""
    for name in names:
        if not hasattr(stream_info, name):
            continue
        value = getattr(stream_info, name)
        try:
            return value() if callable(value) else value
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return None


def stream_name(stream_info: object) -> str:
    """Return a stream name from pylsl or mne-lsl metadata."""
    return str(metadata_value(stream_info, "name") or "")


def stream_type(stream_info: object) -> str:
    """Return a stream type from pylsl or mne-lsl metadata."""
    return str(metadata_value(stream_info, "stype", "type") or "")


def stream_source_id(stream_info: object) -> str:
    """Return LSL source id when present."""
    return str(metadata_value(stream_info, "source_id") or "")


def stream_sample_rate(stream_info: object) -> float:
    """Return the advertised nominal sample rate."""
    return float(
        metadata_value(stream_info, "sfreq", "nominal_srate", "sample_rate") or 0.0
    )


def stream_channel_count(stream_info: object) -> int:
    """Return the advertised channel count."""
    return int(metadata_value(stream_info, "n_channels", "channel_count") or 0)


def pylsl_channel_metadata(
    stream_info: object,
    count: int,
) -> tuple[list[str], list[str]]:
    """Read legacy pylsl XML channel labels and units when present."""
    names: list[str] = []
    units: list[str] = []
    try:
        channel = stream_info.desc().child("channels").child("channel")  # type: ignore[attr-defined]
        for _ in range(count):
            names.append(str(channel.child_value("label") or "").strip())
            units.append(str(channel.child_value("unit") or "").strip())
            channel = channel.next_sibling()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return [], []
    return names, units


def stream_channel_names(stream_info: object) -> list[str]:
    """Return channel labels without inventing missing metadata."""
    count = stream_channel_count(stream_info)
    values = metadata_value(stream_info, "get_channel_names")
    if values is None:
        names, _ = pylsl_channel_metadata(stream_info, count)
        return names
    return [str(value).strip() for value in values]
