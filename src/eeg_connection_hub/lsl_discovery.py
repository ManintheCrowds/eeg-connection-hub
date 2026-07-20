# PURPOSE: Detect Muse EEG LSL streams and discover multiple inputs.
# DEPENDENCIES: lsl_metadata
"""LSL stream discovery for BlueMuse-published Muse EEG."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from eeg_connection_hub.lsl_metadata import (
    stream_name,
    stream_source_id,
    stream_type,
)


def is_muse_eeg_stream(stream_info: object) -> bool:
    """Return True when stream metadata looks like Muse EEG from BlueMuse."""
    name = stream_name(stream_info).lower()
    stype = stream_type(stream_info).lower()
    return "muse" in name and ("eeg" in name or "eeg" in stype)


def stable_stream_id(stream_info: object) -> str:
    """
    Derive a stable, filesystem-safe stream id from LSL metadata.

    Uses source_id when present, otherwise hashes name+type.
    """
    source = stream_source_id(stream_info).strip()
    if source:
        slug = _slugify(source)
        return f"muse-{slug[:48]}" if slug else _hash_id(stream_info)
    return _hash_id(stream_info)


def _hash_id(stream_info: object) -> str:
    key = f"{stream_name(stream_info)}|{stream_type(stream_info)}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"muse-{digest}"


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return cleaned


@dataclass(frozen=True)
class DiscoveredStream:
    """One Muse EEG stream candidate from LSL resolve."""

    stream_id: str
    lsl_name: str
    lsl_type: str
    source_id: str
    stream_info: object


def discover_muse_eeg_streams(streams: list[object]) -> list[DiscoveredStream]:
    """
    Return all Muse EEG streams (multi-stream; never rejects >1).

    Stable ordering by stream_id for deterministic catalog.
    """
    found: list[DiscoveredStream] = []
    seen: set[str] = set()
    for info in streams:
        if not is_muse_eeg_stream(info):
            continue
        sid = stable_stream_id(info)
        if sid in seen:
            suffix = hashlib.sha256(stream_name(info).encode()).hexdigest()[:6]
            sid = f"{sid}-{suffix}"
        seen.add(sid)
        found.append(
            DiscoveredStream(
                stream_id=sid,
                lsl_name=stream_name(info),
                lsl_type=stream_type(info),
                source_id=stream_source_id(info),
                stream_info=info,
            )
        )
    found.sort(key=lambda item: item.stream_id)
    return found
