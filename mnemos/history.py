"""Walk a MemoryIndex and check every snapshot against Walrus.

The check needs no secret: the public key, hashes, and signatures live on the
Sui object, and the ciphertext lives on Walrus. For each version it confirms
the version number, the link to the previous content hash, the content hash
recomputed from the downloaded blob, and the Ed25519 signature.
"""

from __future__ import annotations

from typing import Any, Callable

from . import chain


def parse_index(contents: dict[str, Any]) -> dict[str, Any]:
    """Turn MemoryIndex Move JSON into bytes and ints, keeping the on-chain order."""
    raw_head = contents.get("head")
    head = chain.GENESIS_PREV if raw_head in (None, "") else chain.as_bytes(raw_head)
    snapshots = []
    for snap in contents.get("snapshots") or []:
        snapshots.append(
            {
                "version": int(snap.get("version")),
                "blob_id": chain.as_bytes(snap.get("blob_id")).decode("utf-8"),
                "session_id": chain.as_bytes(snap.get("session_id")).decode("utf-8"),
                "content_hash": chain.as_bytes(snap.get("content_hash")),
                "prev_hash": chain.as_bytes(snap.get("prev_hash")),
                "signature": chain.as_bytes(snap.get("signature")),
            }
        )
    return {
        "pubkey": chain.as_bytes(contents.get("pubkey")),
        "head": head or chain.GENESIS_PREV,
        "version": int(contents.get("version") or 0),
        "snapshots": snapshots,
    }


def list_history(index: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "version": snap["version"],
            "content_hash": snap["content_hash"].hex(),
            "prev_hash": snap["prev_hash"].hex(),
            "session_id": snap["session_id"],
            "blob_id": snap["blob_id"],
        }
        for snap in index["snapshots"]
    ]


def verify_history(index: dict[str, Any], fetch_blob: Callable[[str], bytes]) -> dict[str, Any]:
    """Check every snapshot. One BROKEN row makes the verdict fail."""
    pubkey = index["pubkey"]
    rows: list[dict[str, Any]] = []
    expected_prev = chain.GENESIS_PREV
    expected_version = 1
    if len(pubkey) != 32:
        return {"ok": False, "rows": [], "verdict": "MemoryIndex public key is not 32 bytes"}
    for snap in index["snapshots"]:
        version = snap["version"]
        content = snap["content_hash"]
        reason = ""
        if version != expected_version:
            reason = f"version {version}, expected {expected_version}"
        elif snap["prev_hash"] != expected_prev:
            reason = "prev_hash does not match the previous content hash"
        else:
            try:
                blob = fetch_blob(snap["blob_id"])
            except Exception as exc:
                blob = None
                reason = f"blob not readable from Walrus ({exc.__class__.__name__})"
            if blob is not None:
                recomputed = chain.content_hash(
                    snap["prev_hash"], chain.sha256(blob), version, snap["session_id"], snap["blob_id"]
                )
                if recomputed != content:
                    reason = "blob does not match the committed content hash"
                elif not chain.verify_hash(pubkey, content, snap["signature"]):
                    reason = "Ed25519 signature does not verify"
        rows.append(
            {
                "version": version,
                "ok": not reason,
                "reason": reason,
                "content_hash": content.hex(),
                "blob_id": snap["blob_id"],
            }
        )
        expected_prev = content
        expected_version = version + 1
    tail_ok = True
    if index["snapshots"]:
        if index["head"] != expected_prev or index["version"] != expected_version - 1:
            tail_ok = False
    elif index["head"] != chain.GENESIS_PREV or index["version"] != 0:
        tail_ok = False
    broken = [row for row in rows if not row["ok"]]
    if broken:
        verdict = f"BROKEN: {len(broken)} of {len(rows)} versions failed"
    elif not tail_ok:
        verdict = "BROKEN: index head does not match the last snapshot"
    else:
        verdict = f"OK: {len(rows)} version{'' if len(rows) == 1 else 's'} verified"
    return {"ok": not broken and tail_ok, "rows": rows, "verdict": verdict, "pubkey": pubkey.hex()}


def format_row(row: dict[str, Any]) -> str:
    status = "OK" if row["ok"] else "BROKEN"
    line = f"v{row['version']:<4} {status:<6} {row['content_hash'][:16]}  blob {row['blob_id']}"
    if row["reason"]:
        line += f"  ({row['reason']})"
    return line
