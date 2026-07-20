# PURPOSE: JSON Schema validation for enthea features and hub envelope.
# DEPENDENCIES: jsonschema, pathlib
"""Protocol schema loading and validation."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / "schemas"


class QualityDict(TypedDict):
    status: str
    sample_count: int
    required_samples: int
    message: str


class EntheaFeaturesV1(TypedDict, total=False):
    schema: str
    ts: float
    source: str
    artifact: bool
    drive_safe: bool
    quality: QualityDict
    bands: dict[str, float]
    controls: dict[str, float]
    raw_eeg: list[list[float]]


class HubEnvelopeV1(TypedDict):
    hub_schema: str
    stream_id: str
    session_id: str
    session_label: str
    features: EntheaFeaturesV1


@lru_cache(maxsize=4)
def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"schema not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(data)
    return data


def _validator_for(name: str) -> jsonschema.Draft202012Validator:
    schema = load_schema(name)
    if name == "hub.envelope.v1.schema.json":
        store = {
            "enthea.muse.features.v1.schema.json": load_schema(
                "enthea.muse.features.v1.schema.json"
            ),
        }
        resolver = jsonschema.RefResolver.from_schema(schema, store=store)
        return jsonschema.Draft202012Validator(schema, resolver=resolver)
    return jsonschema.Draft202012Validator(schema)


def validate_features(frame: dict[str, Any], *, allow_raw: bool = False) -> None:
    if not allow_raw and "raw_eeg" in frame:
        raise jsonschema.ValidationError("raw_eeg not permitted in default mode")
    _validator_for("enthea.muse.features.v1.schema.json").validate(frame)


def validate_envelope(envelope: dict[str, Any], *, allow_raw: bool = False) -> None:
    _validator_for("hub.envelope.v1.schema.json").validate(envelope)
    validate_features(envelope["features"], allow_raw=allow_raw)


def build_envelope(
    *,
    stream_id: str,
    session_id: str,
    session_label: str,
    features: dict[str, Any],
) -> dict[str, Any]:
    """Wrap an enthea.muse.features/v1 frame in the hub envelope."""
    return {
        "hub_schema": "eeg-connection-hub.envelope/v1",
        "stream_id": stream_id,
        "session_id": session_id,
        "session_label": session_label,
        "features": features,
    }
