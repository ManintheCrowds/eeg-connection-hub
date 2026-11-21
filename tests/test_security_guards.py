# PURPOSE: Tests that fail when a security guard is removed — constant-time token compare,
#          the legacy websockets 12 handshake hook, CLI log redaction, and Origin user-info.
from __future__ import annotations

import ast
import importlib
import inspect
import io
import logging
import secrets
import textwrap

import pytest
from test_integration import _free_port, running_hub
from test_security import TOKEN, _loopback_config, _raw_http, _upgrade

from eeg_connection_hub import cli
from eeg_connection_hub.auth import secrets_compare, validate_token
from eeg_connection_hub.config import HubConfig
from eeg_connection_hub.security import RedactingFilter, origin_allowed


def _call_names(fn: object) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _has_equality_compare(fn: object) -> bool:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and any(isinstance(op, ast.Eq) for op in node.ops):
            return True
    return False


def _calls_prefix_method(fn: object) -> bool:
    return bool(_call_names(fn) & {"startswith", "endswith"})


def _operator_token() -> str:
    """32+ character token with characters the bearer pattern stops on."""
    core = secrets.token_hex(16)
    return core[:8] + "!" + core[8:16] + ":" + core[16:24] + "," + core[24:]


def test_token_compare_rejects_prefix_and_is_constant_time() -> None:
    """A prefix match or ``==`` must not authenticate.

    Behavioral checks catch ``startswith`` / sliced prefixes. The AST checks
    catch a non-constant ``==`` that still accepts only the exact token.
    """
    cfg = HubConfig(
        host="0.0.0.0",
        allow_lan=True,
        access_token=TOKEN,
        synth_streams=0,
    )
    assert validate_token(cfg, TOKEN) is True
    assert validate_token(cfg, TOKEN[:-1]) is False
    assert validate_token(cfg, TOKEN + "x") is False
    assert validate_token(cfg, "x" + TOKEN) is False
    assert validate_token(cfg, "q" * len(TOKEN)) is False

    assert "compare_digest" in _call_names(secrets_compare)
    assert "secrets_compare" in _call_names(validate_token)
    assert not _has_equality_compare(secrets_compare)
    assert not _has_equality_compare(validate_token)
    assert not _calls_prefix_method(secrets_compare)
    assert not _calls_prefix_method(validate_token)


@pytest.mark.parametrize(
    "origin",
    [
        "http://user@127.0.0.1",
        "http://user@localhost:3000",
        "https://user@localhost",
        "http://user@[::1]",
    ],
)
def test_origin_userinfo_is_rejected(origin: str) -> None:
    """Loopback host with user-info is not a loopback origin. Removing the username check allows it."""
    assert origin_allowed(HubConfig(), origin) is False


def test_cli_installs_log_redaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """Skipping install_log_redaction leaves the token in the log line the CLI's own handler emits."""
    token = _operator_token()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    async def fake_run(self: object) -> None:
        logging.getLogger("eeg.redaction.probe").warning(
            "Authorization: Bearer %s",
            getattr(self, "config").access_token,
        )

    monkeypatch.setattr(cli.EEGConnectionHub, "run", fake_run)
    try:
        code = cli.main(
            [
                "--allow-lan",
                "--access-token",
                token,
                "--synth",
                "0",
                "--ws-port",
                str(_free_port()),
                "--http-port",
                str(_free_port()),
            ]
        )
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        for log_handler in root.handlers:
            log_handler.filters = [flt for flt in log_handler.filters if not isinstance(flt, RedactingFilter)]
    assert code == 0
    assert any(isinstance(flt, RedactingFilter) for flt in handler.filters)
    text = stream.getvalue()
    tail = token.split("!", 1)[1]
    assert token not in text
    assert tail not in text
    assert "***" in text
    assert token not in capsys.readouterr().err


async def test_legacy_ws_hook_is_checked_when_asyncio_api_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The websockets 12 fallback must reject a bad Host and a foreign Origin.

    The suite otherwise imports websockets' asyncio server, so deleting the
    legacy hook's checks — or not wiring it — does not fail those tests.
    """
    real_import = importlib.import_module

    def hide_asyncio_server(name: str, package: str | None = None) -> object:
        if name == "websockets.asyncio.server":
            raise ImportError("forced legacy hook")
        return real_import(name, package)

    monkeypatch.setattr("eeg_connection_hub.hub_server.importlib.import_module", hide_asyncio_server)
    cfg = _loopback_config()
    async with running_hub(cfg):
        status, body = await _raw_http(cfg.ws_port, _upgrade(f"evil.example:{cfg.ws_port}"))
        assert status == 403 and b"forbidden host" in body
        status, body = await _raw_http(
            cfg.ws_port,
            _upgrade(f"127.0.0.1:{cfg.ws_port}", origin="https://evil.example"),
        )
        assert status == 403 and b"forbidden origin" in body
        status, _body = await _raw_http(cfg.ws_port, _upgrade(f"127.0.0.1:{cfg.ws_port}"))
        assert status == 101
