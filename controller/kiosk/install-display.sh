#!/bin/sh
# Install the SPARC touchscreen: it starts by itself whenever a display is plugged
# in. Identity, Wi-Fi, Bluetooth pairing and schedules are left as they are.
# Run from the bundle folder that holds controller-source.tgz and its checksum.
set -eu
test "$(id -u)" = 0 || { echo 'Run this with sudo.'; exit 1; }
bundle_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
identity=/var/lib/sparc-controller/identity.json
conf=/etc/sparc-controller/ble.conf
python=/opt/sparc-controller/.venv-controller/bin/python
kiosk=/usr/local/lib/sparc-display
identity_before=$(sha256sum "$identity" | cut -d ' ' -f 1)
cd "$bundle_dir"
sha256sum --check controller-source.sha256
staging=$(mktemp -d /var/tmp/sparc-display.XXXXXX)
trap 'rm -rf "$staging"' EXIT
cp "$conf" "$staging/ble.conf.before"
tar --no-same-owner --warning=no-unknown-keyword -xzf controller-source.tgz -C "$staging"
(cd "$staging" && "$python" -m unittest discover -s controller/tests)

echo 'Installing the kiosk browser. This can take several minutes.'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends cage chromium fonts-inter wlr-randr curl

backup=/var/backups/sparc-controller/source-$(date -u +%Y%m%dT%H%M%SZ).tgz
tar -czf "$backup" -C /opt/sparc-controller controller
rollback() {
    result=$?
    if [ "$result" -ne 0 ]; then
        systemctl disable --now sparc-display.service 2>/dev/null || true
        rm -f /etc/systemd/system/sparc-display.service /etc/systemd/system/sparc-display-hotplug.service \
            /etc/udev/rules.d/99-sparc-display.rules /etc/pam.d/sparc-display
        rm -rf "$kiosk" /opt/sparc-controller/controller
        tar --no-same-owner -xzf "$backup" -C /opt/sparc-controller
        install -m 0644 /opt/sparc-controller/controller/sparc-controller-app-test.service /etc/systemd/system/ || true
        cp "$staging/ble.conf.before" "$conf"
        systemctl daemon-reload
        udevadm control --reload-rules || true
        systemctl restart sparc-controller-app-test.service || true
        echo 'Touchscreen install failed; the previous controller was restored.'
    fi
    rm -rf "$staging"
}
trap rollback EXIT
tar --no-same-owner --warning=no-unknown-keyword -xzf controller-source.tgz -C /opt/sparc-controller
src=/opt/sparc-controller/controller/kiosk

id sparc-display >/dev/null 2>&1 || useradd --create-home --home-dir /var/lib/sparc-display --shell /usr/sbin/nologin --user-group sparc-display
usermod -a -G video,render,input sparc-display
install -d -m 0755 "$kiosk"
install -m 0755 "$src/display-connected" "$src/start-kiosk" "$src/session" "$src/hotplug" "$kiosk/"
install -m 0644 "$src/sparc-display.pam" /etc/pam.d/sparc-display
install -m 0644 "$src/sparc-display.service" "$src/sparc-display-hotplug.service" /etc/systemd/system/
install -m 0644 "$src/99-sparc-display.rules" /etc/udev/rules.d/
# The controller's own unit travels with its code (it now allows Wi-Fi setup over Bluetooth).
install -m 0644 /opt/sparc-controller/controller/sparc-controller-app-test.service /etc/systemd/system/
test -e /etc/sparc-controller/display.conf || install -m 0644 "$src/display.conf" /etc/sparc-controller/display.conf
grep -q '^SPARC_DISPLAY_PORT=' "$conf" || printf 'SPARC_DISPLAY_PORT=8767\n' >> "$conf"
systemctl daemon-reload
udevadm control --reload-rules
systemctl restart sparc-controller-app-test.service

for _ in $(seq 1 20); do
    curl -fsS -o /dev/null --max-time 1 http://127.0.0.1:8767/healthz && break
    sleep 1
done
curl -fsS --max-time 3 http://127.0.0.1:8767/api/state | grep -q '"ok":true'
curl -fsS --max-time 3 http://127.0.0.1:8767/ | grep -q '<title>SPARC</title>'
test "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: attacker.example' http://127.0.0.1:8767/api/state)" = 421
echo 'The screen page answers on this Pi only.'
busctl get-property org.bluez /org/bluez/hci0 org.bluez.LEAdvertisingManager1 ActiveInstances | grep -q 'y [1-9]'
test "$identity_before" = "$(sha256sum "$identity" | cut -d ' ' -f 1)"
echo 'Bluetooth advertising is on. Controller identity unchanged.'

# Render the page once in the kiosk browser, as the kiosk user, to prove it runs here.
if runuser -u sparc-display -- env HOME=/var/lib/sparc-display timeout 120 chromium --headless=new --no-first-run \
        --virtual-time-budget=10000 --dump-dom http://127.0.0.1:8767/ > "$staging/dom.html" 2> "$staging/chromium.log" \
        && grep -q 'data-ready="true"' "$staging/dom.html"; then
    echo 'Chromium rendered the SPARC screen.'
else
    echo 'WARNING: Chromium could not render the screen in a test run:'
    tail -n 15 "$staging/chromium.log" || true
fi

systemctl enable sparc-display.service
if "$kiosk/display-connected"; then
    systemctl restart sparc-display.service
    echo 'A display is connected: the SPARC screen is starting now.'
else
    echo 'No display connected yet. Plug one in and the SPARC screen starts by itself.'
fi
