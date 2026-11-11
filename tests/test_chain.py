import json
import os

import pytest

from mnemos import chain
from mnemos.py_engine import Engine


def test_hkdf_and_signature_roundtrip():
    keys = chain.derive_keys("harbor-secret")
    assert len(keys["enc_key"]) == 32
    assert len(keys["pubkey"]) == 32
    assert keys["sui_address"].startswith("0x")
    assert len(keys["sui_address"]) == 66
    prev = bytes(32)
    blob = b"ciphertext-bytes"
    digest = chain.content_hash(prev, chain.sha256(blob), 1, "sess", "blob-1")
    signature = chain.sign_hash(keys["sign_seed"], digest)
    assert chain.verify_hash(keys["pubkey"], digest, signature)
    assert not chain.verify_hash(keys["pubkey"], bytes(32), signature)


def test_seal_matches_xchacha_layout():
    keys = chain.derive_keys("harbor-secret")
    nonce = bytes(range(24))
    blob = chain.seal(keys["enc_key"], b'{"entries":[],"v":1}', nonce)
    assert blob.startswith(b"MNMS")
    assert blob[4] == 1
    assert chain.open_blob(keys["enc_key"], blob) == b'{"entries":[],"v":1}'
    with pytest.raises(Exception):
        chain.open_blob(keys["enc_key"], blob[:-1] + bytes([blob[-1] ^ 1]))


def test_canonical_plaintext_is_sorted():
    raw = chain.canonical_plaintext(
        [
            {"id": "b", "kind": "fact", "session_id": "s", "text": "two", "ts": 2},
            {"id": "a", "kind": "fact", "session_id": "s", "text": "one", "ts": 1},
        ]
    )
    parsed = json.loads(raw)
    assert [row["id"] for row in parsed["entries"]] == ["a", "b"]
    assert list(parsed.keys()) == ["entries", "v"]


def test_local_recall_and_checkpoint_fails_closed(tmp_path):
    engine = Engine(str(tmp_path / "mnemos.sqlite"), "harbor-secret", {"index_object_id": ""})
    try:
        remembered = engine.remember("The harbor passphrase is silver-kelp.", "sess-a", "fact")
        assert remembered["id"]
        hits = engine.recall("harbor passphrase", 5)["hits"]
        assert hits and "silver-kelp" in hits[0]["text"]
        assert engine.recall("unrelated-zebra", 5)["hits"] == []
        with pytest.raises(RuntimeError, match="index object"):
            engine.checkpoint(
                [{"kind": "checkpoint", "text": "user: hello", "session_id": "sess-a"}],
                "idem",
                "sess-a",
            )
        report = engine.doctor()
        assert report["backend"] == "python"
        assert report["ok"] is True
    finally:
        engine.close()


def test_content_hash_is_stable():
    first = chain.content_hash(bytes(32), bytes(range(32)), 1, "alpha", "blob")
    second = chain.content_hash(bytes(32), bytes(range(32)), 1, "alpha", "blob")
    assert first == second
    assert first != chain.content_hash(bytes(32), bytes(range(32)), 2, "alpha", "blob")


@pytest.mark.skipif(os.environ.get("MNEMOS_REQUIRE_RUST") != "1", reason="native module optional")
def test_rust_content_hash_matches_python():
    import mnemos_core

    prev = bytes(32).hex()
    blob_sha = bytes(range(32)).hex()
    rust = mnemos_core.content_hash_hex(prev, blob_sha, 1, "alpha", "blob")
    python = chain.content_hash(bytes(32), bytes(range(32)), 1, "alpha", "blob").hex()
    assert rust == python
