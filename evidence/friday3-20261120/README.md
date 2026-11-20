# Friday 3 evidence — local privacy hardening (2026-11-20, America/Chicago)

Synthetic data only (`--synth 2`); no real EEG, no hardware. **Hardware unverified.**

| File | What |
|------|------|
| `smoke.sh` | Reproducible smoke: `PY=<venv>/bin/python bash evidence/friday3-20261120/smoke.sh` |
| `ws_probe.py` | Minimal websockets client used by the smoke (connect with a chosen `Origin`) |
| `smoke-output.txt` | Captured run: (i) foreign Origin → 403, (ii) loopback Origin → frames, (iii) `Host: evil.example` → 403 vs `Host: 127.0.0.1` → 200, (iv) `--host 0.0.0.0` w/o `--allow-lan` → exit 2, (v) LAN+`-v`: Bearer OK / wrong → 401 & 4401 / token count in log = 0 |
| `hub-stderr.txt`, `hub-lan-stderr.txt` | Hub `-v` logs from the smoke (the LAN token was a throwaway random value passed via env and never echoed; log shows only `***`; random WebSocket handshake nonces are elided by `smoke.sh`) |
| `tests-and-lint.txt` | pytest on Python 3.10–3.13 + `ruff check .` |

Also checked by hand (not scripted): the legacy websockets 12.0 `process_request` path returns 403 for a foreign Origin / bad Host and 101 for a loopback Origin.
