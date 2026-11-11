"""Argon2id key derivation: fixed vectors, legacy keys, and cross-backend agreement."""

import json
import os

import pytest

from mnemos import chain
from mnemos.py_engine import Engine

os.environ.setdefault("MNEMOS_NATIVE_AUTO", "0")

SECRET = "correct horse battery staple"
# Same vectors as native/src/chain.rs tests.
VECTOR = {
    "master": "d781c179861b596924a45be2ea53f74b738b43c16d3dfdeba5eda6f734936949",
    "enc_key": "bedd094c73fa21baf68b321e3e0b4247977b90575b1974ca1a9b0a671f1c583d",
    "sign_seed": "6e038151dd79c80e9bcd0c3da4e0bc84c609de82374c1b5f693b01e6ed4ce894",
    "sui_seed": "dd1172ec73161443121bad1b25f48bc072171429cd9d1d5198024c6ca02dac48",
    "pubkey": "0190f8e2c96add33e0f4beb5349a084acd5404b7cd94d49ba624def453bec26f",
    "sui_address": "0x2ea7a84f5f226a7335cf0664e50574bbf701711cb5665ee1b426924efd665a83",
}
LEGACY = {
    "enc_key": "48ab39ef7bd121ec437a9cc62900ff1864fc78ab24437c573a3e2789fc9ec45a",
    "pubkey": "fe1a2632754a7e0328d0b425eb6a51b235cc753d6f0a9ce6addb144aeea07460",
}


def test_argon2id_parameters():
    assert (chain.KDF_MEMORY_KIB, chain.KDF_TIME_COST, chain.KDF_PARALLELISM) == (65536, 3, 1)
    assert chain.KDF_SALT == b"mnemos-kdf-v2/argon2id"


def test_argon2id_vectors():
    keys = chain.derive_keys(SECRET)
    assert chain.master_key(SECRET).hex() == VECTOR["master"]
    for name in ("enc_key", "sign_seed", "sui_seed", "pubkey"):
        assert keys[name].hex() == VECTOR[name], name
    assert keys["sui_address"] == VECTOR["sui_address"]
    assert keys["kdf"] == "argon2id"


def test_legacy_vectors_are_the_old_hkdf():
    keys = chain.derive_legacy_keys(SECRET)
    assert keys["enc_key"].hex() == LEGACY["enc_key"]
    assert keys["pubkey"].hex() == LEGACY["pubkey"]
    assert keys["pubkey"] != chain.derive_keys(SECRET)["pubkey"]


def test_master_key_is_cached(monkeypatch):
    import argon2.low_level

    chain.master_key("cache-secret")
    monkeypatch.setattr(argon2.low_level, "hash_secret_raw", lambda *a, **k: pytest.fail("argon2 ran twice"))
    chain.master_key("cache-secret")


def test_rust_derives_the_same_keys():
    core = pytest.importorskip("mnemos_core")
    if not hasattr(core, "public_identity"):
        pytest.skip("installed mnemos_core predates Argon2id")
    ident = json.loads(core.public_identity(SECRET))
    assert ident["pubkey_hex"] == VECTOR["pubkey"]
    assert ident["sui_address"] == VECTOR["sui_address"]
    assert ident["legacy_pubkey_hex"] == LEGACY["pubkey"]


def _legacy_index(secret, tmp_path, monkeypatch):
    """A one-version MemoryIndex sealed and signed with the legacy keys."""
    from mnemos import py_engine

    legacy = chain.derive_legacy_keys(secret)
    entries = [{"id": "e1", "kind": "fact", "session_id": "s", "text": "amber-fjord", "ts": 1}]
    blob = chain.seal(legacy["enc_key"], chain.canonical_plaintext(entries), bytes(24))
    content = chain.content_hash(bytes(32), chain.sha256(blob), 1, "s", "blob-legacy")
    sig = chain.sign_hash(legacy["sign_seed"], content)
    contents = {
        "pubkey": legacy["pubkey"].hex(),
        "head": content.hex(),
        "version": 1,
        "snapshots": [
            {
                "blob_id": b"blob-legacy".hex(),
                "session_id": b"s".hex(),
                "content_hash": content.hex(),
                "prev_hash": bytes(32).hex(),
                "signature": sig.hex(),
                "version": 1,
            }
        ],
    }
    from mnemos import reads

    obj = {"asMoveObject": {"contents": {"type": {"repr": "0xabc::memory_index::MemoryIndex"}, "json": contents}}}
    monkeypatch.setattr(reads, "get_object", lambda config, oid, timeout=20: obj)
    monkeypatch.setattr(py_engine, "walrus_get", lambda agg, blob_id, timeout=60: blob)
    return contents


def test_legacy_index_restores_read_only(tmp_path, monkeypatch):
    from mnemos import py_chain, sui_client

    contents = _legacy_index(SECRET, tmp_path, monkeypatch)
    engine = Engine(str(tmp_path / "db.sqlite"), SECRET, {"index_object_id": "0x1", "package_id": "0x2"})
    try:
        result = engine.restore("0x1")
        assert result["entries"] == 1 and result["kdf"] == "legacy" and result["read_only"] is True
        assert engine.recall("amber-fjord")["hits"][0]["text"] == "amber-fjord"
        engine.remember("new fact", "s")
        monkeypatch.setattr(sui_client, "read_move_json", lambda config, oid, check_package=False: contents)
        monkeypatch.setattr(sui_client, "check_rpc", lambda url: None)
        monkeypatch.setattr(sui_client, "execute", lambda *a, **k: pytest.fail("must not sign"))
        with pytest.raises(RuntimeError, match="read-only"):
            py_chain.flush(engine)
    finally:
        engine.close()


def test_wrong_secret_still_refused(tmp_path, monkeypatch):
    _legacy_index("someone-else", tmp_path, monkeypatch)
    engine = Engine(str(tmp_path / "db.sqlite"), SECRET, {})
    try:
        with pytest.raises(RuntimeError, match="different key"):
            engine.restore("0x1")
    finally:
        engine.close()
