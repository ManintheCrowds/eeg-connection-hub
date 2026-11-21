# Privacy

## Data handled

| Data | Default | Notes |
|------|---------|-------|
| Derived band powers | Published | Relative bands + ENTHEA-style controls |
| Quality / artifact flags | Published | No raw samples |
| Raw EEG samples | **Off** | Opt-in via `--enable-raw-eeg`; loopback only |
| Session labels | Pseudonymous | `participant-NNN`; operator-assigned labels optional later |
| Access tokens | LAN mode only | Auto-generated token is stored in a mode-0600 file under the XDG state directory; stderr and service logs (journald, Docker, CI) receive only the path. Operator-supplied tokens (at least 32 characters) are never printed. Redacted from all logs, including `-v` and tracebacks. |

## Logging

- Hub logs stream **names**, ids, and errors — **never** raw EEG arrays or token values.
- Enable verbose logging with `-v`; still no raw sample payload.

## Retention

- In-memory only during runtime; no persistence layer in beta.
- Consumers are responsible for their own storage policies.

## Third parties

- BlueMuse and Muse SDK privacy terms apply upstream of this hub.
- This package does not phone home.
