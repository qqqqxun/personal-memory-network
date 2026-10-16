from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import DATA_DIR, KEYRING_USER, SERVICE_NAME, TEST_MODE, ensure_data_dir

PREFIX = "enc:v1:"


def _decode_key(raw: str) -> bytes:
    text = raw.strip()
    if len(text) == 64:
        return bytes.fromhex(text)
    return bytes.fromhex(text.encode("utf-8").hex()[:64].ljust(64, "0"))


def _load_or_create_key() -> bytes:
    env = os.environ.get("MEMORY_NET_MASTER_KEY")
    if env:
        if len(env) == 64 and all(c in "0123456789abcdefABCDEF" for c in env):
            return bytes.fromhex(env)
        # Allow any long secret; derive 32 bytes via repeat/truncate after utf-8.
        blob = env.encode("utf-8")
        return (blob + b"\x00" * 32)[:32]

    if TEST_MODE:
        return bytes.fromhex("ab" * 32)

    ensure_data_dir()
    key_file = DATA_DIR / ".master_key"
    try:
        import keyring

        stored = keyring.get_password(SERVICE_NAME, KEYRING_USER)
        if stored:
            return bytes.fromhex(stored)
        generated = secrets.token_hex(32)
        keyring.set_password(SERVICE_NAME, KEYRING_USER, generated)
        if not key_file.exists():
            key_file.write_text(generated, encoding="utf-8")
        return bytes.fromhex(generated)
    except Exception:
        if key_file.exists():
            return bytes.fromhex(key_file.read_text(encoding="utf-8").strip())
        generated = secrets.token_hex(32)
        key_file.write_text(generated, encoding="utf-8")
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return bytes.fromhex(generated)


class Cipher:
    def __init__(self, key: bytes | None = None) -> None:
        self.key = key or _load_or_create_key()
        if len(self.key) != 32:
            raise ValueError("master key must be 32 bytes")
        self._aes = AESGCM(self.key)

    def encrypt(self, plaintext: str | None) -> str | None:
        if plaintext is None:
            return None
        if plaintext.startswith(PREFIX):
            return plaintext
        nonce = os.urandom(12)
        token = nonce + self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return PREFIX + token.hex()

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(PREFIX):
            return value
        raw = bytes.fromhex(value[len(PREFIX) :])
        nonce, ct = raw[:12], raw[12:]
        return self._aes.decrypt(nonce, ct, None).decode("utf-8")

    def encrypt_bytes(self, data: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aes.encrypt(nonce, data, None)

    def decrypt_bytes(self, data: bytes) -> bytes:
        return self._aes.decrypt(data[:12], data[12:], None)


def token_path() -> Path:
    ensure_data_dir()
    return DATA_DIR / "token.txt"


def load_or_create_auth_token() -> str:
    env = os.environ.get("MEMORY_NET_TOKEN")
    if env:
        return env
    if TEST_MODE:
        return "test-token"
    path = token_path()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token
