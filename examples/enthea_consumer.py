#!/usr/bin/env python3
# PURPOSE: Example WebSocket consumer for hub envelopes (ENTHEA-compatible features).
# DEPENDENCIES: websockets (stdlib asyncio)
"""Consume eeg-connection-hub feature envelopes without ENTHEA installed."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


async def consume(uri: str, stream_id: str | None, token: str = "") -> None:
    try:
        import websockets
    except ImportError:
        print("pip install websockets", file=sys.stderr)
        raise SystemExit(2)

    if stream_id:
        sep = "&" if "?" in uri else "?"
        uri = f"{uri}{sep}stream_id={stream_id}"

    print(f"Connecting to {uri}", file=sys.stderr)  # token travels in a header, never in the URL
    headers = {"Authorization": f"Bearer {token}"} if token else None
    try:
        connect = websockets.connect(uri, additional_headers=headers)
    except TypeError:  # websockets < 14 legacy client
        connect = websockets.connect(uri, extra_headers=headers)
    async with connect as ws:
        async for raw in ws:
            envelope = json.loads(raw)
            features = envelope.get("features", {})
            controls = features.get("controls", {})
            print(
                json.dumps(
                    {
                        "stream_id": envelope.get("stream_id"),
                        "session_label": envelope.get("session_label"),
                        "status": features.get("quality", {}).get("status"),
                        "dose": controls.get("dose"),
                        "drive_safe": features.get("drive_safe"),
                    }
                )
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="ENTHEA-style hub consumer example")
    parser.add_argument("--uri", default="ws://127.0.0.1:8765")
    parser.add_argument("--stream-id", default="", help="Optional stream filter")
    parser.add_argument(
        "--token",
        default=os.environ.get("EEG_HUB_ACCESS_TOKEN", ""),
        help="LAN access token when required (default: $EEG_HUB_ACCESS_TOKEN); sent as a Bearer header",
    )
    args = parser.parse_args()
    asyncio.run(consume(args.uri, args.stream_id or None, args.token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
