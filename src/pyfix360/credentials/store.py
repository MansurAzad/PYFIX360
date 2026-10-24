"""Credential store.

API keys are NEVER stored in the database. The preferred backend is the
system keyring (Windows Credential Manager / macOS Keychain / SecretService).
If no keyring backend is usable, credentials are stored in an encrypted file
using a machine-derived key — and the weakness is surfaced to the caller so
the UI can warn the user.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from hashlib import pbkdf2_hmac
from pathlib import Path

from pyfix360.config import get_config

logger = logging.getLogger(__name__)

_SERVICE = "pyfix360"
_keyring = None
_keyring_error = ""


def _load_keyring():
    global _keyring, _keyring_error
    if _keyring is None and _keyring_error == "":
        try:
            import keyring

            kr = keyring.get_keyring()
            if getattr(kr, "priority", 0) and kr.__class__.__name__ != "fail.Keyring":
                _keyring = keyring
            else:
                _keyring_error = "no usable system keyring backend"
        except Exception as exc:  # pragma: no cover - depends on host
            _keyring_error = str(exc)
    return _keyring


@dataclass
class StoreInfo:
    backend: str          # "keyring" | "file-encrypted"
    warning: str = ""


def store_info() -> StoreInfo:
    if _load_keyring() is not None:
        return StoreInfo("keyring")
    return StoreInfo("file-encrypted",
                     "System keyring unavailable; credentials are encrypted at rest "
                     "with a machine-derived key (weaker than an OS keychain).")


def _xor_key() -> bytes:
    machine = "|".join([
        sys.platform, os.environ.get("COMPUTERNAME", ""),
        os.environ.get("USERNAME", ""), str(Path.home()),
    ])
    salt = b"pyfix360-credential-v1"
    return pbkdf2_hmac("sha256", machine.encode(), salt, 200_000, dklen=32)


def _vault_path() -> Path:
    return get_config().data_dir / "credentials.vault"


def _file_store(name: str, secret: str | None) -> str | None:
    path = _vault_path()
    vault: dict[str, str] = {}
    if path.is_file():
        try:
            raw = base64.b64decode(path.read_bytes())
            key = _xor_key()
            dec = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
            vault = json.loads(dec.decode("utf-8"))
        except Exception:
            vault = {}
    if secret is None:
        val = vault.get(name)
        if val is None:
            return None
        key = _xor_key()
        raw = base64.b64decode(val)
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8")
    if secret == "":
        vault.pop(name, None)
    else:
        key = _xor_key()
        data = secret.encode("utf-8")
        enc = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        vault[name] = base64.b64encode(enc).decode()
    payload = json.dumps(vault).encode("utf-8")
    key = _xor_key()
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(payload))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64encode(out))
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return None


def set_credential(name: str, secret: str) -> StoreInfo:
    kr = _load_keyring()
    if kr is not None:
        try:
            kr.set_password(_SERVICE, name, secret)
            return store_info()
        except Exception:
            pass
    _file_store(name, secret)
    return store_info()


def get_credential(name: str) -> str | None:
    kr = _load_keyring()
    if kr is not None:
        try:
            val = kr.get_password(_SERVICE, name)
            if val is not None:
                return val
        except Exception:
            logger.debug("keyring read failed for %s; falling back to file vault", name)
    return _file_store(name, None)


def delete_credential(name: str) -> None:
    kr = _load_keyring()
    if kr is not None:
        with contextlib.suppress(Exception):
            kr.delete_password(_SERVICE, name)
    _file_store(name, "")


def fingerprint(secret: str) -> str:
    """Non-reversible identifier for a stored secret (never the secret itself)."""
    return hashlib.sha256(secret.encode()).hexdigest()[:12]
