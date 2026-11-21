# Security

## Threat model (beta)

Local lab / LAN collaboration tool. **Not** hardened for internet exposure.

## Defaults

- Bind `127.0.0.1` — only local processes can connect.
- Features-only frames — no raw EEG unless explicitly enabled on loopback.

## LAN exposure

Requires **both**:

1. `--allow-lan` (binds `0.0.0.0`, or the given `--host`)
2. An access token: `--access-token <secret>` or `$EEG_HUB_ACCESS_TOKEN` (preferred: keeps it out of `ps`). An operator-supplied token must be at least 32 characters; a shorter value is a startup error (exit 2). If omitted, one is auto-generated with `secrets.token_urlsafe(32)` (43 characters) and printed once to stderr.

A non-loopback `--host` (e.g. `0.0.0.0`) without `--allow-lan` is a startup error (exit 2).

Clients send `Authorization: Bearer <token>` on the WebSocket handshake and HTTP requests. The old `?token=` query parameter still works but is **deprecated and will be removed in 0.2.0** (URLs end up in logs, shell history, and proxies); the hub logs a one-time warning when it is used.

Unauthorized clients receive HTTP 401 or WebSocket close `4401`.

## Browser protections (Origin / Host)

A web page in the user's browser can try to reach a local hub. Two checks run before auth on both the WebSocket handshake and HTTP (`/health`, `/streams`); failures get HTTP 403:

- **Origin allowlist** — requests with no `Origin` (curl, Python, native apps) and loopback origins (`http(s)://localhost`, `127.0.0.1`, `[::1]`, any port) are allowed. Anything else, including `null`, needs `--allow-origin <origin>` (repeatable).
- **Host check (DNS rebinding)** — loopback mode accepts only `localhost`, `127.0.0.1`, `[::1]` (any port). LAN mode also accepts the bound address, any IP literal (rebinding requires a DNS name), and names given with `--allow-host` (e.g. `labpc.local`). Requests without a `Host` header are rejected.

## Log redaction

Access tokens are scrubbed (`token=***`, `Bearer ***`, and the literal token value) from every log record, including websockets' handshake debug lines under `-v`. The same filter cleans `exc_info`, `exc_text`, and `stack_info`, so a malformed header that echoes the token inside a `ValueError` traceback (for example an `Authorization` value with a stray control byte) does not land in the log. The configured token is replaced first — including percent-encoded forms, upper or lower hex, and that encoded value when it appears outside `token=` — and only then do the `token=` / `Bearer` patterns run. Characters such as `!`, `:`, and `,` used to leave a suffix in header dumps and `?token=` lines; the exact value is removed in full. The startup log line never contains the token.

## Raw EEG opt-in

- `--enable-raw-eeg` refused when bind address is not loopback.
- Raw window is clipped (±250 µV scale) and capped (≤8 samples × 4 channels).
- Never written to application logs.

## Dependencies

- Optional `pylsl` for live LSL; synthetic mode avoids it in CI.
- Keep BlueMuse and OS firewall policies in mind when enabling LAN.

## Reporting

For ManintheCrowds-internal issues, contact the repo operator. No public security advisory process until GitHub publication is approved.
