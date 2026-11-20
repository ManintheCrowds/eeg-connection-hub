#!/usr/bin/env bash
# Friday 3 smoke: Origin allowlist, Host check, bind refusal. Synthetic streams only (--synth 2).
# Usage: PY=/path/to/venv/bin/python bash evidence/friday3-20261120/smoke.sh
set -u
PY="${PY:-python}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WS=28765; HTTP=28766
run() { echo "\$ $*"; "$@"; echo "[exit $?]"; echo; }

echo "# date: $(date '+%Y-%m-%d %H:%M:%S %Z')  git: $(git -C "$HERE" rev-parse --short HEAD)"
echo "# $($PY --version)  websockets $($PY -c 'import websockets; print(websockets.__version__)')"
echo
echo "## start hub (loopback, synthetic)"
echo "\$ $PY -m eeg_connection_hub.cli --synth 2 --ws-port $WS --http-port $HTTP -v &"
$PY -m eeg_connection_hub.cli --synth 2 --ws-port $WS --http-port $HTTP -v >"$HERE/hub-stderr.txt" 2>&1 &
HUB=$!
sleep 2

echo "## (i) WS with Origin https://evil.example -> expect rejection"
run $PY "$HERE/ws_probe.py" "ws://127.0.0.1:$WS" "https://evil.example"

echo "## (ii) WS with Origin http://localhost:$HTTP -> expect frames"
run $PY "$HERE/ws_probe.py" "ws://127.0.0.1:$WS" "http://localhost:$HTTP"

echo "## (iii) /health with Host: evil.example (DNS rebinding) -> expect 403; Host: 127.0.0.1 -> 200"
run curl -sS -i -H "Host: evil.example" "http://127.0.0.1:$HTTP/health"
echo
run curl -sS -i -H "Host: 127.0.0.1" "http://127.0.0.1:$HTTP/health"
echo

kill $HUB; wait $HUB 2>/dev/null

echo "## (iv) --host 0.0.0.0 without --allow-lan -> expect non-zero exit + clear message"
run $PY -m eeg_connection_hub.cli --host 0.0.0.0 --synth 2
echo "## (v, bonus) LAN mode + -v: Bearer header works, wrong token 401, token never in logs"
# Random throwaway token generated here and never echoed; passed via env, not argv.
export EEG_HUB_ACCESS_TOKEN="$($PY -c 'import secrets; print(secrets.token_urlsafe(24))')"
$PY -m eeg_connection_hub.cli --allow-lan --synth 2 --ws-port $WS --http-port $HTTP -v >"$HERE/hub-lan-stderr.txt" 2>&1 &
HUB=$!
sleep 2
echo "\$ curl -H 'Authorization: Bearer <token>' http://127.0.0.1:$HTTP/streams   -> HTTP \
$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $EEG_HUB_ACCESS_TOKEN" "http://127.0.0.1:$HTTP/streams")"
echo "\$ curl -H 'Authorization: Bearer wrong' http://127.0.0.1:$HTTP/streams      -> HTTP \
$(curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer wrong' "http://127.0.0.1:$HTTP/streams")"
echo "\$ curl 'http://127.0.0.1:$HTTP/health?token=<token>' (deprecated)          -> HTTP \
$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$HTTP/health?token=$EEG_HUB_ACCESS_TOKEN")"
$PY - "$WS" <<'PYEOF'
import asyncio, json, os, sys
import websockets
async def main():
    tok = os.environ["EEG_HUB_ACCESS_TOKEN"]
    uri = f"ws://127.0.0.1:{sys.argv[1]}"
    async with websockets.connect(uri, additional_headers={"Authorization": f"Bearer {tok}"}) as ws:
        print("WS Bearer: FRAME", json.loads(await ws.recv())["stream_id"])
    async with websockets.connect(f"{uri}?token={tok}") as ws:
        print("WS ?token= (deprecated): FRAME", json.loads(await ws.recv())["stream_id"])
    async with websockets.connect(uri, additional_headers={"Authorization": "Bearer wrong"}) as ws:
        try:
            await asyncio.wait_for(ws.recv(), 3)
        except websockets.exceptions.ConnectionClosed as exc:
            print("WS wrong token: closed code", exc.rcvd.code, exc.rcvd.reason)
asyncio.run(main())
PYEOF
kill $HUB; wait $HUB 2>/dev/null
echo "token occurrences in -v hub log: $(grep -cF "$EEG_HUB_ACCESS_TOKEN" "$HERE/hub-lan-stderr.txt")"
echo "redacted markers in -v hub log:  $(grep -c 'token=\*\*\*' "$HERE/hub-lan-stderr.txt")"
grep -m3 -E 'token=\*\*\*|deprecated' "$HERE/hub-lan-stderr.txt"
unset EEG_HUB_ACCESS_TOKEN
echo
echo "## hub log excerpt (rejections)"
grep -E "rejected|connection rejected" "$HERE/hub-stderr.txt"

# Elide random per-connection WebSocket handshake nonces (not secrets, but they trip gitleaks' generic rule).
sed -i -E 's/(Sec-WebSocket-(Key|Accept): ).*/\1<per-connection nonce elided>/' "$HERE"/hub-*stderr.txt
