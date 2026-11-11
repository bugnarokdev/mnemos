"""Snapshot bytes written by one backend open on the other."""

import os

import pytest

from mnemos import chain

os.environ.setdefault("MNEMOS_NATIVE_AUTO", "0")


def _python_snapshot():
    keys = chain.derive_keys("cross-secret")
    nonce = bytes(range(24))
    entries = [
        {"id": "a", "kind": "fact", "session_id": "sess", "text": "silver-kelp", "ts": 1},
    ]
    plaintext = chain.canonical_plaintext(entries)
    blob = chain.seal(keys["enc_key"], plaintext, nonce)
    prev = bytes(32)
    content = chain.content_hash(prev, chain.sha256(blob), 1, "sess", "blob-id")
    signature = chain.sign_hash(keys["sign_seed"], content)
    return keys, plaintext, blob, content, signature


def test_python_snapshot_roundtrip():
    keys, plaintext, blob, content, signature = _python_snapshot()
    assert chain.open_blob(keys["enc_key"], blob) == plaintext
    assert chain.verify_hash(keys["pubkey"], content, signature)


@pytest.mark.skipif(
    os.environ.get("MNEMOS_BACKEND") == "python" and os.environ.get("MNEMOS_FORCE_NO_RUST") == "1",
    reason="rust module hidden",
)
def test_rust_opens_python_blob_and_hash_matches():
    pytest.importorskip("mnemos_core")
    import mnemos_core

    keys, plaintext, blob, content, signature = _python_snapshot()
    opened = mnemos_core.open_blob_text("cross-secret", blob.hex())
    assert opened == plaintext.decode("utf-8")
    rust_hash = mnemos_core.content_hash_hex(
        "00" * 32,
        chain.sha256(blob).hex(),
        1,
        "sess",
        "blob-id",
    )
    assert rust_hash == content.hex()
    assert chain.verify_hash(keys["pubkey"], bytes.fromhex(rust_hash), signature)


def test_python_opens_rust_blob():
    pytest.importorskip("mnemos_core")
    import mnemos_core

    if not hasattr(mnemos_core, "seal_blob_hex"):
        pytest.skip("installed mnemos_core has no seal_blob_hex")
    keys, plaintext, _blob, _content, _signature = _python_snapshot()
    nonce = bytes(range(24))
    sealed = mnemos_core.seal_blob_hex("cross-secret", plaintext.decode("utf-8"), nonce.hex())
    assert chain.open_blob(keys["enc_key"], bytes.fromhex(sealed)) == plaintext


def test_store_written_by_one_backend_recalls_on_the_other(tmp_path):
    pytest.importorskip("mnemos_core")
    import mnemos_core

    db = tmp_path / "mnemos.db"
    secret = "cross-secret"
    config = "{}"
    rust = mnemos_core.Engine(str(db), secret, config)
    try:
        rust.remember("silver-kelp", "sess", "fact")
    finally:
        rust.close()
    from mnemos.py_engine import Engine

    python = Engine(str(db), secret, {}, reason="cross")
    try:
        hits = python.recall("kelp", limit=5)["hits"]
        assert any("silver-kelp" in hit["text"] for hit in hits)
        python.remember("python-side", "sess", "fact")
    finally:
        python.close()
    rust = mnemos_core.Engine(str(db), secret, config)
    try:
        payload = rust.recall("python-side", 5)
    finally:
        rust.close()
    assert "python-side" in payload
