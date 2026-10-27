#!/usr/bin/env python3
"""Verify the installed interpreter, D-Bus import and actual crypto operations."""
from __future__ import annotations

import importlib.metadata
import os
import platform
import sys


def main() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit("SPARC requires Python 3.11 or later.")
    try:
        import cryptography
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from dbus_next.aio import MessageBus
    except ImportError:
        raise SystemExit("Missing dependency: install python3-cryptography and python3-dbus-next, then use the --system-site-packages venv described in controller/README.md.") from None

    major = int(cryptography.__version__.split(".")[0])
    if not 43 <= major < 47:
        raise SystemExit("Use the supported cryptography range >=43,<47; keep Debian security updates installed.")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"SPARC runtime check", info=b"c2d").derive(os.urandom(32))
    cipher = ChaCha20Poly1305(key)
    nonce, aad, message = os.urandom(12), b"runtime test", b"SPARC"
    sealed = cipher.encrypt(nonce, message, aad)
    if cipher.decrypt(nonce, sealed, aad) != message:
        raise SystemExit("Authenticated encryption round trip failed.")
    try:
        cipher.decrypt(nonce, sealed[:-1] + bytes([sealed[-1] ^ 1]), aad)
    except InvalidTag:
        pass
    else:
        raise SystemExit("Authenticated encryption did not reject tampering.")
    print(f"Python {platform.python_version()} on {platform.machine()}")
    print(f"cryptography {cryptography.__version__}; dbus-next {importlib.metadata.version('dbus-next')}")
    print("HKDF-SHA256, ChaCha20-Poly1305, tamper rejection and D-Bus imports passed.")
    print("This does not test Bluetooth, NetworkManager, the router or a physical iPhone.")


if __name__ == "__main__":
    main()
