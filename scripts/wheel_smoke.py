# PURPOSE: Run from OUTSIDE the repo against an installed wheel; proves schemas ship as package data.
"""Validate one synthetic envelope using only the installed package."""

from __future__ import annotations

import eeg_connection_hub
from eeg_connection_hub.features import synth_features
from eeg_connection_hub.schemas import SCHEMA_DIR, build_envelope, validate_envelope


def main() -> None:
    env = build_envelope(
        stream_id="muse-synth-1",
        session_id="0123456789abcdef",
        session_label="participant-001",
        features=synth_features(0.0, stream_index=1),
    )
    validate_envelope(env)
    print(f"package: {eeg_connection_hub.__file__}")
    print(f"schemas: {SCHEMA_DIR}")
    print("wheel smoke OK: synthetic envelope validated from installed package data")


if __name__ == "__main__":
    main()
