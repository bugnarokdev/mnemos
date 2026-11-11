"""Chain writes for the Python backend. Snapshot bytes match ``mnemos.chain``."""

from __future__ import annotations

import base64
import json
import sys
import time
from typing import Any

from . import chain
from .py_engine import walrus_get, walrus_put


def _now() -> int:
    return int(time.time() * 1000)


def _meta(db, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _set_meta(db, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _parse_index(contents: dict[str, Any]) -> dict[str, Any]:
    pubkey = chain.as_bytes(contents.get("pubkey"))
    raw_head = contents.get("head")
    head = chain.GENESIS_PREV if raw_head in (None, "") else chain.as_bytes(raw_head)
    if not head:
        head = chain.GENESIS_PREV
    if len(head) != 32:
        raise RuntimeError("index head is not 32 bytes")
    snapshots = []
    for snap in contents.get("snapshots") or []:
        signature = chain.as_bytes(snap.get("signature"))
        snapshots.append(
            {
                "blob_id": chain.as_bytes(snap.get("blob_id")).decode("utf-8"),
                "session_id": chain.as_bytes(snap.get("session_id")).decode("utf-8"),
                "content_hash": chain.as_bytes(snap.get("content_hash")),
                "prev_hash": chain.as_bytes(snap.get("prev_hash")),
                "signature": signature,
                "version": int(snap.get("version")),
            }
        )
    snapshots.sort(key=lambda item: item["version"])
    return {
        "version": int(contents.get("version") or 0),
        "head": head,
        "pubkey": pubkey,
        "snapshots": snapshots,
    }


def _sui():
    from . import sui_client

    return sui_client


def read_chain_fresh(engine, index_id: str) -> dict[str, Any]:
    local = int(_meta(engine._db, "chain_version", "0") or "0")
    latest = None
    for attempt in range(8):
        contents = _sui().read_move_json(engine._config["graphql_url"], index_id)
        parsed = _parse_index(contents)
        latest = parsed
        if parsed["version"] >= local:
            return parsed
        if attempt < 7:
            time.sleep(1)
    if latest is None:
        raise RuntimeError(f"MemoryIndex {index_id} was not readable")
    return latest


def _unsynced(engine) -> list[dict[str, Any]]:
    rows = engine._db.execute(
        "SELECT id, session_id, kind, text, ts FROM entries WHERE snapshot_hash IS NULL ORDER BY ts ASC"
    ).fetchall()
    return [dict(row) for row in rows]


def _sui_seed(engine) -> bytes:
    explicit = str(engine._config.get("sui_private_key") or "").strip()
    if not explicit:
        return engine._keys["sui_seed"]
    raw = explicit.removeprefix("0x")
    try:
        decoded = bytes.fromhex(raw)
    except ValueError:
        decoded = b""
    if len(decoded) == 32:
        return decoded
    raise RuntimeError("MNEMOS_SUI_PRIVATE_KEY must be a 32-byte hex seed in the Python backend")


def flush(engine, idem_key: str | None = None) -> dict[str, Any]:
    index_id = str(engine._config.get("index_object_id") or "")
    package_id = str(engine._config.get("package_id") or "")
    if not index_id or not package_id:
        raise RuntimeError("index object is not configured; run `hermes mnemos init`")
    with engine._lock:
        head = read_chain_fresh(engine, index_id)
        if head["pubkey"] != engine._keys["pubkey"]:
            if head["pubkey"] == engine._legacy_keys["pubkey"]:
                raise RuntimeError(chain.LEGACY_READ_ONLY)
            raise RuntimeError("MemoryIndex was signed by a different key than this secret")
        entries = _unsynced(engine)
        if not entries:
            row = engine._db.execute(
                "SELECT blob_id FROM snapshots WHERE synced = 1 AND blob_id IS NOT NULL ORDER BY version DESC LIMIT 1"
            ).fetchone()
            return {
                "flushed": 0,
                "head": head["head"].hex(),
                "version": head["version"],
                "blob_id": row["blob_id"] if row else None,
            }
        # Refuse a non-testnet RPC before uploading anything. execute checks again.
        _sui().check_rpc(engine._config["sui_rpc"])
        session_id = entries[-1]["session_id"]
        plaintext = chain.canonical_plaintext(entries)
        plain_key = chain.sha256(plaintext).hex()
        idem = idem_key or plain_key
        pending = engine._db.execute(
            "SELECT content_hash, prev_hash, version, blob, blob_id, signature FROM snapshots WHERE idem_key = ? AND synced = 0 ORDER BY created_at DESC LIMIT 1",
            (idem,),
        ).fetchone()
        if pending and bytes.fromhex(pending["content_hash"]) == head["head"]:
            _mark(engine, pending["content_hash"], entries)
            return {
                "flushed": len(entries),
                "blob_id": pending["blob_id"],
                "content_hash": pending["content_hash"],
                "version": pending["version"],
                "reused": True,
            }
        if pending and bytes.fromhex(pending["prev_hash"]) != head["head"]:
            engine._db.execute("DELETE FROM snapshots WHERE content_hash = ? AND synced = 0", (pending["content_hash"],))
            engine._db.commit()
            pending = None
        if pending:
            blob = pending["blob"]
            blob_id = pending["blob_id"] or walrus_put(engine._config["walrus_publisher"], blob, int(engine._config["epochs"]))
            if not pending["blob_id"]:
                engine._db.execute(
                    "UPDATE snapshots SET blob_id = ? WHERE content_hash = ?",
                    (blob_id, pending["content_hash"]),
                )
            digest = _append(engine, package_id, index_id, blob_id, session_id, bytes.fromhex(pending["content_hash"]), bytes.fromhex(pending["prev_hash"]), bytes.fromhex(pending["signature"]), int(pending["version"]))
            _mark(engine, pending["content_hash"], entries)
            return {
                "flushed": len(entries),
                "blob_id": blob_id,
                "content_hash": pending["content_hash"],
                "version": pending["version"],
                "digest": digest,
            }
        version = head["version"] + 1
        nonce = chain_nonce()
        blob = chain.seal(engine._keys["enc_key"], plaintext, nonce)
        blob_id = walrus_put(engine._config["walrus_publisher"], blob, int(engine._config["epochs"]))
        content = chain.content_hash(head["head"], chain.sha256(blob), version, session_id, blob_id)
        signature = chain.sign_hash(engine._keys["sign_seed"], content)
        content_hex = content.hex()
        engine._db.execute(
            """
            INSERT INTO snapshots(content_hash, prev_hash, version, session_id, blob, blob_id, signature, idem_key, synced, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (content_hex, head["head"].hex(), version, session_id, blob, blob_id, signature.hex(), idem, _now()),
        )
        engine._db.commit()
        digest = _append(engine, package_id, index_id, blob_id, session_id, content, head["head"], signature, version)
        _mark(engine, content_hex, entries)
        _set_meta(engine._db, "chain_head", content_hex)
        _set_meta(engine._db, "chain_version", str(version))
        engine._db.commit()
        return {
            "flushed": len(entries),
            "blob_id": blob_id,
            "content_hash": content_hex,
            "version": version,
            "digest": digest,
            "head": content_hex,
        }


def chain_nonce() -> bytes:
    import secrets

    return secrets.token_bytes(24)


def checkpoint(engine, entries: list[dict[str, Any]], idem_key: str, session_id: str) -> dict[str, Any]:
    with engine._lock:
        row = engine._db.execute(
            "SELECT blob_id FROM snapshots WHERE idem_key = ? AND synced = 1 AND blob_id IS NOT NULL ORDER BY version DESC LIMIT 1",
            (idem_key,),
        ).fetchone()
        if row:
            return {"blob_id": row["blob_id"], "reused": True, "flushed": 0}
    for entry in entries:
        text = str(entry.get("text") or "")
        if text.strip():
            engine.remember(text, str(entry.get("session_id") or session_id), str(entry.get("kind") or "checkpoint"))
    return flush(engine, idem_key)


def _mark(engine, content_hash: str, entries: list[dict[str, Any]]) -> None:
    engine._db.execute("UPDATE snapshots SET synced = 1 WHERE content_hash = ?", (content_hash,))
    for entry in entries:
        engine._db.execute(
            "UPDATE entries SET snapshot_hash = ? WHERE id = ?",
            (content_hash, entry["id"]),
        )
    engine._db.commit()


def _append(engine, package_id: str, index_id: str, blob_id: str, session_id: str, content: bytes, prev: bytes, signature: bytes, version: int) -> str:
    sui = _sui()
    inputs = [
        sui.resolve_owned(engine._config["graphql_url"], index_id),
        sui._input_pure(sui.pure_vec_u8(blob_id.encode("utf-8"))),
        sui._input_pure(sui.pure_vec_u8(session_id.encode("utf-8"))),
        sui._input_pure(sui.pure_vec_u8(content)),
        sui._input_pure(sui.pure_vec_u8(prev)),
        sui._input_pure(sui.pure_vec_u8(signature)),
        sui._input_pure(sui.bcs_u64(version)),
    ]
    return sui.execute(
        engine._config["sui_rpc"],
        engine.sui_address(),
        inputs,
        [sui.move_call(package_id, "memory_index", "append", list(range(7)))],
        _sui_seed(engine),
        30_000_000,
    )


def init_chain(engine, modules_json: str) -> dict[str, Any]:
    from .faucet import fund_if_needed

    address = engine.sui_address()
    print(f"mnemos: sui address {address}", file=sys.stderr, flush=True)
    _sui().check_rpc(engine._config["sui_rpc"])
    funded = fund_if_needed(engine._config["faucet_url"], engine._config["graphql_url"], address, 200_000_000)
    print("mnemos: gas ready", file=sys.stderr, flush=True)
    if not engine._config.get("package_id"):
        print("mnemos: publishing MemoryIndex", file=sys.stderr, flush=True)
        parsed = json.loads(modules_json)
        modules = [base64.b64decode(item) for item in parsed["modules"]]
        dependencies = list(parsed["dependencies"])
        sui = _sui()
        inputs = [sui._input_pure(sui.bcs_address(address))]
        commands = [sui.publish_command(modules, dependencies), sui.transfer_command(0, 0)]
        digest = sui.execute(engine._config["sui_rpc"], address, inputs, commands, _sui_seed(engine), 100_000_000)
        package_id = _wait_package(engine, address)
        engine._config["package_id"] = package_id
        print(f"mnemos: package {package_id} ({digest})", file=sys.stderr, flush=True)
    if not engine._config.get("index_object_id"):
        print("mnemos: creating MemoryIndex", file=sys.stderr, flush=True)
        sui = _sui()
        inputs = [sui._input_pure(sui.pure_vec_u8(engine._keys["pubkey"]))]
        digest = sui.execute(
            engine._config["sui_rpc"],
            address,
            inputs,
            [sui.move_call(engine._config["package_id"], "memory_index", "create", [0])],
            _sui_seed(engine),
            30_000_000,
        )
        index_id = _wait_index(engine, address, engine._config["package_id"])
        engine._config["index_object_id"] = index_id
        print(f"mnemos: index {index_id} ({digest})", file=sys.stderr, flush=True)
    return {
        "package_id": engine._config.get("package_id") or "",
        "index_object_id": engine._config.get("index_object_id") or "",
        "sui_address": address,
        "pubkey": engine._keys["pubkey_hex"],
        "faucet": funded,
    }


def _wait_package(engine, owner: str) -> str:
    for _ in range(20):
        found = None
        for type_name in (
            "0x2::package::UpgradeCap",
            "0x0000000000000000000000000000000000000000000000000000000000000002::package::UpgradeCap",
        ):
            found = _sui().find_owned(engine._config["graphql_url"], owner, type_name)
            if found:
                break
        if found:
            package = ((found.get("contents") or {}).get("json") or {}).get("package")
            if package:
                return str(package)
        time.sleep(1)
    raise RuntimeError("UpgradeCap was not visible after publish")


def _wait_index(engine, owner: str, package_id: str) -> str:
    suffix = f"{package_id}::memory_index::MemoryIndex"
    for _ in range(20):
        found = _sui().find_owned(engine._config["graphql_url"], owner, suffix)
        if found and found.get("address"):
            return str(found["address"])
        time.sleep(1)
    raise RuntimeError("MemoryIndex was not visible after create")


def faucet(engine) -> dict[str, Any]:
    from .faucet import fund_if_needed

    funded = fund_if_needed(engine._config["faucet_url"], engine._config["graphql_url"], engine.sui_address(), 0)
    return {"sui_address": engine.sui_address(), "faucet": funded}


# Imported for restore tests that rebuild a blob the Rust core can open.
def rebuild_plaintext(engine_entries: list[dict[str, Any]]) -> bytes:
    return chain.canonical_plaintext(engine_entries)


def download_blob(aggregator: str, blob_id: str) -> bytes:
    return walrus_get(aggregator, blob_id)
