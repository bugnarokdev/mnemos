"""Canonical snapshot bytes shared by the Python fallback and the native core.

A snapshot's content hash is SHA-256 over a versioned preimage that binds the
previous hash, the encrypted blob, the chain version, the session id, and the
Walrus blob id. The snapshot signature is Ed25519 over
``b"mnemos-snapshot-v1" || content_hash``. The encryption key and the signing
seed are HKDF-SHA256 outputs of the user secret. Plaintext never includes the
key.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

DOMAIN = b"mnemos-snapshot-v1"
PREIMAGE_MAGIC = b"MNEMOS1\x00"
BLOB_MAGIC = b"MNMS"
BLOB_VERSION = 1
BLOB_AAD = b"mnemos-blob-v1"
GENESIS_PREV = bytes(32)

SALT_ENC = b"mnemos-aead-v1"
INFO_ENC = b"xchacha20-poly1305"
SALT_SIGN = b"mnemos-sign-v1"
INFO_SIGN = b"ed25519-seed"
SALT_SUI = b"mnemos-sui-v1"
INFO_SUI = b"ed25519-seed"


def _hkdf(secret: str, salt: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(
        secret.encode("utf-8")
    )


def _rotl32(value: int, bits: int) -> int:
    return ((value << bits) & 0xFFFFFFFF) | (value >> (32 - bits))


def _quarter(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = _rotl32(state[d], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = _rotl32(state[b], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = _rotl32(state[d], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = _rotl32(state[b], 7)


def hchacha20(key: bytes, nonce16: bytes) -> bytes:
    """HChaCha20, the subkey step of XChaCha20 (no final addition of the input)."""
    if len(key) != 32 or len(nonce16) != 16:
        raise ValueError("HChaCha20 expects a 32-byte key and a 16-byte nonce")
    constant = b"expand 32-byte k"

    def u32(blob: bytes) -> int:
        return int.from_bytes(blob, "little")

    state = [
        u32(constant[0:4]),
        u32(constant[4:8]),
        u32(constant[8:12]),
        u32(constant[12:16]),
        u32(key[0:4]),
        u32(key[4:8]),
        u32(key[8:12]),
        u32(key[12:16]),
        u32(key[16:20]),
        u32(key[20:24]),
        u32(key[24:28]),
        u32(key[28:32]),
        u32(nonce16[0:4]),
        u32(nonce16[4:8]),
        u32(nonce16[8:12]),
        u32(nonce16[12:16]),
    ]
    for _ in range(10):
        _quarter(state, 0, 4, 8, 12)
        _quarter(state, 1, 5, 9, 13)
        _quarter(state, 2, 6, 10, 14)
        _quarter(state, 3, 7, 11, 15)
        _quarter(state, 0, 5, 10, 15)
        _quarter(state, 1, 6, 11, 12)
        _quarter(state, 2, 7, 8, 13)
        _quarter(state, 3, 4, 9, 14)
    words = state[0:4] + state[12:16]
    return b"".join(word.to_bytes(4, "little") for word in words)


def xchacha20poly1305_encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
    if len(nonce) != 24:
        raise ValueError("XChaCha20-Poly1305 nonce must be 24 bytes")
    subkey = hchacha20(key, nonce[:16])
    inner = b"\x00\x00\x00\x00" + nonce[16:]
    return ChaCha20Poly1305(subkey).encrypt(inner, plaintext, aad)


def xchacha20poly1305_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    if len(nonce) != 24:
        raise ValueError("XChaCha20-Poly1305 nonce must be 24 bytes")
    subkey = hchacha20(key, nonce[:16])
    inner = b"\x00\x00\x00\x00" + nonce[16:]
    return ChaCha20Poly1305(subkey).decrypt(inner, ciphertext, aad)


def derive_keys(secret: str) -> dict[str, Any]:
    if not secret:
        raise ValueError("Mnemos secret is empty")
    enc = _hkdf(secret, SALT_ENC, INFO_ENC)
    sign_seed = _hkdf(secret, SALT_SIGN, INFO_SIGN)
    sui_seed = _hkdf(secret, SALT_SUI, INFO_SUI)
    sign_key = Ed25519PrivateKey.from_private_bytes(sign_seed)
    pubkey = sign_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return {
        "enc_key": enc,
        "sign_seed": sign_seed,
        "sui_seed": sui_seed,
        "pubkey": pubkey,
        "pubkey_hex": pubkey.hex(),
        "sui_address": sui_address(ed25519_public_from_seed(sui_seed)),
    }


def ed25519_public_from_seed(seed: bytes) -> bytes:
    key = Ed25519PrivateKey.from_private_bytes(seed)
    return key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def sui_address(pubkey: bytes) -> str:
    digest = hashlib.blake2b(b"\x00" + pubkey, digest_size=32).digest()
    return "0x" + digest.hex()


def canonical_plaintext(entries: list[dict[str, Any]]) -> bytes:
    rows = []
    for entry in sorted(entries, key=lambda item: str(item["id"])):
        rows.append(
            {
                "id": str(entry["id"]),
                "kind": str(entry["kind"]),
                "session_id": str(entry["session_id"]),
                "text": str(entry["text"]),
                "ts": int(entry["ts"]),
            }
        )
    return json.dumps({"entries": rows, "v": 1}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def content_preimage(prev: bytes, blob_sha: bytes, version: int, session_id: str, blob_id: str) -> bytes:
    if len(prev) != 32 or len(blob_sha) != 32:
        raise ValueError("hash inputs must be 32 bytes")
    session = session_id.encode("utf-8")
    blob = blob_id.encode("utf-8")
    return (
        PREIMAGE_MAGIC
        + prev
        + blob_sha
        + int(version).to_bytes(8, "little")
        + len(session).to_bytes(4, "little")
        + session
        + len(blob).to_bytes(4, "little")
        + blob
    )


def content_hash(prev: bytes, blob_sha: bytes, version: int, session_id: str, blob_id: str) -> bytes:
    return hashlib.sha256(content_preimage(prev, blob_sha, version, session_id, blob_id)).digest()


def signing_message(content: bytes) -> bytes:
    if len(content) != 32:
        raise ValueError("content hash must be 32 bytes")
    return DOMAIN + content


def sign_hash(sign_seed: bytes, content: bytes) -> bytes:
    key = Ed25519PrivateKey.from_private_bytes(sign_seed)
    return key.sign(signing_message(content))


def verify_hash(pubkey: bytes, content: bytes, signature: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(pubkey).verify(signature, signing_message(content))
        return True
    except Exception:
        return False


def seal(enc_key: bytes, plaintext: bytes, nonce: bytes) -> bytes:
    if len(nonce) != 24:
        raise ValueError("nonce must be 24 bytes")
    body = xchacha20poly1305_encrypt(enc_key, nonce, plaintext, BLOB_AAD)
    return BLOB_MAGIC + bytes([BLOB_VERSION]) + nonce + body


def open_blob(enc_key: bytes, blob: bytes) -> bytes:
    if len(blob) < 4 + 1 + 24 + 16 or not blob.startswith(BLOB_MAGIC):
        raise ValueError("not a Mnemos blob")
    if blob[4] != BLOB_VERSION:
        raise ValueError(f"unsupported blob version {blob[4]}")
    nonce = blob[5:29]
    body = blob[29:]
    return xchacha20poly1305_decrypt(enc_key, nonce, body, BLOB_AAD)


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def as_bytes(value: Any) -> bytes:
    """Decode a GraphQL Move ``vector<u8>`` (int list, hex, or base64)."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("0x") or text.startswith("0X"):
            text = text[2:]
        try:
            if text and all(ch in "0123456789abcdefABCDEF" for ch in text) and len(text) % 2 == 0:
                return bytes.fromhex(text)
        except ValueError:
            pass
        import base64

        return base64.b64decode(text)
    if isinstance(value, list):
        if not value:
            return b""
        if isinstance(value[0], int):
            return bytes(int(item) & 0xFF for item in value)
        return b"".join(as_bytes(item) for item in value)
    raise TypeError(f"cannot decode bytes from {type(value).__name__}")
