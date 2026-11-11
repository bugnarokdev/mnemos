"""Pure-Python Mnemos engine.

Local recall, encryption, Walrus I/O, Sui transactions, and hash-chain
verification run here when the native module is not loaded. Snapshot bytes
match the Rust core.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import chain

DEFAULTS = {
    "sui_rpc": "https://fullnode.testnet.sui.io:443",
    "graphql_url": "https://graphql.testnet.sui.io/graphql",
    "faucet_url": "https://faucet.testnet.sui.io",
    "walrus_publisher": "https://publisher.walrus-testnet.walrus.space",
    "walrus_aggregator": "https://aggregator.walrus-testnet.walrus.space",
    "epochs": 5,
    "package_id": "",
    "index_object_id": "",
    "network": "testnet",
}


def load_file_config(hermes_home: str | os.PathLike[str]) -> dict[str, Any]:
    path = Path(hermes_home) / "mnemos" / "config.json"
    stored: dict[str, Any] = {}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            stored = loaded
    merged = dict(DEFAULTS)
    merged.update({key: value for key, value in stored.items() if key in DEFAULTS or key == "epochs"})
    merged["epochs"] = int(merged.get("epochs") or DEFAULTS["epochs"])
    return merged


def save_file_config(hermes_home: str | os.PathLike[str], values: dict[str, Any]) -> None:
    path = Path(hermes_home) / "mnemos" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            current = loaded
    for key, value in values.items():
        if value is None:
            current.pop(key, None)
        elif key in DEFAULTS:
            current[key] = value
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")


def package_id_from_type(type_repr: str) -> str:
    """``0xPKG::memory_index::MemoryIndex`` -> ``0xPKG``; empty if the type is something else."""
    parts = str(type_repr or "").split("::")
    if len(parts) == 3 and parts[1:] == ["memory_index", "MemoryIndex"] and parts[0].startswith("0x"):
        return parts[0]
    return ""


def ensure_package_id(hermes_home: str | os.PathLike[str], config: dict[str, Any], timeout: float = 8) -> str:
    """Fill a missing package_id from the MemoryIndex type and save it.

    A restore on a new machine knows the object id but not the package id, and
    flush needs both. The package id is public: it is the prefix of the
    object's Move type. Network errors leave the config unchanged.
    """
    index_id = str(config.get("index_object_id") or "").strip()
    if config.get("package_id") or not index_id:
        return str(config.get("package_id") or "")
    try:
        obj = graphql_object(config.get("graphql_url") or DEFAULTS["graphql_url"], index_id, timeout=timeout)
    except Exception:
        return ""
    repr_ = (((obj.get("asMoveObject") or {}).get("contents") or {}).get("type") or {}).get("repr") or ""
    package_id = package_id_from_type(repr_)
    if package_id:
        config["package_id"] = package_id
        save_file_config(hermes_home, {"package_id": package_id})
    return package_id


class Engine:
    backend = "python"
    chain_writes = True

    def __init__(self, db_path: str, secret: str, config: dict[str, Any], reason: str = ""):
        self._lock = threading.Lock()
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._keys = chain.derive_keys(secret)
        self._legacy_keys = chain.derive_legacy_keys(secret)
        self._config = dict(DEFAULTS)
        self._config.update(config or {})
        self._config["epochs"] = int(self._config.get("epochs") or 5)
        explicit = str(config.get("sui_private_key") or "").strip()
        if explicit:
            self._sui_address = ""
            self._explicit_sui_key = True
        else:
            self._sui_address = self._keys["sui_address"]
            self._explicit_sui_key = False
        self.reason = reason or "native module is not loaded; chain writes use the Python gRPC client"
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _init_db(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                ts INTEGER NOT NULL,
                snapshot_hash TEXT
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                content_hash TEXT PRIMARY KEY,
                prev_hash TEXT NOT NULL,
                version INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                blob BLOB NOT NULL,
                blob_id TEXT,
                signature TEXT NOT NULL,
                idem_key TEXT,
                synced INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self._db.commit()

    def _meta_set(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def remember(self, text: str, session_id: str, kind: str = "fact") -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("empty memory")
        kind = kind or "fact"
        session_id = session_id or "default"
        ts = int(time.time() * 1000)
        with self._lock:
            count = self._db.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
            digest = chain.sha256(f"{kind}\0{session_id}\0{text}\0{ts}\0{count}".encode("utf-8"))
            entry_id = digest.hex()
            self._db.execute(
                "INSERT INTO entries(id, session_id, kind, text, ts, snapshot_hash) VALUES(?, ?, ?, ?, ?, NULL)",
                (entry_id, session_id, kind, text, ts),
            )
            self._db.commit()
        return {"id": entry_id}

    def recall(self, query: str, limit: int = 5) -> dict[str, Any]:
        limit = max(1, min(int(limit or 5), 20))
        tokens = [tok for tok in _tokens(query) if len(tok) >= 4]
        with self._lock:
            rows = self._db.execute(
                "SELECT id, session_id, kind, text, ts FROM entries ORDER BY ts DESC LIMIT 400"
            ).fetchall()
        scored: list[dict[str, Any]] = []
        for row in rows:
            text = row["text"]
            lowered = text.lower()
            if tokens:
                score = sum(1 for tok in tokens if tok in lowered)
                if score <= 0:
                    continue
            else:
                score = 0
            scored.append(
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "kind": row["kind"],
                    "text": text,
                    "ts": row["ts"],
                    "score": score,
                }
            )
        if tokens:
            scored.sort(key=lambda item: (-item["score"], -item["ts"]))
        return {"hits": scored[:limit]}

    def checkpoint(self, entries: list[dict[str, Any]], idem_key: str, session_id: str) -> dict[str, Any]:
        from .py_chain import checkpoint

        return checkpoint(self, entries, idem_key, session_id)

    def flush(self) -> dict[str, Any]:
        from .py_chain import flush

        return flush(self, None)

    def init_chain(self, modules_json: str) -> dict[str, Any]:
        from .py_chain import init_chain

        return init_chain(self, modules_json)

    def faucet(self) -> dict[str, Any]:
        from .py_chain import faucet

        return faucet(self)

    def restore(self, object_id: str) -> dict[str, Any]:
        object_id = (object_id or self._config.get("index_object_id") or "").strip()
        if not object_id:
            raise RuntimeError("index object id is required")
        index = graphql_object(self._config["graphql_url"], object_id)
        contents = (((index or {}).get("asMoveObject") or {}).get("contents") or {}).get("json") or {}
        pubkey = chain.as_bytes(contents.get("pubkey"))
        if pubkey == self._keys["pubkey"]:
            read_keys = self._keys
        elif pubkey == self._legacy_keys["pubkey"]:
            read_keys = self._legacy_keys
        else:
            raise RuntimeError("MemoryIndex was signed by a different key than this secret")
        snapshots = list(contents.get("snapshots") or [])
        expected_prev = chain.GENESIS_PREV
        expected_version = 1
        restored: list[dict[str, Any]] = []
        verified: list[dict[str, Any]] = []
        for snap in snapshots:
            prev = chain.as_bytes(snap.get("prev_hash"))
            content = chain.as_bytes(snap.get("content_hash"))
            signature = chain.as_bytes(snap.get("signature"))
            version = int(snap.get("version"))
            blob_id = chain.as_bytes(snap.get("blob_id")).decode("utf-8")
            session_id = chain.as_bytes(snap.get("session_id")).decode("utf-8")
            if prev != expected_prev or version != expected_version:
                raise RuntimeError(f"hash chain broke at version {version}")
            blob = walrus_get(self._config["walrus_aggregator"], blob_id)
            blob_sha = chain.sha256(blob)
            recomputed = chain.content_hash(prev, blob_sha, version, session_id, blob_id)
            if recomputed != content:
                raise RuntimeError(f"blob {blob_id} does not match the committed content hash")
            if not chain.verify_hash(pubkey, content, signature):
                raise RuntimeError(f"snapshot signature failed at version {version}")
            payload = json.loads(chain.open_blob(read_keys["enc_key"], blob).decode("utf-8"))
            for entry in payload.get("entries") or []:
                restored.append(entry)
            verified.append(
                {
                    "content_hash": content.hex(),
                    "prev_hash": prev.hex(),
                    "version": version,
                    "session_id": session_id,
                    "blob_id": blob_id,
                    "signature": signature.hex(),
                    "blob": blob,
                }
            )
            expected_prev = content
            expected_version += 1
        head = chain.as_bytes(contents.get("head")) if contents.get("head") not in (None, "") else chain.GENESIS_PREV
        if snapshots and head != expected_prev:
            raise RuntimeError("index head does not match the last snapshot")
        if not snapshots and head not in (b"", chain.GENESIS_PREV):
            raise RuntimeError("empty index has a non-genesis head")
        with self._lock:
            self._db.execute("DELETE FROM entries")
            self._db.execute("DELETE FROM snapshots")
            for entry in restored:
                self._db.execute(
                    "INSERT INTO entries(id, session_id, kind, text, ts, snapshot_hash) VALUES(?, ?, ?, ?, ?, ?)",
                    (
                        str(entry["id"]),
                        str(entry["session_id"]),
                        str(entry["kind"]),
                        str(entry["text"]),
                        int(entry["ts"]),
                        verified[-1]["content_hash"] if verified else None,
                    ),
                )
            for snap in verified:
                self._db.execute(
                    """
                    INSERT INTO snapshots(
                        content_hash, prev_hash, version, session_id, blob, blob_id, signature, idem_key, synced, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, NULL, 1, ?)
                    """,
                    (
                        snap["content_hash"],
                        snap["prev_hash"],
                        snap["version"],
                        snap["session_id"],
                        snap["blob"],
                        snap["blob_id"],
                        snap["signature"],
                        int(time.time() * 1000),
                    ),
                )
            self._meta_set("chain_head", expected_prev.hex() if snapshots else chain.GENESIS_PREV.hex())
            self._meta_set("chain_version", str(expected_version - 1))
            self._meta_set("index_object_id", object_id)
            self._db.commit()
        return {
            "entries": len(restored),
            "snapshots": len(verified),
            "head": (expected_prev.hex() if snapshots else chain.GENESIS_PREV.hex()),
            "index_object_id": object_id,
            "kdf": read_keys["kdf"],
            "read_only": read_keys is self._legacy_keys,
        }

    def doctor(self) -> dict[str, Any]:
        checks = []
        checks.append({"name": "backend", "ok": True, "detail": self.reason})
        checks.append(
            {
                "name": "signing_key",
                "ok": len(self._keys["pubkey"]) == 32,
                "detail": self._keys["pubkey_hex"],
            }
        )
        with self._lock:
            pending = self._db.execute(
                "SELECT COUNT(*) AS n FROM entries WHERE snapshot_hash IS NULL"
            ).fetchone()["n"]
            stored = self._db.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
        checks.append({"name": "local_store", "ok": True, "detail": f"{stored} entries, {pending} unsynced"})
        checks.append(self._probe("walrus_publisher", self._config["walrus_publisher"].rstrip("/") + "/v1/api"))
        checks.append(self._probe_graphql())
        index_id = str(self._config.get("index_object_id") or "")
        if index_id:
            try:
                obj = graphql_object(self._config["graphql_url"], index_id, timeout=8)
                ok = bool(obj)
                detail = "readable" if ok else "missing"
            except Exception as exc:
                ok = False
                detail = str(exc)
            checks.append({"name": "index_object", "ok": ok, "detail": detail})
        else:
            checks.append({"name": "index_object", "ok": False, "detail": "unset"})
        checks.append(
            {
                "name": "chain_writes",
                "ok": True,
                "detail": "python gRPC client",
            }
        )
        hard = [item for item in checks if item["name"] in {"signing_key", "local_store"}]
        return {
            "ok": all(item["ok"] for item in hard),
            "backend": self.backend,
            "reason": self.reason,
            "checks": checks,
            "sui_address": self._sui_address,
            "pubkey": self._keys["pubkey_hex"],
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            stored = self._db.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
            pending = self._db.execute(
                "SELECT COUNT(*) AS n FROM entries WHERE snapshot_hash IS NULL"
            ).fetchone()["n"]
            head = self._db.execute("SELECT value FROM meta WHERE key = 'chain_head'").fetchone()
            version = self._db.execute("SELECT value FROM meta WHERE key = 'chain_version'").fetchone()
        return {
            "backend": self.backend,
            "entries": stored,
            "pending": pending,
            "head": head["value"] if head else chain.GENESIS_PREV.hex(),
            "version": int(version["value"]) if version else 0,
            "index_object_id": self._config.get("index_object_id") or "",
            "package_id": self._config.get("package_id") or "",
            "sui_address": self._sui_address,
            "pubkey": self._keys["pubkey_hex"],
            "sui_rpc": self._config.get("sui_rpc") or "",
            "network": self._config.get("network") or "testnet",
            "chain_writes": True,
            "reason": self.reason,
        }

    def pubkey_hex(self) -> str:
        return self._keys["pubkey_hex"]

    def sui_address(self) -> str:
        return self._sui_address

    def _probe(self, name: str, url: str) -> dict[str, Any]:
        try:
            status, _body = http_request("GET", url, timeout=5)
            return {"name": name, "ok": 200 <= status < 500, "detail": f"HTTP {status}"}
        except Exception as exc:
            return {"name": name, "ok": False, "detail": str(exc)}

    def _probe_graphql(self) -> dict[str, Any]:
        try:
            data = graphql(self._config["graphql_url"], "{ chainIdentifier }", timeout=8)
            ident = ((data.get("data") or {}).get("chainIdentifier")) or ""
            return {"name": "sui_graphql", "ok": bool(ident), "detail": ident or "empty"}
        except Exception as exc:
            return {"name": "sui_graphql", "ok": False, "detail": str(exc)}


def _tokens(query: str) -> list[str]:
    out = []
    current = []
    for ch in (query or "").lower():
        if ch.isalnum():
            current.append(ch)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def http_request(method: str, url: str, data: bytes | None = None, timeout: float = 30, headers: dict | None = None):
    merged = {"User-Agent": "mnemos/0.2.2"}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:300]!r}") from exc


def walrus_put(publisher: str, payload: bytes, epochs: int, timeout: float = 90) -> str:
    url = publisher.rstrip("/") + f"/v1/blobs?epochs={int(epochs)}"
    last: Exception | None = None
    for attempt in range(3):
        try:
            _status, body = http_request(
                "PUT",
                url,
                data=payload,
                timeout=timeout,
                headers={"Content-Type": "application/octet-stream"},
            )
            parsed = json.loads(body.decode("utf-8"))
            blob_id = _blob_id(parsed)
            if not blob_id:
                raise RuntimeError(f"Walrus response had no blob id: {body[:300]!r}")
            return blob_id
        except Exception as exc:
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"Walrus PUT failed: {last}")


def walrus_get(aggregator: str, blob_id: str, timeout: float = 60) -> bytes:
    url = aggregator.rstrip("/") + "/v1/blobs/" + blob_id
    last: Exception | None = None
    for attempt in range(4):
        try:
            _status, body = http_request("GET", url, timeout=timeout)
            if not body:
                raise RuntimeError("empty blob")
            return body
        except Exception as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Walrus GET {blob_id} failed: {last}")


def _blob_id(payload: dict) -> str:
    if "newlyCreated" in payload:
        obj = payload["newlyCreated"].get("blobObject") or {}
        return str(obj.get("blobId") or payload["newlyCreated"].get("blobId") or "")
    if "alreadyCertified" in payload:
        cert = payload["alreadyCertified"]
        if isinstance(cert, dict):
            return str(cert.get("blobId") or "")
    return str(payload.get("blobId") or "")


def graphql(url: str, query: str, variables: dict | None = None, timeout: float = 20) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    _status, raw = http_request(
        "POST",
        url,
        data=body,
        timeout=timeout,
        headers={"Content-Type": "application/json"},
    )
    parsed = json.loads(raw.decode("utf-8"))
    if parsed.get("errors"):
        raise RuntimeError(parsed["errors"][0].get("message") or str(parsed["errors"]))
    return parsed


def graphql_object(url: str, object_id: str, timeout: float = 20) -> dict:
    query = """
    query($id: SuiAddress!) {
      object(address: $id) {
        address
        version
        digest
        asMoveObject { contents { type { repr } json } }
      }
    }
    """
    data = graphql(url, query, {"id": object_id}, timeout=timeout)
    obj = (data.get("data") or {}).get("object")
    if not obj:
        raise RuntimeError(f"Sui object {object_id} was not found")
    return obj
