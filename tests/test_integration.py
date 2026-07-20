# PURPOSE: Integration tests — multi-stream hub, multi-subscriber WS, auth, stale streams.
from __future__ import annotations

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest

from eeg_connection_hub.config import HubConfig
from eeg_connection_hub.hub_server import EEGConnectionHub


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@asynccontextmanager
async def running_hub(config: HubConfig) -> AsyncIterator[EEGConnectionHub]:
    hub = EEGConnectionHub(config)
    task = asyncio.create_task(hub.run())
    # Allow servers and synthetic workers to initialize
    for _ in range(50):
        if hub.workers:
            break
        await asyncio.sleep(0.05)
    try:
        yield hub
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def _recv_envelope(ws: object) -> dict[str, object]:
    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)  # type: ignore[attr-defined]
    return json.loads(raw)


@pytest.mark.asyncio
async def test_two_streams_two_subscribers() -> None:
    ws_port = _free_port()
    http_port = _free_port()
    config = HubConfig(
        host="127.0.0.1",
        ws_port=ws_port,
        http_port=http_port,
        feature_hz=20.0,
        synth_streams=2,
    )
    async with running_hub(config):
        import websockets

        uri = f"ws://127.0.0.1:{ws_port}"
        async with websockets.connect(uri) as ws1, websockets.connect(uri) as ws2:
            frames1: set[str] = set()
            frames2: set[str] = set()
            deadline = asyncio.get_event_loop().time() + 3.0
            while len(frames1) < 2 and asyncio.get_event_loop().time() < deadline:
                for ws, acc in ((ws1, frames1), (ws2, frames2)):
                    try:
                        msg = await asyncio.wait_for(_recv_envelope(ws), timeout=1.0)
                        assert msg["hub_schema"] == "eeg-connection-hub.envelope/v1"
                        acc.add(str(msg["stream_id"]))
                    except asyncio.TimeoutError:
                        pass
                await asyncio.sleep(0.05)
            assert len(frames1) >= 2
            assert frames1 == frames2


@pytest.mark.asyncio
async def test_stream_filter_subscription() -> None:
    ws_port = _free_port()
    http_port = _free_port()
    config = HubConfig(host="127.0.0.1", ws_port=ws_port, http_port=http_port, synth_streams=2)
    async with running_hub(config):
        import websockets

        uri = f"ws://127.0.0.1:{ws_port}?stream_id=muse-demo-headset-1"
        async with websockets.connect(uri) as ws:
            seen: set[str] = set()
            for _ in range(5):
                msg = await _recv_envelope(ws)
                seen.add(str(msg["stream_id"]))
            assert seen == {"muse-demo-headset-1"}


@pytest.mark.asyncio
async def test_disconnect_reconnect() -> None:
    ws_port = _free_port()
    http_port = _free_port()
    config = HubConfig(host="127.0.0.1", ws_port=ws_port, http_port=http_port, synth_streams=1)
    async with running_hub(config):
        import websockets

        uri = f"ws://127.0.0.1:{ws_port}"
        async with websockets.connect(uri) as ws:
            await _recv_envelope(ws)
        await asyncio.sleep(0.1)
        async with websockets.connect(uri) as ws2:
            msg = await _recv_envelope(ws2)
            assert msg["features"]["schema"] == "enthea.muse.features/v1"


@pytest.mark.asyncio
async def test_stale_stream_marked_in_catalog() -> None:
    ws_port = _free_port()
    http_port = _free_port()
    config = HubConfig(host="127.0.0.1", ws_port=ws_port, http_port=http_port, synth_streams=1)
    hub = EEGConnectionHub(config)
    await hub._init_synthetic_workers()
    worker = hub.workers["muse-demo-headset-1"]
    assert worker.inlet is not None
    worker.inlet.set_stale(True)
    await hub._poll_worker(worker, synth=True, stream_index=0)
    hub.registry.mark_stale("muse-demo-headset-1", "synthetic stale")
    catalog = hub.catalog_payload()["streams"]
    assert catalog[0]["stale"] is True


@pytest.mark.asyncio
async def test_unauthorized_lan_client() -> None:
    ws_port = _free_port()
    http_port = _free_port()
    config = HubConfig(
        host="0.0.0.0",
        ws_port=ws_port,
        http_port=http_port,
        allow_lan=True,
        access_token="test-secret-token",
        synth_streams=1,
    )
    async with running_hub(config):
        import websockets

        uri = f"ws://127.0.0.1:{ws_port}"

        try:
            async with websockets.connect(uri) as ws:
                await asyncio.wait_for(ws.recv(), timeout=2.0)
            raised = False
        except (websockets.exceptions.ConnectionClosedError, asyncio.TimeoutError, OSError):
            raised = True
        assert raised, "expected unauthorized WS client to fail on recv"

        ok_uri = f"{uri}?token=test-secret-token"
        async with websockets.connect(ok_uri) as ws:
            msg = await _recv_envelope(ws)
            assert "stream_id" in msg


@pytest.mark.asyncio
async def test_http_health_and_catalog() -> None:
    ws_port = _free_port()
    http_port = _free_port()
    config = HubConfig(host="127.0.0.1", ws_port=ws_port, http_port=http_port, synth_streams=2)
    async with running_hub(config):
        reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
        writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(4096), timeout=3.0)
        writer.close()
        body = data.split(b"\r\n\r\n", 1)[1]
        health = json.loads(body)
        assert health["status"] == "ok"
        assert health["streams_active"] >= 2

        reader2, writer2 = await asyncio.open_connection("127.0.0.1", http_port)
        writer2.write(b"GET /streams HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer2.drain()
        data2 = await asyncio.wait_for(reader2.read(8192), timeout=3.0)
        writer2.close()
        catalog = json.loads(data2.split(b"\r\n\r\n", 1)[1])
        assert len(catalog["streams"]) >= 2


@pytest.mark.asyncio
async def test_raw_eeg_opt_in_loopback() -> None:
    ws_port = _free_port()
    http_port = _free_port()
    config = HubConfig(
        host="127.0.0.1",
        ws_port=ws_port,
        http_port=http_port,
        synth_streams=1,
        enable_raw_eeg=True,
    )
    hub = EEGConnectionHub(config)
    await hub._init_synthetic_workers()
    worker = hub.workers["muse-demo-headset-1"]
    from eeg_connection_hub.stream_mapping import build_channel_mapping
    from eeg_connection_hub.synthetic import SyntheticStreamInfo
    import numpy as np
    import time

    info = SyntheticStreamInfo(name="MuseS-DEMO-01", source_id="demo-headset-1")
    worker.config = build_channel_mapping(info)
    worker.last_sample_at = time.monotonic()
    t = np.linspace(0, 2, 512)
    sig = 40.0 * np.sin(2 * np.pi * 10.0 * t)
    stacked = np.column_stack([sig, sig, sig, sig])
    for row in stacked:
        worker.buf.append(row)
    await hub._poll_worker(worker, synth=False, stream_index=0)
    raw = worker.latest["features"].get("raw_eeg")
    assert raw is not None
    assert isinstance(raw, list)
