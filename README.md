# EEG Connection Hub (beta)

LAN-first broker that discovers **multiple** BlueMuse-published Muse LSL EEG streams, computes derived feature frames compatible with `enthea.muse.features/v1`, and fans out to **multiple** WebSocket subscribers with stable stream IDs and pseudonymous sessions.

**Public beta:** [ManintheCrowds/eeg-connection-hub](https://github.com/ManintheCrowds/eeg-connection-hub). Formal GitHub Release still waiting-human (**EEG-HUB-2**). Live Muse hardware validation has not been performed.

## Quick start

```powershell
cd C:\Users\Dell\Documents\GitHub\eeg-connection-hub
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
eeg-hub --synth 2
```

- WebSocket: `ws://127.0.0.1:8765` (one JSON envelope per stream per tick)
- HTTP health: `http://127.0.0.1:8766/health`
- Stream catalog: `http://127.0.0.1:8766/streams`

Example consumer:

```powershell
python examples/enthea_consumer.py --uri ws://127.0.0.1:8765
```

## Honest comparison to existing bridge

| Capability | `local-proto` `enthea_bridge.py` | `eeg-connection-hub` |
|------------|----------------------------------|----------------------|
| Multiple WS subscribers | Yes (broadcast same frame) | Yes (per-stream envelopes) |
| Multiple Muse LSL inputs | **No** — rejects if >1 stream | **Yes** — discovers all |
| Stream catalog / health HTTP | No | Yes |
| Session / stream routing | No | Yes (`stream_id`, `session_id`) |
| Multi-client integration test | No explicit test | Yes (pytest) |
| Default bind | Loopback | Loopback |
| Raw EEG | Never emitted | Opt-in, loopback-only |

BlueMuse can publish **multiple** Muse headsets on one LAN; the existing bridge selects at most one and raises if ambiguous. This hub generalizes that path.

## Ecosystem attribution (not a fork)

- **Consumes** LSL streams typically published by [BlueMuse](https://github.com/kowalej/BlueMuse) (GPL-3.0). This hub does **not** bundle or fork BlueMuse.
- Feature schema aligns with the ENTHEA Muse seam (`enthea.muse.features/v1`) used in [ENTHEA](https://github.com/elder-plinius/ENTHEA) (AGPL-3.0). This hub does **not** copy ENTHEA source; see `examples/enthea_consumer.py` for a minimal consumer.
- **This package** is MIT-licensed (see `LICENSE`).

## Configuration highlights

| Flag | Default | Meaning |
|------|---------|---------|
| `--host` | `127.0.0.1` | Loopback bind |
| `--allow-lan` | off | Bind `0.0.0.0`; requires `--access-token` (auto-generated if omitted) |
| `--enable-raw-eeg` | off | Attach clipped raw window in envelope; loopback only; never logged |
| `--synth N` | `0` | Use N synthetic Muse streams (no hardware) |

## Validation status

- Unit and integration tests pass against **synthetic** LSL-equivalent sources.
- **Live Muse hardware validation has not been performed** in this beta drop.

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [PRIVACY.md](PRIVACY.md)
- [SECURITY.md](SECURITY.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)

## Proposed GitHub metadata (for later approval)

**Description:** LAN-first multi-stream Muse EEG feature broker — BlueMuse LSL in, ENTHEA-compatible WebSocket out.

**Topics:** `eeg`, `muse-headband`, `lsl`, `lab-streaming-layer`, `websocket`, `bluemuse`, `neurofeedback`, `maninthecrowds`
