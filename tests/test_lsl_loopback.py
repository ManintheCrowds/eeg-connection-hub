# PURPOSE: Live pylsl loopback — fake Muse outlets → hub → usable feature frames.
# HARDWARE: unverified; synthetic LSL outlets only (no real recordings).
from __future__ import annotations

import asyncio
import json
import math
import random
import socket
import threading
import time
import uuid
import urllib.request
from collections import Counter
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import pytest

from eeg_connection_hub.config import HubConfig
from eeg_connection_hub.hub_server import EEGConnectionHub

pylsl = pytest.importorskip("pylsl", reason="pylsl not installed")


def _liblsl_available() -> bool:
    try:
        pylsl.local_clock()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _liblsl_available(),
    reason="liblsl unavailable",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _FakeMuseOutlets:
    """Background thread pushing 4ch 256 Hz Muse-like LSL EEG (fake only)."""

    def __init__(self, count: int = 2, *, duration_sec: float = 25.0) -> None:
        self.count = count
        self.duration_sec = duration_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self.run_id = uuid.uuid4().hex[:8]
        self.names = [f"MuseS-LB-{self.run_id}-{k + 1:02d}" for k in range(count)]
        self.source_ids = [f"lb-{self.run_id}-{k + 1}" for k in range(count)]
        self._outlets: list[Any] = []

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="fake-muse-lsl", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("fake Muse outlets failed to start")
        if self._error is not None:
            raise RuntimeError(f"fake Muse outlet error: {self._error}") from self._error

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._outlets.clear()

    def _run(self) -> None:
        try:
            for k in range(self.count):
                info = pylsl.StreamInfo(
                    self.names[k],
                    "EEG",
                    4,
                    256,
                    "float32",
                    self.source_ids[k],
                )
                chans = info.desc().append_child("channels")
                for lab in ("TP9", "AF7", "AF8", "TP10"):
                    ch = chans.append_child("channel")
                    ch.append_child_value("label", lab)
                    ch.append_child_value("unit", "microvolts")
                self._outlets.append(pylsl.StreamOutlet(info))
            self._ready.set()
            i = 0
            t0 = time.time()
            while not self._stop.is_set() and (time.time() - t0) < self.duration_sec:
                for k, outlet in enumerate(self._outlets):
                    chunk = [
                        [
                            40 * math.sin(2 * math.pi * (10 + 2 * k) * (i + j) / 256)
                            + random.gauss(0, 3)
                            for _ in range(4)
                        ]
                        for j in range(16)
                    ]
                    outlet.push_chunk(chunk)
                i += 16
                time.sleep(16 / 256)
        except BaseException as exc:  # noqa: BLE001 — surface to starter
            self._error = exc
            self._ready.set()


@asynccontextmanager
async def running_hub(config: HubConfig) -> AsyncIterator[EEGConnectionHub]:
    hub = EEGConnectionHub(config)
    task = asyncio.create_task(hub.run())
    # Live mode: workers appear after first resolve; don't block forever here.
    for _ in range(60):
        if hub.workers:
            break
        await asyncio.sleep(0.05)
    try:
        yield hub
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_pylsl_loopback_two_streams_usable_frames() -> None:
    """Roadmap proof: real pylsl resolve + fake Muse → safe feature frames at usable rate."""
    outlets = _FakeMuseOutlets(count=2, duration_sec=30.0)
    outlets.start()
    # Confirm outlets are resolvable before starting the hub.
    found = await asyncio.to_thread(pylsl.resolve_streams, 1.0)
    names = {s.name() for s in found}
    missing = [n for n in outlets.names if n not in names]
    assert not missing, f"outlets not visible to pylsl resolve: {missing}; saw={sorted(names)[:20]}"

    ws_port = _free_port()
    http_port = _free_port()
    config = HubConfig(
        host="127.0.0.1",
        ws_port=ws_port,
        http_port=http_port,
        feature_hz=10.0,
        synth_streams=0,
        resolve_interval_sec=2.0,
        resolve_wait_sec=0.5,
    )
    try:
        async with running_hub(config) as hub:
            for _ in range(40):
                if len(hub.workers) >= 2:
                    break
                await asyncio.sleep(0.25)
            else:
                pytest.fail(
                    f"hub did not discover 2 live LSL streams in time; workers={list(hub.workers)}"
                )

            # Warm until at least one worker has a live inlet + config.
            for _ in range(40):
                ready = sum(1 for w in hub.workers.values() if w.inlet is not None and w.config is not None)
                if ready >= 2:
                    break
                await asyncio.sleep(0.25)
            else:
                detail = {
                    sid: (w.inlet is not None, w.latest.get("features", {}).get("quality", {}))
                    for sid, w in hub.workers.items()
                }
                pytest.fail(f"hub inlets not open in time: {detail}")

            # Extra warm for sample buffer → safe frames.
            await asyncio.sleep(1.5)

            import websockets

            statuses: Counter[tuple[str, str]] = Counter()
            collect_sec = 6.0
            t0 = time.monotonic()
            async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as ws:
                while time.monotonic() - t0 < collect_sec:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        continue
                    msg = json.loads(raw)
                    sid = str(msg["stream_id"])
                    status = str(msg["features"]["quality"]["status"])
                    statuses[(sid, status)] += 1

            stream_ids = {sid for sid, _ in statuses}
            assert len(stream_ids) >= 2, f"expected 2 streams, got {stream_ids} counts={dict(statuses)}"

            for sid in sorted(stream_ids):
                total = sum(c for (s, _), c in statuses.items() if s == sid)
                safe = statuses.get((sid, "safe"), 0)
                error = statuses.get((sid, "error"), 0)
                assert total > 0, f"{sid}: no frames"
                assert safe >= 5, f"{sid}: too few safe frames ({safe}/{total}); {dict(statuses)}"
                assert error / total <= 0.25, f"{sid}: too many errors ({error}/{total})"
                hz = safe / collect_sec
                assert hz >= 4.0, f"{sid}: safe rate {hz:.2f} Hz too low (want ≥4 in short test)"
    finally:
        outlets.stop()
