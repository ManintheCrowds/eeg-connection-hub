"""Tiny WS client for the Friday 3 smoke: connect with a given Origin, print outcome. Synthetic data only."""

import asyncio
import json
import sys

import websockets
from websockets.exceptions import InvalidStatus


async def main(uri: str, origin: str) -> int:
    try:
        async with websockets.connect(uri, origin=origin, open_timeout=3) as ws:
            for _ in range(3):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                print(f"FRAME stream_id={msg['stream_id']} schema={msg['hub_schema']}")
        return 0
    except InvalidStatus as exc:
        body = exc.response.body.decode(errors="replace").strip()
        print(f"REJECTED HTTP {exc.response.status_code} body={body!r}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1], sys.argv[2])))
