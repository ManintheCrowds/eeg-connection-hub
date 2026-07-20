# Privacy

## Data handled

| Data | Default | Notes |
|------|---------|-------|
| Derived band powers | Published | Relative bands + ENTHEA-style controls |
| Quality / artifact flags | Published | No raw samples |
| Raw EEG samples | **Off** | Opt-in via `--enable-raw-eeg`; loopback only |
| Session labels | Pseudonymous | `participant-NNN`; operator-assigned labels optional later |
| Access tokens | LAN mode only | Printed once to stderr at startup; not logged |

## Logging

- Hub logs stream **names**, ids, and errors — **never** raw EEG arrays or token values.
- Enable verbose logging with `-v`; still no raw sample payload.

## Retention

- In-memory only during runtime; no persistence layer in beta.
- Consumers are responsible for their own storage policies.

## Third parties

- BlueMuse and Muse SDK privacy terms apply upstream of this hub.
- This package does not phone home.
