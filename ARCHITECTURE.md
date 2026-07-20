# Architecture

## Problem

ManintheCrowds sessions may include **multiple** Muse headsets on a LAN, each publishing EEG via BlueMuse → LSL. Downstream apps (e.g. ENTHEA-inspired consumers) need **derived features**, not raw EEG, with stable identity per headset and support for **multiple subscribers**.

## Existing bridge (reference)

`MiscRepos/local-proto/scripts/bmi_eeg/enthea_bridge.py`:

- Resolves LSL, picks **one** Muse EEG stream (`select_muse_stream` raises if >1).
- Computes bandpower → `enthea.muse.features/v1` at a fixed rate.
- Broadcasts the **same** latest frame to all WebSocket clients.
- Binds loopback by default; no HTTP catalog; no session routing.
- No dedicated multi-subscriber integration test in that tree.

BlueMuse **can** expose multiple streams; the bridge does not consume more than one.

## Hub design

```
  BlueMuse → LSL (N streams)
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

- Default: loopback only, no token.
- LAN: `--allow-lan` + access token on WS query (`?token=`) and HTTP.
- Raw EEG: disabled unless `--enable-raw-eeg`; loopback-only; excluded from logs.
- Internet multi-tenancy: **out of scope** for beta.

### Synthetic mode

`--synth N` uses in-memory inlets (`synthetic.py`) for demo and CI without pylsl or hardware.

## Non-goals (beta)

- Official BlueMuse or ENTHEA integration bundle
- Cloud relay or account system
- BLE / direct Muse pairing (use BlueMuse upstream)
