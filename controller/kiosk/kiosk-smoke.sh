#!/bin/sh
# Dry run without a display: start the installed screen service on a virtual
# output, then ask the live page what it shows through Chromium's local
# debugging port. Everything it changes lives in /run and is put back.
set -u
test "$(id -u)" = 0 || { echo 'Run this with sudo.'; exit 1; }
dropin=/run/systemd/system/sparc-display.service.d
mkdir -p "$dropin"
cat > "$dropin/smoke.conf" <<'EOF'
[Service]
ExecCondition=
ExecCondition=/bin/true
ExecStartPre=
Environment=WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 WLR_HEADLESS_OUTPUTS=1 SPARC_DISPLAY_CHROMIUM_FLAGS=--remote-debugging-port=9222
EOF
systemctl daemon-reload
systemctl start sparc-display.service
sleep 25
echo "== service $(systemctl is-active sparc-display.service), restarts $(systemctl show -p NRestarts --value sparc-display.service), chromium processes $(pgrep -c -u sparc-display chromium)"
/opt/sparc-controller/.venv-controller/bin/python - <<'PY'
import base64, json, os, socket, struct, time, urllib.request

for _ in range(20):
    try:
        targets = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=2))
        break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("== Chromium's debugging port did not answer.")
page = next(t for t in targets if t.get("type") == "page")
print("== page:", page.get("title"), page.get("url"))
host_port, path = page["webSocketDebuggerUrl"][len("ws://"):].split("/", 1)
host, port = host_port.split(":")
sock = socket.create_connection((host, int(port)), timeout=10)
key = base64.b64encode(os.urandom(16)).decode()
sock.sendall(f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
head = b""
while b"\r\n\r\n" not in head:
    head += sock.recv(1)

def exact(n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise SystemExit("== Debugging connection closed.")
        data += chunk
    return data

def send(message):
    data, mask = json.dumps(message).encode(), os.urandom(4)
    size = len(data)
    header = bytes([0x81, 0x80 | size]) if size < 126 else bytes([0x81, 0x80 | 126]) + struct.pack(">H", size)
    sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

def receive():
    size = exact(2)[1] & 0x7F
    if size == 126:
        size = struct.unpack(">H", exact(2))[0]
    elif size == 127:
        size = struct.unpack(">Q", exact(8))[0]
    return json.loads(exact(size))

def evaluate(expression, ident):
    send({"id": ident, "method": "Runtime.evaluate", "params": {"expression": expression, "returnByValue": True}})
    while True:
        reply = receive()
        if reply.get("id") == ident:
            return json.loads(reply["result"]["result"]["value"])

probe = ("JSON.stringify({ready: document.documentElement.dataset.ready || null,"
         " chip: document.getElementById('chip-label').textContent,"
         " battery: document.getElementById('soc-int').textContent + document.getElementById('soc-dec').textContent,"
         " link: document.getElementById('link-label').textContent, size: [innerWidth, innerHeight],"
         " kiosk: document.documentElement.classList.contains('kiosk'),"
         " polls: performance.getEntriesByType('resource').filter(e => e.name.includes('/api/state')).length})")
first = evaluate(probe, 1)
time.sleep(6)
second = evaluate(probe, 2)
print("== page shows:", {k: v for k, v in second.items() if k != "polls"})
print("== state polls in 6 seconds:", second["polls"] - first["polls"])
PY
systemctl stop sparc-display.service
sleep 3
echo "== after stop: chromium processes $(pgrep -c -u sparc-display chromium)"
rm -rf "$dropin"
systemctl daemon-reload
systemctl start getty@tty1.service
echo "== restored: screen $(systemctl is-active sparc-display.service) and $(systemctl is-enabled sparc-display.service), console login $(systemctl is-active getty@tty1.service)"
