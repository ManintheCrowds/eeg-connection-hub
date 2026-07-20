# PURPOSE: Unit tests for feature math and schema validation.
from __future__ import annotations

import json
import math
import unittest

import jsonschema
import numpy as np

from eeg_connection_hub.features import (
    EEG_CHANNELS,
    features_from_buffer,
    synth_features,
    warming_features,
)
from eeg_connection_hub.lsl_discovery import discover_muse_eeg_streams, is_muse_eeg_stream
from eeg_connection_hub.schemas import build_envelope, load_schema, validate_envelope, validate_features
from eeg_connection_hub.stream_mapping import build_channel_mapping, validate_lsl_chunk_timestamps
from eeg_connection_hub.synthetic import SyntheticStreamInfo


class _PropertyStream:
    name = "MuseS-6F8C"
    stype = "EEG"
    sfreq = 256.0
    n_channels = 4

    def get_channel_names(self) -> list[str]:
        return ["AF8", "TP10", "TP9", "AF7"]


class TestFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.features_schema = load_schema("enthea.muse.features.v1.schema.json")
        cls.envelope_schema = load_schema("hub.envelope.v1.schema.json")

    def test_synth_validates(self) -> None:
        frame = synth_features(0.0, stream_index=1)
        validate_features(frame)
        self.assertEqual(frame["schema"], "enthea.muse.features/v1")
        self.assertTrue(frame["drive_safe"])

    def test_alpha_sine_elevates_alpha(self) -> None:
        sfreq = 256.0
        t = np.arange(int(sfreq * 2.0)) / sfreq
        sig = 50.0 * np.sin(2 * math.pi * 10.0 * t)
        buf = np.column_stack([sig, sig, sig, sig])
        feat = features_from_buffer(buf, sfreq)
        self.assertGreater(feat["bands"]["alpha"], feat["bands"]["beta"])
        validate_features(feat)

    def test_artifact_not_drive_safe(self) -> None:
        artifact = features_from_buffer(np.full((128, 4), 500.0), 256.0)
        self.assertTrue(artifact["artifact"])
        self.assertFalse(artifact["drive_safe"])
        validate_features(artifact)

    def test_envelope_wraps_features(self) -> None:
        features = synth_features(1.0)
        envelope = build_envelope(
            stream_id="muse-demo-1",
            session_id="abcd1234ef567890",
            session_label="participant-001",
            features=features,
        )
        validate_envelope(envelope)
        self.assertEqual(envelope["hub_schema"], "eeg-connection-hub.envelope/v1")

    def test_raw_eeg_rejected_by_default(self) -> None:
        frame = synth_features(0.0)
        frame["raw_eeg"] = [[1.0, 2.0, 3.0, 4.0]]
        with self.assertRaises(jsonschema.ValidationError):
            validate_features(frame, allow_raw=False)

    def test_multi_stream_discovery(self) -> None:
        streams = [
            SyntheticStreamInfo(name="MuseS-A", source_id="headset-a"),
            SyntheticStreamInfo(name="MuseS-B", source_id="headset-b"),
            SyntheticStreamInfo(name="Markers", stype="Markers"),
        ]
        found = discover_muse_eeg_streams(streams)
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0].stream_id, found[0].stream_id)
        self.assertNotEqual(found[0].stream_id, found[1].stream_id)

    def test_channel_mapping(self) -> None:
        cfg = build_channel_mapping(_PropertyStream())
        self.assertEqual(cfg.sample_rate, 256.0)
        self.assertEqual(cfg.indices, (2, 3, 0, 1))

    def test_timestamp_validation(self) -> None:
        self.assertEqual(
            validate_lsl_chunk_timestamps([99.9, 100.0], sample_count=2, now=100.0),
            100.0,
        )
        with self.assertRaises(ValueError):
            validate_lsl_chunk_timestamps([98.0], sample_count=1, now=100.0)

    def test_warming_frame(self) -> None:
        frame = warming_features(sample_count=5, required_samples=64)
        self.assertEqual(frame["quality"]["status"], "warming")
        validate_features(frame)

    def test_muse_eeg_heuristic(self) -> None:
        eeg = SyntheticStreamInfo(name="MuseS-X", stype="EEG")
        markers = SyntheticStreamInfo(name="MuseS-X", stype="Markers")
        self.assertTrue(is_muse_eeg_stream(eeg))
        self.assertFalse(is_muse_eeg_stream(markers))


if __name__ == "__main__":
    raise SystemExit(unittest.main())
