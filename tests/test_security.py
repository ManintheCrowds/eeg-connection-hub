# PURPOSE: Friday 3 privacy hardening — Origin allowlist, Host check (DNS rebinding), Bearer token,
#          log redaction, and non-loopback bind refusal. Synthetic streams only.
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import subprocess
import sys

import pytest
import websockets
from test_integration import _free_port, _recv_envelope, running_hub
from websockets.exceptions import ConnectionClosed, InvalidStatus

from eeg_connection_hub import cli
from eeg_connection_hub.config import HubConfig
from eeg_connection_hub.security import (
    RedactingFilter,
    bearer_token,
    extract_token,
    host_allowed,
    origin_allowed,
    parse_host_header,
    redact,
)

TOKEN = "unit-test-token-Zx9qP"  # synthetic test value, not a real secret


def _loopback_config(**kw: object) -> HubConfig:
    return HubConfig(host="127.0.0.1", ws_port=_free_port(), http_port=_free_port(), synth_streams=1, **kw)


def _lan_config(**kw: object) -> HubConfig:
    return HubConfig(
        host="0.0.0.0",
        ws_port=_free_port(),
        http_port=_free_port(),
        allow_lan=True,
        access_token=TOKEN,
        synth_streams=1,
        **kw,
    )


async def _raw_http(port: int, request: str) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request.encode("latin-1"))
    await writer.drain()
    data = await asyncio.wait_for(reader.read(65536), timeout=3.0)
    writer.close()
    status = int(data.split(b" ", 2)[1])
    return status, data.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in data else b""


def _get(path: str, host: str | None = "127.0.0.1", extra: str = "") -> str:
    host_line = f"Host: {host}\r\n" if host is not None else ""
    return f"GET {path} HTTP/1.1\r\n{host_line}{extra}Connection: close\r\n\r\n"


def _upgrade(host: str, origin: str | None = None) -> str:
    origin_line = f"Origin: {origin}\r\n" if origin else ""
    return (
        "GET / HTTP/1.1\r\n"
        f"Host: {host}\r\n{origin_line}"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )


# ---------------------------------------------------------------- unit: parsing / policy


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("localhost", "localhost"),
        ("LOCALHOST:8766", "localhost"),
        ("127.0.0.1:1", "127.0.0.1"),
        ("[::1]:8765", "::1"),
        ("[::1]", "::1"),
        ("evil.example", "evil.example"),
        ("::1", None),
        ("localhost:abc", None),
        ("a b", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_host_header(value: str | None, expected: str | None) -> None:
    assert parse_host_header(value) == expected


def test_host_allowed_loopback_mode() -> None:
    cfg = HubConfig()
    for ok in ("localhost", "localhost:8766", "127.0.0.1:9", "[::1]:8765"):
        assert host_allowed(cfg, ok), ok
    for bad in ("evil.example", "evil.example:8766", "192.168.1.20", "localhost.evil.example", None):
        assert not host_allowed(cfg, bad), bad


def test_host_allowed_lan_mode_and_extra_hosts() -> None:
    cfg = HubConfig(host="0.0.0.0", allow_lan=True, access_token=TOKEN, allow_hosts=("labpc.local",))
    assert host_allowed(cfg, "192.168.1.20:8766")  # IP literal: not a rebinding vector
    assert host_allowed(cfg, "[fe80::1]:8766")
    assert host_allowed(cfg, "labpc.local:8766")
    assert host_allowed(cfg, "localhost")
    assert not host_allowed(cfg, "evil.example")
    bound = HubConfig(host="192.168.1.20", allow_lan=True, access_token=TOKEN)
    assert host_allowed(bound, "192.168.1.20:8765")


def test_origin_policy() -> None:
    cfg = HubConfig()
    assert origin_allowed(cfg, None)
    for ok in ("http://localhost", "http://localhost:3000", "http://127.0.0.1:5173", "http://[::1]:8080"):
        assert origin_allowed(cfg, ok), ok
    for bad in (
        "https://evil.example",
        "http://localhost.evil.example",
        "http://127.0.0.1.nip.io",
        "null",
        "file://",
        "http://localhost:3000/path",
    ):
        assert not origin_allowed(cfg, bad), bad
    extended = HubConfig(allow_origins=("https://app.example/",))
    assert origin_allowed(extended, "https://APP.example")
    assert not origin_allowed(extended, "https://app.example:8443")


def test_token_extraction_prefers_bearer() -> None:
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("bearer   abc ") == "abc"
    assert bearer_token("Basic abc") is None
    assert bearer_token("Bearer ") is None
    headers = {"Authorization": "Bearer hdr"}
    assert extract_token(headers.get, {"token": ["q"]}) == ("hdr", False)
    assert extract_token({}.get, {"token": ["q"]}) == ("q", True)
    assert extract_token({}.get, {}) == (None, False)


def test_redact_helpers() -> None:
    text = f"GET /?stream_id=a&token={TOKEN}&x=1 Authorization: Bearer {TOKEN}"
    out = redact(text)
    assert TOKEN not in out
    assert "token=***&x=1" in out and "Bearer ***" in out
    assert redact(f"value {TOKEN} here", [TOKEN]) == "value *** here"
    hint = "URL query (?token=); use the 'Authorization: Bearer <token>' header"
    assert redact(hint) == hint  # placeholders in our own warning stay readable


def test_redacting_filter_scrubs_args() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter([TOKEN]))
    log = logging.getLogger("eeg_test_redact")
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        log.debug("< GET %s HTTP/1.1", f"/?token={TOKEN}")
        log.debug("secret=%s", TOKEN)
    finally:
        log.removeHandler(handler)
    assert TOKEN not in stream.getvalue()


