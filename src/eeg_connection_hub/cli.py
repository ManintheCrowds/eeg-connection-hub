# PURPOSE: CLI entry point for eeg-connection-hub.
# DEPENDENCIES: argparse, asyncio, hub_server, config
"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from eeg_connection_hub.config import HubConfig
from eeg_connection_hub.hub_server import EEGConnectionHub


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LAN-first multi-stream Muse EEG feature broker",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (loopback default)")
    parser.add_argument("--ws-port", type=int, default=8765)
    parser.add_argument("--http-port", type=int, default=8766)
    parser.add_argument("--hz", type=float, default=10.0, dest="feature_hz")
    parser.add_argument("--window-sec", type=float, default=2.0)
    parser.add_argument(
        "--allow-lan",
        action="store_true",
        help="Bind 0.0.0.0 and require access token for clients",
    )
    parser.add_argument(
        "--access-token",
        default="",
        help="LAN access token (auto-generated when --allow-lan and omitted)",
    )
    parser.add_argument(
        "--enable-raw-eeg",
        action="store_true",
        help="Attach clipped raw EEG window (loopback only; never logged)",
    )
    parser.add_argument(
        "--synth",
        type=int,
        default=0,
        metavar="N",
        help="Use N synthetic Muse streams instead of LSL",
    )
    parser.add_argument(
        "--session-prefix",
        default="participant",
        dest="session_label_prefix",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    host = "0.0.0.0" if args.allow_lan else args.host
    try:
        config = HubConfig(
            host=host,
            ws_port=args.ws_port,
            http_port=args.http_port,
            feature_hz=args.feature_hz,
            window_sec=args.window_sec,
            allow_lan=args.allow_lan,
            access_token=args.access_token,
            enable_raw_eeg=args.enable_raw_eeg,
            synth_streams=args.synth,
            session_label_prefix=args.session_label_prefix,
        )
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if config.allow_lan and config.access_token:
        print(f"LAN access token: {config.access_token}", file=sys.stderr)

    hub = EEGConnectionHub(config)
    try:
        asyncio.run(hub.run())
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
