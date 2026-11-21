# PURPOSE: CLI entry point for eeg-connection-hub.
# DEPENDENCIES: argparse, asyncio, hub_server, config
"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from eeg_connection_hub.config import HubConfig, is_loopback_host
from eeg_connection_hub.hub_server import EEGConnectionHub
from eeg_connection_hub.security import install_log_redaction

TOKEN_ENV = "EEG_HUB_ACCESS_TOKEN"
# Operator-supplied tokens (flag or env). secrets.token_urlsafe(32) is 43
# characters, so the auto-generated token is already above this floor.
MIN_ACCESS_TOKEN_LENGTH = 32


def supplied_token_error(token: str) -> str | None:
    """Return a startup error when an operator-supplied token is too short.

    An empty string means the operator did not supply one. LAN mode then
    auto-generates a token that already meets the floor.
    """
    if not token:
        return None
    if len(token) < MIN_ACCESS_TOKEN_LENGTH:
        return (
            f"access token must be at least {MIN_ACCESS_TOKEN_LENGTH} characters "
            f"(got {len(token)}). Set --access-token or ${TOKEN_ENV} to a long "
            "random value. The auto-generated token already meets this floor."
        )
    return None


def generated_token_path() -> Path:
    """Path of the 0600 file that holds an auto-generated LAN token.

    ``$XDG_STATE_HOME`` when set, otherwise ``~/.local/state``. Service managers
    capture stderr (journald, Docker, CI), so the token itself is not printed.
    """
    raw = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(raw).expanduser() if raw else Path.home() / ".local" / "state"
    return base / "eeg-connection-hub" / "access-token"


def write_generated_token(token: str) -> Path:
    """Write ``token`` to the state file (directory 0700, file 0600) and return the path."""
    path = generated_token_path()
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, token.encode("utf-8") + b"\n")
    except OSError:
        os.close(fd)
        os.unlink(temporary)
        raise
    os.close(fd)
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LAN-first multi-stream Muse EEG feature broker",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (loopback default); non-loopback requires --allow-lan",
    )
    parser.add_argument("--ws-port", type=int, default=8765)
    parser.add_argument("--http-port", type=int, default=8766)
    parser.add_argument("--hz", type=float, default=10.0, dest="feature_hz")
    parser.add_argument("--window-sec", type=float, default=2.0)
    parser.add_argument(
        "--allow-lan",
        action="store_true",
        help="Bind 0.0.0.0 (or --host) and require access token for clients",
    )
    parser.add_argument(
        "--access-token",
        default=os.environ.get(TOKEN_ENV, ""),
        help=(
            f"LAN access token (default: ${TOKEN_ENV}; minimum {MIN_ACCESS_TOKEN_LENGTH} "
            "characters; auto-generated when --allow-lan and omitted)"
        ),
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        dest="allow_origins",
        metavar="ORIGIN",
        help="Extra browser Origin allowed to connect, e.g. https://app.example (repeatable). "
        "No-Origin clients and http(s)://localhost|127.0.0.1|[::1] are always allowed.",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        dest="allow_hosts",
        metavar="NAME",
        help="Extra Host header name accepted (DNS-rebinding guard), e.g. labpc.local (repeatable)",
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

    host = args.host
    if args.allow_lan and is_loopback_host(host):
        host = "0.0.0.0"
    if not args.allow_lan and not is_loopback_host(host):
        print(
            f"Config error: --host {host} is not a loopback address; refusing to expose the hub "
            "without --allow-lan (which also enforces an access token). "
            "Use the default --host 127.0.0.1 for local-only use.",
            file=sys.stderr,
        )
        return 2
    token_supplied = bool(args.access_token)
    strength_error = supplied_token_error(args.access_token)
    if strength_error:
        print(f"Config error: {strength_error}", file=sys.stderr)
        return 2
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
            allow_origins=tuple(args.allow_origins),
            allow_hosts=tuple(args.allow_hosts),
        )
    except ValueError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    install_log_redaction([config.access_token])
    if config.allow_lan and config.access_token and not token_supplied:
        # stderr is the journald/Docker/CI log. Never print the token; the operator
        # reads the 0600 file. A token supplied via the flag or the env var is not written.
        try:
            token_path = write_generated_token(config.access_token)
        except OSError as exc:
            print(
                "Config error: could not store the auto-generated LAN access token "
                f"({exc.__class__.__name__}: {exc}). The token was not printed.",
                file=sys.stderr,
            )
            return 2
        print(f"LAN access token written to {token_path} (mode 0600).", file=sys.stderr)
        print(
            "Clients: send header 'Authorization: Bearer <token>' using the value in that file.",
            file=sys.stderr,
        )

    hub = EEGConnectionHub(config)
    try:
        asyncio.run(hub.run())
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
