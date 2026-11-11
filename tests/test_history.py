import hashlib

from mnemos import chain, history, native_wheel


def _build(secret="harbor-secret", count=3):
    keys = chain.derive_keys(secret)
    blobs = {}
    snaps = []
    prev = chain.GENESIS_PREV
    for version in range(1, count + 1):
        plaintext = chain.canonical_plaintext(
            [{"id": f"e{version}", "kind": "fact", "session_id": "s", "text": f"fact {version}", "ts": version}]
        )
        blob = chain.seal(keys["enc_key"], plaintext, bytes([version]) * 24)
        blob_id = f"blob-{version}"
        blobs[blob_id] = blob
        content = chain.content_hash(prev, chain.sha256(blob), version, "s", blob_id)
        snaps.append(
            {
                "version": version,
                "blob_id": list(blob_id.encode()),
                "session_id": list(b"s"),
                "content_hash": "0x" + content.hex(),
                "prev_hash": list(prev),
                "signature": chain.sign_hash(keys["sign_seed"], content).hex(),
            }
        )
        prev = content
    contents = {"pubkey": keys["pubkey"].hex(), "head": prev.hex(), "version": count, "snapshots": snaps}
    return contents, blobs


def _verify(contents, blobs):
    return history.verify_history(history.parse_index(contents), lambda blob_id: blobs[blob_id])


def test_clean_history_verifies():
    contents, blobs = _build()
    report = _verify(contents, blobs)
    assert report["ok"], report
    assert [row["ok"] for row in report["rows"]] == [True, True, True]
    assert report["verdict"] == "OK: 3 versions verified"
    listed = history.list_history(history.parse_index(contents))
    assert [row["version"] for row in listed] == [1, 2, 3]


def test_empty_history_verifies():
    contents = {"pubkey": "00" * 32, "head": [], "version": 0, "snapshots": []}
    assert _verify(contents, {})["ok"]


def test_tampered_blob_is_detected():
    contents, blobs = _build()
    blob = bytearray(blobs["blob-2"])
    blob[-1] ^= 1
    blobs["blob-2"] = bytes(blob)
    report = _verify(contents, blobs)
    assert not report["ok"]
    assert [row["ok"] for row in report["rows"]] == [True, False, True]
    assert "content hash" in report["rows"][1]["reason"]
    assert report["verdict"].startswith("BROKEN")


def test_forged_signature_is_detected():
    contents, blobs = _build()
    other = chain.derive_keys("someone-else")
    content = bytes.fromhex(contents["snapshots"][2]["content_hash"][2:])
    contents["snapshots"][2]["signature"] = chain.sign_hash(other["sign_seed"], content).hex()
    report = _verify(contents, blobs)
    assert not report["ok"]
    assert "signature" in report["rows"][2]["reason"]


def test_broken_link_and_version_gap_are_detected():
    contents, blobs = _build()
    contents["snapshots"][1]["prev_hash"] = list(bytes(32))
    report = _verify(contents, blobs)
    assert not report["rows"][1]["ok"]
    assert "prev_hash" in report["rows"][1]["reason"]

    contents, blobs = _build()
    del contents["snapshots"][1]
    report = _verify(contents, blobs)
    assert not report["ok"]
    assert "expected 2" in report["rows"][1]["reason"]


def test_head_mismatch_and_missing_blob_are_detected():
    contents, blobs = _build()
    contents["head"] = "11" * 32
    report = _verify(contents, blobs)
    assert not report["ok"] and all(row["ok"] for row in report["rows"])

    contents, blobs = _build()
    del blobs["blob-1"]
    report = _verify(contents, blobs)
    assert not report["rows"][0]["ok"] and "Walrus" in report["rows"][0]["reason"]


def test_native_download_is_opt_in(monkeypatch, tmp_path):
    from mnemos import engine

    called = []
    monkeypatch.delenv("MNEMOS_NATIVE_AUTO", raising=False)
    monkeypatch.delenv("MNEMOS_BACKEND", raising=False)
    monkeypatch.setattr(native_wheel, "try_install", lambda: called.append(1) or "x")
    import sys

    monkeypatch.setitem(sys.modules, "mnemos_core", None)
    opened = engine.open_engine(str(tmp_path / "db.sqlite"), "harbor-secret", {})
    try:
        assert opened.backend == "python"
        assert "opt-in" in opened.reason
        assert called == []
    finally:
        opened.close()


def test_wheel_checksums_are_pinned(monkeypatch):
    assert set(native_wheel.PINNED_SHA256) == {
        f"mnemos_core-0.2.0-cp311-abi3-{tag}.whl"
        for tag in (
            "macosx_10_12_x86_64",
            "macosx_11_0_arm64",
            "manylinux_2_34_aarch64",
            "manylinux_2_34_x86_64",
            "win_amd64",
        )
    }
    urls = []
    monkeypatch.setenv("MNEMOS_NATIVE_BASE", "https://example.invalid")
    monkeypatch.setattr(native_wheel, "_wheel_name", lambda: "mnemos_core-0.2.0-cp311-abi3-win_amd64.whl")
    monkeypatch.setattr(native_wheel, "_get", lambda url, timeout: urls.append(url) or b"not the wheel")
    reason = native_wheel.try_install()
    assert reason == "native wheel checksum did not match"
    assert urls == [native_wheel.DEFAULT_RELEASE + "/mnemos_core-0.2.0-cp311-abi3-win_amd64.whl"]
    assert hashlib.sha256(b"not the wheel").hexdigest() not in native_wheel.PINNED_SHA256.values()


def test_package_id_is_recovered_from_index_type(monkeypatch, tmp_path):
    from mnemos import py_engine

    assert py_engine.package_id_from_type("0xabc::memory_index::MemoryIndex") == "0xabc"
    assert py_engine.package_id_from_type("0x2::coin::Coin") == ""
    obj = {"asMoveObject": {"contents": {"type": {"repr": "0xfeed::memory_index::MemoryIndex"}, "json": {}}}}
    monkeypatch.setattr(py_engine, "graphql_object", lambda url, oid, timeout=8: obj)
    config = py_engine.load_file_config(tmp_path)
    config["index_object_id"] = "0x1"
    assert py_engine.ensure_package_id(tmp_path, config) == "0xfeed"
    assert py_engine.load_file_config(tmp_path)["package_id"] == "0xfeed"
