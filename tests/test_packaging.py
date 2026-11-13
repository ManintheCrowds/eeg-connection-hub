"""Schemas must be loadable from package data (no repo-root paths)."""

from __future__ import annotations

from eeg_connection_hub import schemas


def test_schemas_are_package_data() -> None:
    root = schemas.SCHEMA_DIR
    assert root.is_dir()
    names = sorted(p.name for p in root.iterdir() if p.name.endswith(".json"))
    assert names == [schemas.FEATURES_SCHEMA, schemas.ENVELOPE_SCHEMA]


def test_envelope_ref_resolves_via_registry() -> None:
    bad = schemas.build_envelope(
        stream_id="s", session_id="x", session_label="participant-001", features={"schema": 1}
    )
    try:
        schemas.validate_envelope(bad)
    except Exception as exc:  # jsonschema.ValidationError from the $ref'd features schema
        assert "ValidationError" in type(exc).__name__
    else:
        raise AssertionError("invalid nested features should fail via $ref")
