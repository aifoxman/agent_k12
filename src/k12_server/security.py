"""Small, dependency-free password and bearer token primitives."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from typing import Any


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_text, digest_text = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(digest_text)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, binascii.Error):
        return False


def make_token(secret: str, role: str, subject: str, ttl_seconds: int = 604800) -> str:
    body = {"role": role, "sub": subject, "exp": int(time.time()) + ttl_seconds}
    payload = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).rstrip(b"=")
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{payload.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def read_token(secret: str, token: str) -> dict[str, Any] | None:
    try:
        payload_text, signature_text = token.split(".", 1)
        payload = payload_text.encode()
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
        if not hmac.compare_digest(expected, actual):
            return None
        body = json.loads(base64.urlsafe_b64decode(payload + b"=" * (-len(payload) % 4)))
        if body["exp"] <= time.time() or body["role"] not in {"parent", "child", "setup"}:
            return None
        return body
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, binascii.Error):
        return None