def test_redacting_filter_scrubs_exc_info_exc_text_and_stack_info() -> None:
    """The token in a traceback must not survive the filter, even when the message is clean."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    redactor = RedactingFilter([TOKEN])
    handler.addFilter(redactor)
    log = logging.getLogger("eeg_test_redact_exc")
    log.addHandler(handler)
    log.propagate = False
    log.setLevel(logging.DEBUG)
    try:
        try:
            raise ValueError(f"invalid HTTP header value: Bearer {TOKEN}")
        except ValueError:
            log.error("! no valid HTTP request", exc_info=True)
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="eeg_test_redact_exc",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="cached",
            args=(),
            exc_info=exc_info,
        )
        record.exc_text = f"ValueError: Bearer {TOKEN}\n"
        record.stack_info = f"Stack (most recent call last):\n  Authorization: Bearer {TOKEN}\n"
        assert redactor.filter(record) is True
        assert record.exc_text is not None and TOKEN not in record.exc_text
        assert record.stack_info is not None and TOKEN not in record.stack_info
        assert "***" in record.exc_text and "***" in record.stack_info
        if record.exc_info:
            rendered = logging.Formatter().formatException(record.exc_info)
            assert TOKEN not in rendered
    finally:
        log.removeHandler(handler)
        log.propagate = True
    text = stream.getvalue()
    assert "Traceback" in text
    assert "invalid HTTP header value" in text
    assert TOKEN not in text
    assert "***" in text


# ---------------------------------------------------------------- non-loopback bind refusal


def test_config_rejects_non_loopback_without_allow_lan() -> None:
    for host in ("0.0.0.0", "::", "192.168.1.20"):
        with pytest.raises(ValueError, match="allow-lan"):
            HubConfig(host=host)
    assert HubConfig(host="::1").host == "::1"


def test_cli_bind_all_without_allow_lan_exits(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--host", "0.0.0.0", "--synth", "1"]) == 2
    err = capsys.readouterr().err
    assert "--allow-lan" in err and "0.0.0.0" in err


def test_cli_subprocess_exit_code() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "eeg_connection_hub.cli", "--host", "0.0.0.0", "--synth", "1"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "--allow-lan" in proc.stderr


# ---------------------------------------------------------------- integration: WebSocket


async def test_ws_foreign_origin_rejected() -> None:
    cfg = _loopback_config()
    async with running_hub(cfg):
        with pytest.raises(InvalidStatus) as exc:
            async with websockets.connect(f"ws://127.0.0.1:{cfg.ws_port}", origin="https://evil.example"):
                pass
        assert exc.value.response.status_code == 403


async def test_ws_loopback_origin_allowed() -> None:
    cfg = _loopback_config()
    async with running_hub(cfg):
        origin = f"http://localhost:{cfg.http_port}"
        async with websockets.connect(f"ws://127.0.0.1:{cfg.ws_port}", origin=origin) as ws:
            msg = await _recv_envelope(ws)
            assert msg["hub_schema"] == "eeg-connection-hub.envelope/v1"


async def test_ws_no_origin_allowed() -> None:
    cfg = _loopback_config()
    async with running_hub(cfg):
        async with websockets.connect(f"ws://127.0.0.1:{cfg.ws_port}") as ws:
            assert "stream_id" in await _recv_envelope(ws)


async def test_ws_allow_origin_flag_extends_list() -> None:
    cfg = _loopback_config(allow_origins=("https://app.example",))
    async with running_hub(cfg):
        async with websockets.connect(f"ws://127.0.0.1:{cfg.ws_port}", origin="https://app.example") as ws:
            assert "stream_id" in await _recv_envelope(ws)


async def test_ws_rebinding_host_rejected() -> None:
    cfg = _loopback_config()
    async with running_hub(cfg):
        status, _ = await _raw_http(cfg.ws_port, _upgrade(f"evil.example:{cfg.ws_port}"))
        assert status == 403
        status, _ = await _raw_http(cfg.ws_port, _upgrade(f"127.0.0.1:{cfg.ws_port}"))
        assert status == 101


async def test_ws_bearer_header_accepted() -> None:
    cfg = _lan_config()
    async with running_hub(cfg):
        async with websockets.connect(
            f"ws://127.0.0.1:{cfg.ws_port}",
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
        ) as ws:
            assert "stream_id" in await _recv_envelope(ws)


@pytest.mark.parametrize("auth", [None, "Bearer wrong-token"])
async def test_ws_missing_or_wrong_token_closes_4401(auth: str | None) -> None:
    cfg = _lan_config()
    headers = {"Authorization": auth} if auth else None
    async with running_hub(cfg):
        async with websockets.connect(f"ws://127.0.0.1:{cfg.ws_port}", additional_headers=headers) as ws:
            with pytest.raises(ConnectionClosed) as exc:
                await asyncio.wait_for(ws.recv(), timeout=3.0)
        assert exc.value.rcvd is not None and exc.value.rcvd.code == 4401


async def test_ws_query_token_still_works_but_warns(caplog: pytest.LogCaptureFixture) -> None:
    cfg = _lan_config()
    caplog.set_level(logging.WARNING, logger="eeg_connection_hub.hub_server")
    async with running_hub(cfg):
        async with websockets.connect(f"ws://127.0.0.1:{cfg.ws_port}?token={TOKEN}") as ws:
            assert "stream_id" in await _recv_envelope(ws)
    assert any("deprecated" in r.getMessage() for r in caplog.records)
    assert all(TOKEN not in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------- integration: HTTP


@pytest.mark.parametrize("path", ["/health", "/streams"])
async def test_http_rebinding_host_rejected(path: str) -> None:
    cfg = _loopback_config()
    async with running_hub(cfg):
        status, body = await _raw_http(cfg.http_port, _get(path, host="evil.example"))
        assert status == 403 and json.loads(body)["error"] == "forbidden host"
        status, _ = await _raw_http(cfg.http_port, _get(path, host=None))
        assert status == 403
        for good in ("127.0.0.1", f"localhost:{cfg.http_port}", f"[::1]:{cfg.http_port}"):
            status, _ = await _raw_http(cfg.http_port, _get(path, host=good))
            assert status == 200, good


async def test_http_foreign_origin_rejected() -> None:
    cfg = _loopback_config()
    async with running_hub(cfg):
        status, _ = await _raw_http(cfg.http_port, _get("/health", extra="Origin: https://evil.example\r\n"))
        assert status == 403
        status, _ = await _raw_http(cfg.http_port, _get("/health", extra="Origin: http://localhost:3000\r\n"))
        assert status == 200


async def test_http_token_header_and_401() -> None:
    cfg = _lan_config()
    async with running_hub(cfg):
        status, _ = await _raw_http(cfg.http_port, _get("/health"))
        assert status == 401
        status, _ = await _raw_http(cfg.http_port, _get("/health", extra="Authorization: Bearer nope\r\n"))
        assert status == 401
        ok = f"Authorization: Bearer {TOKEN}\r\n"
        status, body = await _raw_http(cfg.http_port, _get("/health", extra=ok))
        assert status == 200 and json.loads(body)["auth_required"] is True
        status, _ = await _raw_http(cfg.http_port, _get(f"/streams?token={TOKEN}"))
        assert status == 200  # deprecated query token still accepted
        status, _ = await _raw_http(cfg.http_port, _get("/health", host="evil.example", extra=ok))
        assert status == 403  # Host check applies even with a valid token


# ---------------------------------------------------------------- token absent from verbose logs


async def test_token_absent_from_verbose_logs() -> None:
    """Run with the CLI's redaction installed and websockets DEBUG logging, like `eeg-hub -v`."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        cfg = _lan_config()
        cli.install_log_redaction([cfg.access_token])
        async with running_hub(cfg):
            uri = f"ws://127.0.0.1:{cfg.ws_port}"
            async with websockets.connect(f"{uri}?token={TOKEN}") as ws:
                await _recv_envelope(ws)
            async with websockets.connect(uri, additional_headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
                await _recv_envelope(ws)
            await _raw_http(cfg.http_port, _get(f"/health?token={TOKEN}"))
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        for h in root.handlers:
            h.filters = [f for f in h.filters if not isinstance(f, RedactingFilter)]
    text = stream.getvalue()
    assert "websockets" in text  # debug logs were actually produced
    assert "token=***" in text
    assert TOKEN not in text


async def test_ws_control_byte_authorization_traceback_is_redacted() -> None:
    """A stray control byte in Authorization makes websockets raise ValueError(header).

    Under ``-v`` that exception is logged with ``exc_info``. The token must not
    appear in the traceback.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        cfg = _lan_config()
        cli.install_log_redaction([cfg.access_token])
        async with running_hub(cfg):
            key = base64.b64encode(os.urandom(16)).decode()
            request = (
                "GET / HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{cfg.ws_port}\r\n"
                f"Authorization: Bearer {TOKEN}\x01\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n"
            )
            reader, writer = await asyncio.open_connection("127.0.0.1", cfg.ws_port)
            writer.write(request.encode("latin-1"))
            await writer.drain()
            await asyncio.wait_for(reader.read(4096), timeout=3.0)
            writer.close()
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        for log_handler in root.handlers:
            log_handler.filters = [flt for flt in log_handler.filters if not isinstance(flt, RedactingFilter)]
    text = stream.getvalue()
    assert "invalid HTTP header value" in text
    assert "Traceback" in text
    assert "***" in text
    assert TOKEN not in text
