# Architecture

## Radio vs router

**Radio** (upstream): a Muse LSL publisher such as BlueMuse, muselsl, or another tool streams raw EEG on the LAN.  
**Router** (this hub): consumes those LSL streams, derives features, assigns session identity, and fans out to many apps over WebSocket. The hub does not pair BLE devices or replace the radio.

## Problem

ManintheCrowds sessions may include **multiple** Muse headsets on a LAN, each publishing EEG via LSL. Downstream apps (e.g. ENTHEA-inspired consumers) need **derived features**, not raw EEG, with stable identity per headset and support for **multiple subscribers**.

## Existing bridge (reference)

`MiscRepos/local-proto/scripts/bmi_eeg/enthea_bridge.py`:

- Resolves LSL, picks **one** Muse EEG stream (`select_muse_stream` raises if >1).
- Computes bandpower → `enthea.muse.features/v1` at a fixed rate.
- Broadcasts the **same** latest frame to all WebSocket clients.
- Binds loopback by default; no HTTP catalog; no session routing.
- No dedicated multi-subscriber integration test in that tree.

An LSL publisher **can** expose multiple streams; the bridge does not consume more than one.

## Hub design

```
  Muse LSL publisher (N streams)
        ↓
  discover_muse_eeg_streams()
        ↓
  Per-stream StreamWorker (buffer → features_from_buffer)
        ↓
  StreamRegistry (stream_id, session_id, catalog)
        ↓
  Hub envelope (eeg-connection-hub.envelope/v1)
        ↓
  WebSocket fan-out (all subscribers, optional stream_id filter)
        ↓
  HTTP /health, /streams
```

### Stream IDs

Stable ids derived from LSL `source_id` when present, else hashed name/type. Collisions get a short suffix.

### Sessions

On first sighting of a stream, the registry assigns:

- `session_id` — opaque hex token
- `session_label` — pseudonymous label (`participant-001`, …)

No PII is required; operators may override labels in future config.

### Protocol

Inner payload remains **`enthea.muse.features/v1`** for compatibility. Hub wraps:

```json
{
  "hub_schema": "eeg-connection-hub.envelope/v1",
  "stream_id": "muse-demo-headset-1",
  "session_id": "…",
  "session_label": "participant-001",
  "features": { "schema": "enthea.muse.features/v1", … }
}
```

### Security boundaries

- Default: loopback only, no token. Non-loopback `--host` without `--allow-lan` refuses to start.
- LAN: `--allow-lan` + access token via `Authorization: Bearer` on WS handshake and HTTP. Operator-supplied tokens (`--access-token`, `$EEG_HUB_ACCESS_TOKEN`) must be at least 32 characters or startup exits 2. An omitted token is auto-generated into a mode-0600 file under `$XDG_STATE_HOME` (default `~/.local/state`); stderr gets the path only, so service logs do not capture the secret. Legacy `?token=` is accepted until 0.2.0 and logs a deprecation warning. Missing/wrong token: HTTP 401 / WS close 4401.
- Front-door checks (`security.py`, wired via websockets `process_request` and the HTTP handler), applied before auth:
  - `Host` allowlist (DNS rebinding): loopback names always; in LAN mode also the bound host, IP literals, and `--allow-host` names. Else 403.
  - `Origin` allowlist: absent (non-browser) or loopback origins; others need `--allow-origin`. Else 403.
- Logs: a handler-level filter redacts the configured token (including percent-encoded forms) before the `?token=…` and `Bearer …` patterns. That covers websockets debug output and exception tracebacks (`exc_info`, `exc_text`, `stack_info`) under `-v`.
- Raw EEG: disabled unless `--enable-raw-eeg`; loopback-only; excluded from logs.
- Internet multi-tenancy: **out of scope** for beta.

### Synthetic mode

`--synth N` uses in-memory inlets (`synthetic.py`) for demo and CI without pylsl or hardware.

## Non-goals (beta)

- Official BlueMuse or ENTHEA integration bundle
- Cloud relay or account system
- BLE / direct Muse pairing (use an upstream LSL publisher)
