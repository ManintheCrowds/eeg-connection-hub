# Contributing

## Scope

Local beta for ManintheCrowds. **Do not** run `gh repo create` or push without operator approval.

## Setup

```powershell
pip install -e ".[dev,lsl]"
pytest
```

## Code style

- Type hints on public functions
- `# PURPOSE:` header on modules
- Match existing patterns in `src/eeg_connection_hub/`

## Tests

- **Unit:** feature math, schema, discovery — `tests/test_features.py`
- **Integration:** multi-stream, multi-subscriber, auth, stale, raw opt-in — `tests/test_integration.py`

Add tests for new behavior; do not claim live Muse validation without hardware runs.

## Licenses

- Hub: MIT (`LICENSE`)
- Do not copy ENTHEA or BlueMuse source into this tree
- Document upstream GPL-3.0 (BlueMuse) and AGPL-3.0 (ENTHEA) as ecosystem dependencies

## Git

Initial tag after first commit: `pre-eeg-hub-beta-20260718` (or similar pre-release tag).
