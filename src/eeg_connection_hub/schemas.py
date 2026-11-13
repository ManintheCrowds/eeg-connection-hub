# PURPOSE: JSON Schema validation for enthea features and hub envelope.
# DEPENDENCIES: jsonschema, referencing, importlib.resources
"""Protocol schema loading and validation.

Schemas ship inside the package (``eeg_connection_hub/schema_files``) so a
normal, non-editable wheel install can validate frames.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any, TypedDict

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_DIR = files("eeg_connection_hub") / "schema_files"
FEATURES_SCHEMA = "enthea.muse.features.v1.schema.json"
ENVELOPE_SCHEMA = "hub.envelope.v1.schema.json"


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


@lru_cache(maxsize=1)
def _registry() -> Registry:
    """Registry of bundled schemas keyed by ``$id`` (replaces RefResolver)."""
    resources = [
        (name, Resource.from_contents(load_schema(name), default_specification=DRAFT202012))
        for name in (FEATURES_SCHEMA, ENVELOPE_SCHEMA)
    ]
    return Registry().with_resources(resources)


def _validator_for(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(load_schema(name), registry=_registry())


def validate_features(frame: dict[str, Any], *, allow_raw: bool = False) -> None:
    if not allow_raw and "raw_eeg" in frame:
        raise jsonschema.ValidationError("raw_eeg not permitted in default mode")
    _validator_for(FEATURES_SCHEMA).validate(frame)


def validate_envelope(envelope: dict[str, Any], *, allow_raw: bool = False) -> None:
    _validator_for(ENVELOPE_SCHEMA).validate(envelope)
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
