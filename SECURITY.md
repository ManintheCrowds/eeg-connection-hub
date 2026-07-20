# Security

## Threat model (beta)

Local lab / LAN collaboration tool. **Not** hardened for internet exposure.

## Defaults

- Bind `127.0.0.1` — only local processes can connect.
- Features-only frames — no raw EEG unless explicitly enabled on loopback.

## LAN exposure

Requires **both**:

1. `--allow-lan` (binds `0.0.0.0`)
2. `--access-token <secret>` (auto-generated if omitted)

Clients must pass `?token=` on WebSocket URLs and HTTP requests.

Unauthorized clients receive HTTP 401 or WebSocket close `4401`.

## Raw EEG opt-in

- `--enable-raw-eeg` refused when bind address is not loopback.
- Raw window is clipped (±250 µV scale) and capped (≤8 samples × 4 channels).
- Never written to application logs.

## Dependencies

- Optional `pylsl` for live LSL; synthetic mode avoids it in CI.
- Keep BlueMuse and OS firewall policies in mind when enabling LAN.

## Reporting

For ManintheCrowds-internal issues, contact the repo operator. No public security advisory process until GitHub publication is approved.
