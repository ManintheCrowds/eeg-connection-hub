# PURPOSE: Multi-stream Muse feature hub — HTTP catalog/health + WebSocket fan-out.
# DEPENDENCIES: asyncio, json, websockets, config, features, registry, schemas
"""Core hub server implementation."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import numpy as np

from eeg_connection_hub.auth import token_required, validate_token
from eeg_connection_hub.config import HubConfig
from eeg_connection_hub.features import features_from_buffer, synth_features, warming_features
from eeg_connection_hub.lsl_discovery import DiscoveredStream, discover_muse_eeg_streams
from eeg_connection_hub.schemas import build_envelope, validate_envelope
from eeg_connection_hub.stream_mapping import (
    LiveStreamConfig,
    build_channel_mapping,
    next_resolve_delay,
    validate_lsl_chunk_timestamps,
)
from eeg_connection_hub.stream_registry import StreamRegistry
from eeg_connection_hub.synthetic import SyntheticInlet, make_synthetic_discovery

logger = logging.getLogger(__name__)


def _ws_path_and_query(websocket: Any) -> tuple[str, dict[str, list[str]]]:
    raw_path = ""
    if hasattr(websocket, "request") and hasattr(websocket.request, "path"):
        raw_path = websocket.request.path
    elif hasattr(websocket, "path"):
        raw_path = websocket.path
    parsed = urlparse(raw_path)
    return parsed.path, parse_qs(parsed.query)


@dataclass(eq=False)
class Subscriber:
    """One WebSocket client with optional stream filter."""

    websocket: Any
    stream_filter: set[str] | None = None  # None = all streams


@dataclass
class StreamWorker:
    """Per-stream ingestion and latest envelope."""

    stream_id: str
    lsl_name: str
    inlet: Any | None = None
    config: LiveStreamConfig | None = None
    buf: deque[np.ndarray] = field(default_factory=deque)
    latest: dict[str, Any] = field(default_factory=dict)
    last_sample_at: float = 0.0
    resolve_attempt: int = 0
    next_resolve_at: float = 0.0


class EEGConnectionHub:
    """LAN-first broker for multiple Muse LSL streams and WS subscribers."""

    def __init__(
        self,
        config: HubConfig,
        *,
        resolve_streams: Callable[[], list[object]] | None = None,
        local_clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.registry = StreamRegistry(label_prefix=config.session_label_prefix)
        self.subscribers: list[Subscriber] = []
        self.workers: dict[str, StreamWorker] = {}
        self._started_at = time.time()
        self._resolve_streams = resolve_streams
        self._local_clock = local_clock or time.time
        self._t0 = time.time()

    def health_payload(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "uptime_sec": round(time.time() - self._started_at, 2),
            "streams_active": len(self.workers),
            "subscribers": len(self.subscribers),
            "bind": self.config.host,
            "allow_lan": self.config.allow_lan,
            "raw_eeg_enabled": self.config.enable_raw_eeg,
            "auth_required": token_required(self.config),
        }

    def catalog_payload(self) -> dict[str, Any]:
        return {"streams": self.registry.catalog()}

    def _attach_raw_if_enabled(self, features: dict[str, Any], buf: np.ndarray | None) -> dict[str, Any]:
        if not self.config.enable_raw_eeg or buf is None or buf.size == 0:
            return features
        # Loopback-only raw export; never log samples.
        clipped = np.clip(buf[-min(8, buf.shape[0]) :, :], -250.0, 250.0)
        out = dict(features)
        out["raw_eeg"] = clipped.round(4).tolist()
        return out

    def _wrap(self, stream_id: str, features: dict[str, Any]) -> dict[str, Any]:
        state = self.registry.get(stream_id)
        if state is None:
            raise KeyError(stream_id)
        envelope = build_envelope(
            stream_id=stream_id,
            session_id=state.session_id,
            session_label=state.session_label,
            features=features,
        )
        validate_envelope(envelope, allow_raw=self.config.enable_raw_eeg)
        return envelope

    async def _ensure_workers_from_discovery(self, discovered: list[DiscoveredStream]) -> None:
        active_ids = {d.stream_id for d in discovered}
        for item in discovered:
            self.registry.register(item.stream_id, item.lsl_name)
            if item.stream_id not in self.workers:
                self.workers[item.stream_id] = StreamWorker(
                    stream_id=item.stream_id,
                    lsl_name=item.lsl_name,
                    latest=self._wrap(
                        item.stream_id,
                        warming_features(message="collecting live samples"),
                    ),
                )
        for sid in list(self.workers):
            if sid not in active_ids and self.config.synth_streams <= 0:
                self.registry.mark_stale(sid, "LSL stream removed from network")
                self.workers[sid].latest = self._wrap(
                    sid,
                    warming_features(status="error", message="stream offline"),
                )

    async def _init_synthetic_workers(self) -> None:
        infos = make_synthetic_discovery(self.config.synth_streams)
        discovered = [
            DiscoveredStream(
                stream_id=f"muse-demo-headset-{i + 1}",
                lsl_name=info.name,
                lsl_type="EEG",
                source_id=info.source_id,
                stream_info=info,
            )
            for i, info in enumerate(infos)
        ]
        await self._ensure_workers_from_discovery(discovered)
        for i, item in enumerate(discovered):
            worker = self.workers[item.stream_id]
            worker.inlet = SyntheticInlet(item.stream_info, stream_index=i)
            worker.config = build_channel_mapping(item.stream_info)
            worker.last_sample_at = time.monotonic()

    async def _resolve_live_streams(self, pylsl: Any) -> None:
        if self._resolve_streams is not None:
            streams = await asyncio.to_thread(self._resolve_streams)
        else:
            streams = await asyncio.to_thread(pylsl.resolve_streams, wait_time=1.0)
        discovered = discover_muse_eeg_streams(list(streams))
        await self._ensure_workers_from_discovery(discovered)
        now = time.monotonic()
        for item in discovered:
            worker = self.workers[item.stream_id]
            if worker.inlet is None and now >= worker.next_resolve_at:
                try:
                    cfg = build_channel_mapping(item.stream_info)
                    worker.inlet = pylsl.StreamInlet(item.stream_info)
                    worker.config = cfg
                    worker.buf.clear()
                    worker.last_sample_at = now
                    worker.resolve_attempt = 0
                    worker.latest = self._wrap(
                        item.stream_id,
                        warming_features(
                            required_samples=max(32, int(cfg.sample_rate * 0.25)),
                            message="collecting live samples",
                        ),
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    worker.resolve_attempt += 1
                    worker.next_resolve_at = now + next_resolve_delay(worker.resolve_attempt - 1)
                    worker.latest = self._wrap(
                        item.stream_id,
                        warming_features(status="error", message=str(exc)[:160]),
                    )

    async def _poll_worker(self, worker: StreamWorker, *, synth: bool, stream_index: int) -> None:
        now = time.monotonic()
        if synth:
            t = time.time() - self._t0
            features = synth_features(t, stream_index=stream_index)
            worker.latest = self._wrap(worker.stream_id, features)
            self.registry.update_frame(
                worker.stream_id,
                status=features["quality"]["status"],
                frame_ts=features["ts"],
            )
            return

        if worker.inlet is None or worker.config is None:
            return

        cfg = worker.config
        max_samples = max(1, int(math.ceil(cfg.sample_rate * self.config.window_sec)))
        required_samples = max(32, int(math.ceil(cfg.sample_rate * 0.25)))

        try:
            chunk, timestamps = worker.inlet.pull_chunk(timeout=0.0, max_samples=64)
            if chunk:
                arr = np.asarray(chunk, dtype=np.float64)
                if arr.ndim == 1:
                    arr = arr.reshape(1, -1)
                if arr.shape[1] != cfg.channel_count:
                    raise ValueError("LSL chunk width changed after connection")
                clock_fn = getattr(worker.inlet, "local_clock", None) or self._local_clock
                validate_lsl_chunk_timestamps(
                    list(timestamps),
                    sample_count=arr.shape[0],
                    now=float(clock_fn() if callable(clock_fn) else self._local_clock()),
                )
                mapped = arr[:, cfg.indices]
                for row in mapped:
                    worker.buf.append(row)
                while len(worker.buf) > max_samples:
                    worker.buf.popleft()
                worker.last_sample_at = now
            elif now - worker.last_sample_at > max(2.0, self.config.window_sec):
                raise RuntimeError("LSL stream stopped delivering samples")

            if len(worker.buf) >= required_samples:
                stacked = np.vstack(worker.buf)
                features = features_from_buffer(stacked, cfg.sample_rate)
                features["quality"]["required_samples"] = required_samples
                features = self._attach_raw_if_enabled(features, stacked)
                worker.latest = self._wrap(worker.stream_id, features)
                self.registry.update_frame(
                    worker.stream_id,
                    status=features["quality"]["status"],
                    frame_ts=features["ts"],
                    message=features["quality"]["message"],
                )
            else:
                warming = warming_features(
                    sample_count=len(worker.buf),
                    required_samples=required_samples,
                    message="collecting live samples",
                )
                worker.latest = self._wrap(worker.stream_id, warming)
                self.registry.update_frame(
                    worker.stream_id,
                    status=warming["quality"]["status"],
                    frame_ts=warming["ts"],
                )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            try:
                worker.inlet.close_stream()
            except (AttributeError, RuntimeError):
                pass
            worker.inlet = None
            worker.config = None
            worker.buf.clear()
            worker.resolve_attempt += 1
            worker.next_resolve_at = now + next_resolve_delay(worker.resolve_attempt - 1)
            err = warming_features(status="error", message=str(exc)[:160])
            worker.latest = self._wrap(worker.stream_id, err)
            self.registry.mark_stale(worker.stream_id, str(exc)[:160])

    async def broadcast_loop(self) -> None:
        pylsl = None
        synth_count = self.config.synth_streams
        synth = synth_count > 0
        if not synth:
            try:
                pylsl = importlib.import_module("pylsl")
            except ImportError:
                logger.warning("pylsl not installed; falling back to synthetic mode")
                synth = True
                synth_count = max(1, synth_count)

        if synth:
            if self.config.synth_streams != synth_count:
                # Ephemeral fallback when pylsl absent; does not mutate frozen config.
                infos = make_synthetic_discovery(synth_count)
                discovered = [
                    DiscoveredStream(
                        stream_id=f"muse-demo-headset-{i + 1}",
                        lsl_name=info.name,
                        lsl_type="EEG",
                        source_id=info.source_id,
                        stream_info=info,
                    )
                    for i, info in enumerate(infos)
                ]
                await self._ensure_workers_from_discovery(discovered)
                for i, item in enumerate(discovered):
                    worker = self.workers[item.stream_id]
                    worker.inlet = SyntheticInlet(item.stream_info, stream_index=i)
                    worker.config = build_channel_mapping(item.stream_info)
                    worker.last_sample_at = time.monotonic()
            else:
                await self._init_synthetic_workers()

        period = 1.0 / self.config.feature_hz
        stream_order = sorted(self.workers.keys())

        while True:
            loop_start = time.time()
            if not synth and pylsl is not None:
                await self._resolve_live_streams(pylsl)

            for idx, sid in enumerate(stream_order):
                worker = self.workers.get(sid)
                if worker is None:
                    continue
                await self._poll_worker(worker, synth=synth, stream_index=idx)

            stream_order = sorted(self.workers.keys())
            await self._fanout()
            elapsed = time.time() - loop_start
            await asyncio.sleep(max(0.0, period - elapsed))

    async def _fanout(self) -> None:
        dead: list[Subscriber] = []
        for sub in list(self.subscribers):
            targets = self._messages_for(sub)
            for msg in targets:
                try:
                    await sub.websocket.send(msg)
                except Exception:
                    dead.append(sub)
                    break
        for sub in dead:
            if sub in self.subscribers:
                self.subscribers.remove(sub)

    def _messages_for(self, sub: Subscriber) -> list[str]:
        messages: list[str] = []
        for sid, worker in sorted(self.workers.items()):
            if sub.stream_filter is not None and sid not in sub.stream_filter:
                continue
            messages.append(json.dumps(worker.latest))
        return messages

    async def ws_handler(self, websocket: Any) -> None:
        _, query = _ws_path_and_query(websocket)
        token = (query.get("token") or [None])[0]
        if not validate_token(self.config, token):
            await websocket.close(code=4401, reason="unauthorized")
            return

        stream_param = (query.get("stream_id") or query.get("streams") or [None])[0]
        stream_filter: set[str] | None = None
        if stream_param:
            stream_filter = {part.strip() for part in stream_param.split(",") if part.strip()}

        sub = Subscriber(websocket=websocket, stream_filter=stream_filter)
        self.subscribers.append(sub)
        try:
            for msg in self._messages_for(sub):
                await websocket.send(msg)
            await websocket.wait_closed()
        finally:
            if sub in self.subscribers:
                self.subscribers.remove(sub)

    async def http_handler(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = (await reader.readline()).decode("utf-8", errors="ignore").strip()
            if not request_line:
                writer.close()
                return
            parts = request_line.split()
            method = parts[0] if parts else "GET"
            path = parts[1] if len(parts) > 1 else "/"
            # Drain headers
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break

            parsed = urlparse(path)
            query = parse_qs(parsed.query)
            token = (query.get("token") or [None])[0]
            if not validate_token(self.config, token):
                body = json.dumps({"error": "unauthorized"}).encode("utf-8")
                writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\n")
                writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
                writer.write(body)
                await writer.drain()
                writer.close()
                return

            if method != "GET":
                body = json.dumps({"error": "method not allowed"}).encode("utf-8")
                writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Type: application/json\r\n")
                writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
                writer.write(body)
                await writer.drain()
                writer.close()
                return

            route = parsed.path.rstrip("/") or "/"
            if route == "/health":
                payload = self.health_payload()
            elif route == "/streams":
                payload = self.catalog_payload()
            else:
                payload = {"error": "not found"}
                body = json.dumps(payload).encode("utf-8")
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\n")
                writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
                writer.write(body)
                await writer.drain()
                writer.close()
                return

            body = json.dumps(payload).encode("utf-8")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n")
            writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
            writer.write(body)
            await writer.drain()
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def run(self) -> None:
        try:
            serve = importlib.import_module("websockets.asyncio.server").serve
        except ImportError:
            serve = importlib.import_module("websockets.server").serve

        http_server = await asyncio.start_server(
            self.http_handler,
            self.config.host,
            self.config.http_port,
        )
        async with serve(self.ws_handler, self.config.host, self.config.ws_port):
            async with http_server:
                logger.info(
                    "eeg-connection-hub ws://%s:%s http://%s:%s synth=%s",
                    self.config.host,
                    self.config.ws_port,
                    self.config.host,
                    self.config.http_port,
                    self.config.synth_streams,
                )
                await self.broadcast_loop()
