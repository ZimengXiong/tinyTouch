#!/bin/zsh
# Smoke-test the on-device dashboard after flash (no fingerprint mutations).
set -euo pipefail

host="${1:-192.168.7.1}"
base="http://${host}"

print "Ping ${host}…"
ping -c 2 -W 1000 "$host" >/dev/null

print "GET / …"
code="$(curl -sS -o /tmp/tt-dash.html -w '%{http_code}' -H "Host: ${host}" "$base/")"
[[ "$code" == "200" ]] || { print -u2 "index HTTP $code"; exit 1; }

print "GET /api/status …"
status="$(curl -sS -H "Host: ${host}" "$base/api/status")"
print "$status" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert "mode" in d and "idle_led" in d and "slots" in d; print("ok", d.get("mode"), "fps="+str(d.get("fingerprints")), "idle_led="+str(d.get("idle_led")))'

print "GET /api/log …"
curl -sS -H "Host: ${host}" "$base/api/log" | python3 -c 'import json,sys; json.load(sys.stdin); print("ok")'

print "Locked mutation should 401 …"
code="$(curl -sS -o /tmp/tt-label.json -w '%{http_code}' -H "Host: ${host}" -H 'Content-Type: application/json' \
  -d '{"slot":1,"label":"x"}' "$base/api/label")"
[[ "$code" == "401" ]] || { print -u2 "expected 401 got $code"; cat /tmp/tt-label.json; exit 1; }

print "CDC still alive (PING)…"
port="$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)"
if [[ -n "$port" ]]; then
  python3 - "$port" <<'PY'
import sys, time
try:
    import serial
except ImportError:
    print("pyserial missing; skip CDC check")
    raise SystemExit(0)
s = serial.Serial(sys.argv[1], 115200, timeout=1)
time.sleep(0.2)
s.reset_input_buffer()
s.write(b"PING\n")
time.sleep(0.4)
out = s.read(200).decode(errors="replace")
s.close()
assert "PONG" in out, out
print("ok PONG")
PY
else
  print "no serial port; skipped"
fi

print "Dashboard smoke checks passed."
print "Manual next: Unlock → Save label → Enroll empty slot → toggle Idle ring light."
