#!/usr/bin/python3
"""Install the already-built official proxy on the Pi; never export credentials.

Build v0.4.1 as the unprivileged owner first, using the official Go module and
checksum database. This installer keeps the existing enrolled command key.
It performs only a local HTTPS health check, never a Tesla vehicle command.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import time
from urllib.request import urlopen

STATE = Path('/var/lib/sparc-controller')
BINARY = Path('/usr/local/lib/sparc/tesla-http-proxy')
CERT = STATE/'tesla-proxy-cert.pem'
TLS_KEY = STATE/'tesla-proxy-tls-key.pem'
UNIT = Path('/etc/systemd/system/sparc-tesla-proxy.service')
SERVICE = '''[Unit]
Description=SPARC signed Tesla charging connection
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
UMask=0077
CacheDirectory=sparc-tesla-proxy
CacheDirectoryMode=0700
Environment=TESLA_CACHE_FILE=/var/cache/sparc-tesla-proxy/sessions.json
ExecStart=/usr/local/lib/sparc/tesla-http-proxy -host 127.0.0.1 -port 4443 -timeout 7s -cert /var/lib/sparc-controller/tesla-proxy-cert.pem -tls-key /var/lib/sparc-controller/tesla-proxy-tls-key.pem -key-file /var/lib/sparc-controller/tesla-command-key.pem
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
MemoryMax=192M
TasksMax=64

[Install]
WantedBy=multi-user.target
'''


def run(*args):
    # Capture failures privately: external tools must not print key material.
    result = subprocess.run(args, capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError('Tesla proxy setup step failed: '+args[0])
    return result


def write(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Administrator access is required to install the Tesla connection.')
    data = args.binary.read_bytes()
    if hashlib.sha256(data).hexdigest() != args.sha256 or data[:4] != b'\x7fELF':
        raise SystemExit('The built proxy checksum did not match.')
    config_path, key_path = STATE/'tesla-client.json', STATE/'tesla-command-key.pem'
    for path in (config_path, key_path):
        if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
            raise SystemExit('Existing private Tesla setup requires owner-only permissions.')
    config = json.loads(config_path.read_text())
    if config.get('domain') != 'sparc.ipodeskai.com':
        raise SystemExit('Review the existing Tesla application before installing.')
    previous_key_sha = hashlib.sha256(key_path.read_bytes()).hexdigest()
    backup = Path('/var/tmp')/('sparc-tesla-proxy-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700)
    paths = [BINARY, CERT, TLS_KEY, UNIT, config_path]
    previous = {path: path.exists() for path in paths}
    active = subprocess.run(['systemctl','is-active','--quiet','sparc-tesla-proxy']).returncode == 0
    enabled = subprocess.run(['systemctl','is-enabled','--quiet','sparc-tesla-proxy'],capture_output=True).returncode == 0
    for index, path in enumerate(paths):
        if previous[path]: shutil.copy2(path, backup/str(index))
    try:
        write(BINARY, data, 0o755)
        if not CERT.exists() and not TLS_KEY.exists():
            run('openssl','req','-x509','-nodes','-newkey','ec','-pkeyopt','ec_paramgen_curve:secp384r1',
                '-subj','/CN=localhost','-keyout',str(TLS_KEY),'-out',str(CERT),'-sha256','-days','3650',
                '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1',
                '-addext','extendedKeyUsage=serverAuth','-addext','basicConstraints=critical,CA:TRUE',
                '-addext','keyUsage=critical,digitalSignature,keyCertSign,keyAgreement')
            os.chmod(TLS_KEY,0o600); os.chmod(CERT,0o600)
        if not CERT.is_file() or not TLS_KEY.is_file():
            raise RuntimeError('The local proxy certificate and key must both be present.')
        config.update(commandProxyURL='https://localhost:4443',commandProxyCA=str(CERT),privateKeyPath=str(key_path))
        write(config_path, (json.dumps(config,separators=(',',':'))+'\n').encode())
        write(UNIT, SERVICE.encode(), 0o644)
        run('systemctl','daemon-reload')
        run('systemctl','enable','--now','sparc-tesla-proxy')
        run('systemctl','restart','sparc-tesla-proxy')
        context = ssl.create_default_context(cafile=str(CERT))
        for attempt in range(12):
            try:
                with urlopen('https://localhost:4443/health', context=context, timeout=3) as response:
                    if response.status == 200 and response.read(16).strip() == b'OK': break
            except OSError:
                if attempt == 11: raise RuntimeError('Local signed Tesla connection did not become healthy.') from None
                time.sleep(1)
        else:
            raise RuntimeError('The signed Tesla connection health response was invalid.')
        if hashlib.sha256(key_path.read_bytes()).hexdigest() != previous_key_sha:
            raise RuntimeError('The enrolled Tesla command key changed unexpectedly.')
        print('TESLA_PROXY_OK',json.dumps({'version':'v0.4.1','binarySha256':args.sha256,
            'localTLSVerified':True,'vehicleCommandsSent':0,'backup':str(backup)}),flush=True)
    except BaseException:
        subprocess.run(['systemctl','stop','sparc-tesla-proxy'],capture_output=True)
        for index,path in enumerate(paths):
            if previous[path]: shutil.copy2(backup/str(index),path)
            else: path.unlink(missing_ok=True)
        subprocess.run(['systemctl','daemon-reload'],capture_output=True)
        if not enabled: subprocess.run(['systemctl','disable','sparc-tesla-proxy'],capture_output=True)
        if active: subprocess.run(['systemctl','start','sparc-tesla-proxy'],capture_output=True)
        raise


if __name__ == '__main__':
    main()
