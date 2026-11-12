//! Local store plus Walrus and Sui testnet I/O.

use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use base64::Engine as _;
use argon2::{Algorithm, Argon2, Params, Version};
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use sui_crypto::ed25519::Ed25519PrivateKey;
use sui_crypto::SuiSigner;
use sui_sdk_types::{Address, Identifier};
use sui_rpc::field::{FieldMask, FieldMaskUtil};
use sui_transaction_builder::{Function, ObjectInput, TransactionBuilder};

use crate::chain::{
    self, canonical_plaintext, content_hash, open_blob, random_nonce, seal, sha256, sign_hash, verify_hash,
    Entry, Keys, GENESIS_PREV,
};

const LEGACY_READ_ONLY: &str = "MemoryIndex uses the pre-0.2.2 key derivation and is read-only: restore and history still work, new writes need a fresh index (`hermes mnemos init`)";

/// Genesis checkpoint digest of Sui testnet (`4c78adac` in short form).
pub const TESTNET_CHAIN_ID: &str = "69WiPg3DAQiwdxfncX6wYQ2siKwAe6L9BZthQea3JNMD";

#[derive(Clone)]
pub(crate) struct Config {
    pub(crate) sui_rpc: String,
    pub(crate) graphql_url: String,
    pub(crate) faucet_url: String,
    pub(crate) walrus_publisher: String,
    pub(crate) walrus_aggregator: String,
    pub(crate) epochs: u64,
    pub(crate) package_id: String,
    pub(crate) index_object_id: String,
    pub(crate) network: String,
    pub(crate) sui_private_key: String,
    pub(crate) read_source: String,
    pub(crate) graphql_max_lag: u64,
}

pub struct Engine {
    db: Connection,
    keys: Keys,
    legacy_pubkey: [u8; 32],
    legacy_enc_key: [u8; 32],
    sui_key: Ed25519PrivateKey,
    address: Address,
    cfg: Config,
}

struct Head {
    version: u64,
    head: [u8; 32],
    pubkey: [u8; 32],
    snapshots: Vec<OnChainSnap>,
    source: &'static str,
}

struct OnChainSnap {
    blob_id: String,
    session_id: String,
    content_hash: [u8; 32],
    prev_hash: [u8; 32],
    signature: [u8; 64],
    version: u64,
}

struct Pending {
    content_hash: String,
    prev_hash: String,
    version: i64,
    #[allow(dead_code)]
    session_id: String,
    blob: Vec<u8>,
    blob_id: Option<String>,
    signature: String,
}

impl Engine {
    pub fn open(db_path: &str, secret: &str, config_json: &str) -> Result<Self> {
        if let Some(parent) = std::path::Path::new(db_path).parent() {
            std::fs::create_dir_all(parent)?;
        }
        let keys = chain::derive_keys(secret)?;
        let legacy = chain::derive_legacy_keys(secret)?;
        let cfg = parse_config(config_json);
        let sui_key = load_sui_key(&cfg.sui_private_key, keys.sui_seed)?;
        let address = sui_key.public_key().derive_address();
        let db = Connection::open(db_path)?;
        db.execute_batch(
            "
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
                value TEXT NOT NULL
            );
            ",
        )?;
        Ok(Self {
            db,
            keys,
            legacy_pubkey: legacy.pubkey,
            legacy_enc_key: legacy.enc_key,
            sui_key,
            address,
            cfg,
        })
    }

    pub fn pubkey_hex(&self) -> String {
        hex::encode(self.keys.pubkey)
    }

    fn key_mismatch(&self, pubkey: &[u8; 32]) -> anyhow::Error {
        if *pubkey == self.legacy_pubkey {
            anyhow!("{LEGACY_READ_ONLY}")
        } else {
            anyhow!("MemoryIndex was signed by a different key than this secret")
        }
    }

    pub fn sui_address(&self) -> String {
        self.address.to_string()
    }

    pub fn remember(&mut self, text: &str, session_id: &str, kind: &str) -> Result<Value> {
        let text = text.trim();
        if text.is_empty() {
            return Err(anyhow!("empty memory"));
        }
        let session_id = if session_id.is_empty() { "default" } else { session_id };
        let kind = if kind.is_empty() { "fact" } else { kind };
        let ts = now_ms();
        let count: i64 = self.db.query_row("SELECT COUNT(*) FROM entries", [], |row| row.get(0))?;
        let id = hex::encode(sha256(format!("{kind}\0{session_id}\0{text}\0{ts}\0{count}").as_bytes()));
        self.db.execute(
            "INSERT INTO entries(id, session_id, kind, text, ts, snapshot_hash) VALUES(?1, ?2, ?3, ?4, ?5, NULL)",
            params![id, session_id, kind, text, ts],
        )?;
        Ok(json!({"id": id}))
    }

    pub fn recall(&self, query: &str, limit: usize) -> Value {
        let limit = limit.clamp(1, 20);
        let tokens: Vec<String> = tokenize(query).into_iter().filter(|tok| tok.len() >= 4).collect();
        let mut stmt = self
            .db
            .prepare("SELECT id, session_id, kind, text, ts FROM entries ORDER BY ts DESC LIMIT 400")
            .unwrap();
        let rows = stmt
            .query_map([], |row| {
                Ok(Entry {
                    id: row.get(0)?,
                    session_id: row.get(1)?,
                    kind: row.get(2)?,
                    text: row.get(3)?,
                    ts: row.get(4)?,
                })
            })
            .unwrap();
        let mut hits = Vec::new();
        for row in rows.flatten() {
            let lowered = row.text.to_lowercase();
            let score = if tokens.is_empty() {
                0
            } else {
                tokens.iter().filter(|tok| lowered.contains(tok.as_str())).count()
            };
            if !tokens.is_empty() && score == 0 {
                continue;
            }
            hits.push(json!({
                "id": row.id,
                "session_id": row.session_id,
                "kind": row.kind,
                "text": row.text,
                "ts": row.ts,
                "score": score,
            }));
        }
        if !tokens.is_empty() {
            hits.sort_by(|left, right| {
                let ls = left["score"].as_u64().unwrap_or(0);
                let rs = right["score"].as_u64().unwrap_or(0);
                rs.cmp(&ls).then(right["ts"].as_i64().unwrap_or(0).cmp(&left["ts"].as_i64().unwrap_or(0)))
            });
        }
        hits.truncate(limit);
        json!({"hits": hits})
    }

    pub fn checkpoint(&mut self, entries_json: &str, idem_key: &str, session_id: &str) -> Result<Value> {
        if let Some(blob_id) = self.synced_blob(idem_key)? {
            return Ok(json!({"blob_id": blob_id, "reused": true, "flushed": 0}));
        }
        let rows: Vec<Value> = serde_json::from_str(entries_json).context("checkpoint entries")?;
        for row in rows {
            let text = row["text"].as_str().unwrap_or("");
            let kind = row["kind"].as_str().unwrap_or("checkpoint");
            let sid = row["session_id"].as_str().filter(|value| !value.is_empty()).unwrap_or(session_id);
            if !text.trim().is_empty() {
                self.remember(text, sid, kind)?;
            }
        }
        self.flush(Some(idem_key))
    }

    pub fn flush(&mut self, idem_key: Option<&str>) -> Result<Value> {
        let index_id = self.cfg.index_object_id.clone();
        if index_id.is_empty() {
            return Err(anyhow!("index object is not configured; run `hermes mnemos init`"));
        }
        if self.cfg.package_id.is_empty() {
            return Err(anyhow!("package id is not configured; run `hermes mnemos init`"));
        }
        let chain = self.read_chain_fresh(&index_id)?;
        if chain.pubkey != self.keys.pubkey {
            return Err(self.key_mismatch(&chain.pubkey));
        }
        self.adopt_committed(&chain)?;
        let entries = self.unsynced_entries()?;
        if entries.is_empty() {
            let blob_id: Option<String> = self
                .db
                .query_row(
                    "SELECT blob_id FROM snapshots WHERE synced = 1 AND blob_id IS NOT NULL ORDER BY version DESC LIMIT 1",
                    [],
                    |row| row.get(0),
                )
                .ok();
            return Ok(json!({
                "flushed": 0,
                "head": hex::encode(chain.head),
                "version": chain.version,
                "blob_id": blob_id,
            }));
        }
        // Refuse a non-testnet RPC before uploading anything. execute_tx checks again.
        block_on(check_rpc(&self.cfg.sui_rpc))?;
        let session_id = entries.last().map(|entry| entry.session_id.clone()).unwrap_or_else(|| "default".into());
        let plaintext = canonical_plaintext(&entries);
        let plain_key = hex::encode(sha256(&plaintext));
        let idem = idem_key.unwrap_or(plain_key.as_str()).to_string();
        if let Some(pending) = self.pending_by_idem(&idem)? {
            if decode_hex32(&pending.content_hash)? == chain.head {
                self.mark_synced(&pending.content_hash, &entries)?;
                return Ok(json!({
                    "flushed": entries.len(),
                    "blob_id": pending.blob_id,
                    "content_hash": pending.content_hash,
                    "version": pending.version,
                    "reused": true,
                }));
            }
            if decode_hex32(&pending.prev_hash)? == chain.head {
                let blob_id = match pending.blob_id.clone() {
                    Some(id) => id,
                    None => block_on(walrus_put(&self.cfg.walrus_publisher, &pending.blob, self.cfg.epochs))?,
                };
                if pending.blob_id.is_none() {
                    self.db.execute(
                        "UPDATE snapshots SET blob_id = ?1 WHERE content_hash = ?2",
                        params![blob_id, pending.content_hash],
                    )?;
                }
                let digest = self.append_on_chain(&blob_id, &session_id, &pending)?;
                self.mark_synced(&pending.content_hash, &entries)?;
                return Ok(json!({
                    "flushed": entries.len(),
                    "blob_id": blob_id,
                    "content_hash": pending.content_hash,
                    "version": pending.version,
                    "digest": digest,
                }));
            }
            self.db.execute("DELETE FROM snapshots WHERE content_hash = ?1 AND synced = 0", params![pending.content_hash])?;
        }
        let version = chain.version + 1;
        let nonce = random_nonce()?;
        let blob = seal(&self.keys.enc_key, &plaintext, &nonce)?;
        let blob_id = block_on(walrus_put(&self.cfg.walrus_publisher, &blob, self.cfg.epochs))?;
        let blob_sha = sha256(&blob);
        let digest_bytes = content_hash(&chain.head, &blob_sha, version, &session_id, &blob_id);
        let signature = sign_hash(&self.keys.sign_key, &digest_bytes);
        let content_hex = hex::encode(digest_bytes);
        let prev_hex = hex::encode(chain.head);
        let sig_hex = hex::encode(signature);
        self.db.execute(
            "INSERT INTO snapshots(content_hash, prev_hash, version, session_id, blob, blob_id, signature, idem_key, synced, created_at)
             VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 0, ?9)",
            params![content_hex, prev_hex, version as i64, session_id, blob, blob_id, sig_hex, idem, now_ms()],
        )?;
        let pending = Pending {
            content_hash: content_hex.clone(),
            prev_hash: prev_hex,
            version: version as i64,
            session_id: session_id.clone(),
            blob,
            blob_id: Some(blob_id.clone()),
            signature: sig_hex,
        };
        let digest = self.append_on_chain(&blob_id, &session_id, &pending)?;
        self.mark_synced(&content_hex, &entries)?;
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES('chain_head', ?1) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![content_hex],
        )?;
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES('chain_version', ?1) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![version.to_string()],
        )?;
        Ok(json!({
            "flushed": entries.len(),
            "blob_id": blob_id,
            "content_hash": content_hex,
            "version": version,
            "digest": digest,
            "head": content_hex,
        }))
    }

    // Pre-existing loop kept as is; the lint is style only (no behavior change).
    #[allow(clippy::explicit_counter_loop)]
    pub fn restore(&mut self, object_id: &str) -> Result<Value> {
        let object_id = if object_id.is_empty() {
            self.cfg.index_object_id.clone()
        } else {
            object_id.to_string()
        };
        if object_id.is_empty() {
            return Err(anyhow!("index object id is required"));
        }
        let chain = block_on(read_index(&self.cfg, &object_id, false))?;
        let (enc_key, legacy) = if chain.pubkey == self.keys.pubkey {
            (self.keys.enc_key, false)
        } else if chain.pubkey == self.legacy_pubkey {
            (self.legacy_enc_key, true)
        } else {
            return Err(anyhow!("MemoryIndex was signed by a different key than this secret"));
        };
        let mut expected_prev = GENESIS_PREV;
        let mut expected_version = 1u64;
        let mut restored = Vec::new();
        let mut verified = Vec::new();
        for snap in &chain.snapshots {
            if snap.prev_hash != expected_prev || snap.version != expected_version {
                return Err(anyhow!("hash chain broke at version {}", snap.version));
            }
            let blob = block_on(walrus_get(&self.cfg.walrus_aggregator, &snap.blob_id))?;
            let blob_sha = sha256(&blob);
            let recomputed = content_hash(&snap.prev_hash, &blob_sha, snap.version, &snap.session_id, &snap.blob_id);
            if recomputed != snap.content_hash {
                return Err(anyhow!("blob {} does not match the committed content hash", snap.blob_id));
            }
            if !verify_hash(&chain.pubkey, &snap.content_hash, &snap.signature) {
                return Err(anyhow!("snapshot signature failed at version {}", snap.version));
            }
            let plain = open_blob(&enc_key, &blob)?;
            let payload: Value = serde_json::from_slice(&plain).context("snapshot plaintext")?;
            let entries = payload["entries"].as_array().cloned().unwrap_or_default();
            for entry in entries {
                restored.push((snap.content_hash, entry));
            }
            verified.push((snap, blob));
            expected_prev = snap.content_hash;
            expected_version += 1;
        }
        if !chain.snapshots.is_empty() && chain.head != expected_prev {
            return Err(anyhow!("index head does not match the last snapshot"));
        }
        let tx = self.db.transaction()?;
        tx.execute("DELETE FROM entries", [])?;
        tx.execute("DELETE FROM snapshots", [])?;
        for (hash, entry) in &restored {
            tx.execute(
                "INSERT INTO entries(id, session_id, kind, text, ts, snapshot_hash) VALUES(?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    entry["id"].as_str().unwrap_or(""),
                    entry["session_id"].as_str().unwrap_or(""),
                    entry["kind"].as_str().unwrap_or("fact"),
                    entry["text"].as_str().unwrap_or(""),
                    entry["ts"].as_i64().unwrap_or(0),
                    hex::encode(hash),
                ],
            )?;
        }
        for (snap, blob) in &verified {
            tx.execute(
                "INSERT INTO snapshots(content_hash, prev_hash, version, session_id, blob, blob_id, signature, idem_key, synced, created_at)
                 VALUES(?1, ?2, ?3, ?4, ?5, ?6, ?7, NULL, 1, ?8)",
                params![
                    hex::encode(snap.content_hash),
                    hex::encode(snap.prev_hash),
                    snap.version as i64,
                    snap.session_id,
                    blob,
                    snap.blob_id,
                    hex::encode(snap.signature),
                    now_ms(),
                ],
            )?;
        }
        tx.commit()?;
        let head_hex = hex::encode(if chain.snapshots.is_empty() { GENESIS_PREV } else { expected_prev });
        let version_text = chain.version.to_string();
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES('chain_head', ?1) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![head_hex],
        )?;
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES('chain_version', ?1) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![version_text],
        )?;
        self.cfg.index_object_id = object_id.clone();
        Ok(json!({
            "entries": restored.len(),
            "snapshots": verified.len(),
            "head": hex::encode(if chain.snapshots.is_empty() { GENESIS_PREV } else { expected_prev }),
            "index_object_id": object_id,
            "kdf": if legacy { "legacy" } else { "argon2id" },
            "read_only": legacy,
        }))
    }

    pub fn doctor(&self) -> Result<Value> {
        let stored: i64 = self.db.query_row("SELECT COUNT(*) FROM entries", [], |row| row.get(0))?;
        let pending: i64 = self.db.query_row(
            "SELECT COUNT(*) FROM entries WHERE snapshot_hash IS NULL",
            [],
            |row| row.get(0),
        )?;
        let mut checks = vec![
            json!({"name": "backend", "ok": true, "detail": "rust"}),
            json!({"name": "signing_key", "ok": true, "detail": self.pubkey_hex()}),
            json!({"name": "local_store", "ok": true, "detail": format!("{stored} entries, {pending} unsynced")}),
            json!({"name": "chain_writes", "ok": true, "detail": "native core"}),
            json!({"name": "sui_address", "ok": true, "detail": self.sui_address()}),
        ];
        checks.push(block_on(probe_url("walrus_publisher", &format!("{}/v1/api", self.cfg.walrus_publisher.trim_end_matches('/')))));
        checks.push(block_on(probe_graphql(&self.cfg.graphql_url)));
        checks.push(match block_on(check_graphql_fresh(&self.cfg)) {
            Ok(lag) => json!({"name": "graphql_fresh", "ok": true, "detail": format!("{lag} checkpoints behind the fullnode")}),
            Err(err) => json!({"name": "graphql_fresh", "ok": false, "detail": format!("{err} (reads use the fullnode)")}),
        });
        if self.cfg.index_object_id.is_empty() {
            checks.push(json!({"name": "index_object", "ok": false, "detail": "unset"}));
        } else {
            match block_on(read_index(&self.cfg, &self.cfg.index_object_id, false)) {
                Ok(head) => checks.push(json!({
                    "name": "index_object",
                    "ok": head.pubkey == self.keys.pubkey,
                    "detail": format!("readable via {}, version {} snapshots {}", head.source, head.version, head.snapshots.len()),
                })),
                Err(err) => checks.push(json!({"name": "index_object", "ok": false, "detail": err.to_string()})),
            }
        }
        let ok = checks
            .iter()
            .filter(|check| matches!(check["name"].as_str(), Some("signing_key" | "local_store")))
            .all(|check| check["ok"].as_bool().unwrap_or(false));
        Ok(json!({
            "ok": ok,
            "backend": "rust",
            "reason": "the mnemos_core extension is loaded",
            "checks": checks,
            "sui_address": self.sui_address(),
            "pubkey": self.pubkey_hex(),
        }))
    }

    pub fn status(&self) -> Value {
        let stored: i64 = self.db.query_row("SELECT COUNT(*) FROM entries", [], |row| row.get(0)).unwrap_or(0);
        let pending: i64 = self
            .db
            .query_row("SELECT COUNT(*) FROM entries WHERE snapshot_hash IS NULL", [], |row| row.get(0))
            .unwrap_or(0);
        let head: String = self
            .db
            .query_row("SELECT value FROM meta WHERE key = 'chain_head'", [], |row| row.get(0))
            .unwrap_or_else(|_| hex::encode(GENESIS_PREV));
        let version: String = self
            .db
            .query_row("SELECT value FROM meta WHERE key = 'chain_version'", [], |row| row.get(0))
            .unwrap_or_else(|_| "0".into());
        json!({
            "backend": "rust",
            "entries": stored,
            "pending": pending,
            "head": head,
            "version": version.parse::<u64>().unwrap_or(0),
            "index_object_id": self.cfg.index_object_id,
            "package_id": self.cfg.package_id,
            "sui_address": self.sui_address(),
            "pubkey": self.pubkey_hex(),
            "sui_rpc": self.cfg.sui_rpc,
            "network": self.cfg.network,
            "chain_writes": true,
            "reason": "the mnemos_core extension is loaded",
        })
    }

    pub fn faucet(&self) -> Result<Value> {
        let funded = block_on(fund_if_needed(&self.cfg, self.address, 0))?;
        Ok(json!({"sui_address": self.sui_address(), "faucet": funded}))
    }

    pub fn init_chain(&mut self, modules_json: &str) -> Result<Value> {
        eprintln!("mnemos: sui address {}", self.address);
        block_on(check_rpc(&self.cfg.sui_rpc))?;
        let funded = block_on(fund_if_needed(&self.cfg, self.address, 200_000_000))?;
        eprintln!("mnemos: gas ready");
        if self.cfg.package_id.is_empty() {
            eprintln!("mnemos: publishing MemoryIndex");
            let package_id = block_on(publish_package(&self.cfg, &self.sui_key, self.address, modules_json))?;
            eprintln!("mnemos: package {package_id}");
            self.cfg.package_id = package_id;
        }
        if self.cfg.index_object_id.is_empty() {
            eprintln!("mnemos: creating MemoryIndex");
            let index_id = block_on(create_index(&self.cfg, &self.sui_key, self.address, &self.cfg.package_id, &self.keys.pubkey))?;
            self.cfg.index_object_id = index_id;
        }
        Ok(json!({
            "package_id": self.cfg.package_id,
            "index_object_id": self.cfg.index_object_id,
            "sui_address": self.sui_address(),
            "pubkey": self.pubkey_hex(),
            "faucet": funded,
        }))
    }

    fn read_chain_fresh(&self, index_id: &str) -> Result<Head> {
        let local_version: i64 = self
            .db
            .query_row("SELECT value FROM meta WHERE key = 'chain_version'", [], |row| row.get::<_, String>(0))
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(0);
        let mut latest: Option<Head> = None;
        for attempt in 0..8 {
            let chain = block_on(read_index(&self.cfg, index_id, true))?;
            if chain.version as i64 >= local_version {
                return Ok(chain);
            }
            latest = Some(chain);
            if attempt + 1 < 8 {
                std::thread::sleep(Duration::from_secs(1));
            }
        }
        latest.ok_or_else(|| anyhow!("MemoryIndex {index_id} was not readable"))
    }

    fn unsynced_entries(&self) -> Result<Vec<Entry>> {
        let mut stmt = self.db.prepare(
            "SELECT id, session_id, kind, text, ts FROM entries WHERE snapshot_hash IS NULL ORDER BY ts ASC",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(Entry {
                id: row.get(0)?,
                session_id: row.get(1)?,
                kind: row.get(2)?,
                text: row.get(3)?,
                ts: row.get(4)?,
            })
        })?;
        Ok(rows.collect::<std::result::Result<Vec<_>, _>>()?)
    }

    fn synced_blob(&self, idem: &str) -> Result<Option<String>> {
        let mut stmt = self.db.prepare(
            "SELECT blob_id FROM snapshots WHERE idem_key = ?1 AND synced = 1 AND blob_id IS NOT NULL ORDER BY version DESC LIMIT 1",
        )?;
        let mut rows = stmt.query(params![idem])?;
        if let Some(row) = rows.next()? {
            return Ok(row.get(0)?);
        }
        Ok(None)
    }

    fn pending_by_idem(&self, idem: &str) -> Result<Option<Pending>> {
        let mut stmt = self.db.prepare(
            "SELECT content_hash, prev_hash, version, session_id, blob, blob_id, signature
             FROM snapshots WHERE idem_key = ?1 AND synced = 0 ORDER BY created_at DESC LIMIT 1",
        )?;
        let mut rows = stmt.query(params![idem])?;
        if let Some(row) = rows.next()? {
            return Ok(Some(Pending {
                content_hash: row.get(0)?,
                prev_hash: row.get(1)?,
                version: row.get(2)?,
                session_id: row.get(3)?,
                blob: row.get(4)?,
                blob_id: row.get(5)?,
                signature: row.get(6)?,
            }));
        }
        Ok(None)
    }

    fn adopt_committed(&mut self, chain: &Head) -> Result<()> {
        let head = hex::encode(chain.head);
        let exists: i64 = self.db.query_row(
            "SELECT COUNT(*) FROM snapshots WHERE content_hash = ?1",
            params![head],
            |row| row.get(0),
        )?;
        if exists == 0 {
            return Ok(());
        }
        self.db.execute("UPDATE snapshots SET synced = 1 WHERE content_hash = ?1", params![head])?;
        Ok(())
    }

    fn mark_synced(&mut self, content_hash: &str, entries: &[Entry]) -> Result<()> {
        self.db.execute("UPDATE snapshots SET synced = 1 WHERE content_hash = ?1", params![content_hash])?;
        for entry in entries {
            self.db.execute(
                "UPDATE entries SET snapshot_hash = ?1 WHERE id = ?2",
                params![content_hash, entry.id],
            )?;
        }
        Ok(())
    }

    fn append_on_chain(&self, blob_id: &str, session_id: &str, pending: &Pending) -> Result<String> {
        let content = decode_hex32(&pending.content_hash)?;
        let prev = decode_hex32(&pending.prev_hash)?;
        let mut signature = [0u8; 64];
        let raw = hex::decode(&pending.signature)?;
        if raw.len() != 64 {
            return Err(anyhow!("snapshot signature is not 64 bytes"));
        }
        signature.copy_from_slice(&raw);
        let package: Address = self.cfg.package_id.parse().context("package id")?;
        let index: Address = self.cfg.index_object_id.parse().context("index object id")?;
        let version = pending.version as u64;
        let rpc = self.cfg.sui_rpc.clone();
        let key = self.sui_key.clone();
        let sender = self.address;
        let blob_bytes = blob_id.as_bytes().to_vec();
        let session_bytes = session_id.as_bytes().to_vec();
        block_on(async move {
            let mut tx = TransactionBuilder::new();
            tx.set_sender(sender);
            tx.set_gas_budget(30_000_000);
            let object = tx.object(ObjectInput::new(index).as_owned());
            let blob_arg = tx.pure(&blob_bytes);
            let session_arg = tx.pure(&session_bytes);
            let content_arg = tx.pure(&content.to_vec());
            let prev_arg = tx.pure(&prev.to_vec());
            let sig_arg = tx.pure(&signature.to_vec());
            let version_arg = tx.pure(&version);
            tx.move_call(
                Function::new(package, Identifier::from_static("memory_index"), Identifier::from_static("append")),
                vec![object, blob_arg, session_arg, content_arg, prev_arg, sig_arg, version_arg],
            );
            execute_tx(&rpc, &key, tx).await
        })
    }
}

pub(crate) fn parse_config(raw: &str) -> Config {
    let value: Value = serde_json::from_str(raw).unwrap_or(Value::Null);
    Config {
        sui_rpc: str_or(&value, "sui_rpc", "https://fullnode.testnet.sui.io:443"),
        graphql_url: str_or(&value, "graphql_url", "https://graphql.testnet.sui.io/graphql"),
        faucet_url: str_or(&value, "faucet_url", "https://faucet.testnet.sui.io"),
        walrus_publisher: str_or(&value, "walrus_publisher", "https://publisher.walrus-testnet.walrus.space"),
        walrus_aggregator: str_or(&value, "walrus_aggregator", "https://aggregator.walrus-testnet.walrus.space"),
        epochs: u64_of(&value, "epochs", 5),
        package_id: str_or(&value, "package_id", ""),
        index_object_id: str_or(&value, "index_object_id", ""),
        network: str_or(&value, "network", "testnet"),
        sui_private_key: str_or(&value, "sui_private_key", ""),
        read_source: str_or(&value, "read_source", "auto").to_lowercase(),
        graphql_max_lag: u64_of(&value, "graphql_max_lag", GRAPHQL_MAX_LAG),
    }
}

fn str_or(value: &Value, key: &str, default: &str) -> String {
    value.get(key).and_then(|item| item.as_str()).filter(|item| !item.is_empty()).unwrap_or(default).to_string()
}

fn u64_of(value: &Value, key: &str, default: u64) -> u64 {
    match value.get(key) {
        Some(Value::Number(number)) => number.as_u64().unwrap_or(default),
        Some(Value::String(text)) => text.parse().unwrap_or(default),
        _ => default,
    }
}

pub(crate) fn load_sui_key(explicit: &str, seed: [u8; 32]) -> Result<Ed25519PrivateKey> {
    let explicit = explicit.trim();
    if explicit.is_empty() {
        return Ok(Ed25519PrivateKey::new(seed));
    }
    if explicit.starts_with("suiprivkey") {
        return Ed25519PrivateKey::from_suiprivkey(explicit).map_err(|err| anyhow!("sui key: {err}"));
    }
    if let Ok(key) = Ed25519PrivateKey::from_base64(explicit) {
        return Ok(key);
    }
    let stripped = explicit.trim_start_matches("0x");
    if let Ok(bytes) = hex::decode(stripped) {
        if bytes.len() == 32 {
            let mut seed = [0u8; 32];
            seed.copy_from_slice(&bytes);
            return Ok(Ed25519PrivateKey::new(seed));
        }
    }
    Err(anyhow!("MNEMOS_SUI_PRIVATE_KEY must be a suiprivkey, keystore base64, or 32-byte hex seed"))
}

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as i64)
        .unwrap_or(0)
}

fn tokenize(query: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    for ch in query.to_lowercase().chars() {
        if ch.is_ascii_alphanumeric() {
            current.push(ch);
        } else if !current.is_empty() {
            out.push(std::mem::take(&mut current));
        }
    }
    if !current.is_empty() {
        out.push(current);
    }
    out
}

fn decode_hex32(value: &str) -> Result<[u8; 32]> {
    let bytes = hex::decode(value)?;
    bytes.try_into().map_err(|_| anyhow!("expected 32 bytes"))
}

pub(crate) fn block_on<F: std::future::Future>(future: F) -> F::Output {
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .worker_threads(2)
        .build()
        .expect("tokio runtime")
        .block_on(future)
}

fn http_client(timeout: Duration) -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(timeout)
        .connect_timeout(Duration::from_secs(10))
        .build()
        .expect("http client")
}

async fn probe_url(name: &str, url: &str) -> Value {
    let client = http_client(Duration::from_secs(8));
    match client.get(url).send().await {
        Ok(response) => json!({"name": name, "ok": response.status().is_success() || response.status().as_u16() < 500, "detail": format!("HTTP {}", response.status())}),
        Err(err) => json!({"name": name, "ok": false, "detail": err.to_string()}),
    }
}

async fn probe_graphql(url: &str) -> Value {
    match graphql(url, "{ chainIdentifier }", json!({})).await {
        Ok(value) => {
            let ident = value["data"]["chainIdentifier"].as_str().unwrap_or("").to_string();
            json!({"name": "sui_graphql", "ok": !ident.is_empty(), "detail": ident})
        }
        Err(err) => json!({"name": "sui_graphql", "ok": false, "detail": err.to_string()}),
    }
}

async fn graphql(url: &str, query: &str, variables: Value) -> Result<Value> {
    let client = http_client(Duration::from_secs(30));
    let response = client
        .post(url)
        .json(&json!({"query": query, "variables": variables}))
        .send()
        .await?
        .error_for_status()?
        .json::<Value>()
        .await?;
    if let Some(message) = response
        .get("errors")
        .and_then(|item| item.as_array())
        .and_then(|errors| errors.first())
        .and_then(|first| first.get("message"))
        .and_then(|message| message.as_str())
    {
        return Err(anyhow!(message.to_string()));
    }
    Ok(response)
}

pub(crate) async fn walrus_put(publisher: &str, payload: &[u8], epochs: u64) -> Result<String> {
    let client = http_client(Duration::from_secs(120));
    let url = format!("{}/v1/blobs?epochs={epochs}", publisher.trim_end_matches('/'));
    let mut last = String::from("no attempt");
    for attempt in 0..3 {
        match client.put(&url).header("content-type", "application/octet-stream").body(payload.to_vec()).send().await {
            Ok(response) => {
                let status = response.status();
                let body = response.text().await.unwrap_or_default();
                if status.is_success() {
                    let parsed: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
                    if let Some(id) = blob_id_of(&parsed) {
                        return Ok(id);
                    }
                    last = format!("no blob id in {body}");
                } else {
                    last = format!("HTTP {status} {body}");
                }
            }
            Err(err) => last = err.to_string(),
        }
        tokio::time::sleep(Duration::from_millis(400 * (attempt + 1))).await;
    }
    Err(anyhow!("Walrus PUT failed: {last}"))
}

pub(crate) async fn walrus_get(aggregator: &str, blob_id: &str) -> Result<Vec<u8>> {
    let client = http_client(Duration::from_secs(60));
    let url = format!("{}/v1/blobs/{blob_id}", aggregator.trim_end_matches('/'));
    let mut last = String::from("no attempt");
    for attempt in 0..4 {
        match client.get(&url).send().await {
            Ok(response) if response.status().is_success() => return Ok(response.bytes().await?.to_vec()),
            Ok(response) => last = format!("HTTP {}", response.status()),
            Err(err) => last = err.to_string(),
        }
        tokio::time::sleep(Duration::from_millis(500 * (attempt + 1))).await;
    }
    Err(anyhow!("Walrus GET {blob_id} failed: {last}"))
}

fn blob_id_of(payload: &Value) -> Option<String> {
    if let Some(created) = payload.get("newlyCreated") {
        if let Some(id) = created["blobObject"]["blobId"].as_str() {
            return Some(id.to_string());
        }
        if let Some(id) = created["blobId"].as_str() {
            return Some(id.to_string());
        }
    }
    payload["alreadyCertified"]["blobId"].as_str().map(str::to_string)
        .or_else(|| payload["blobId"].as_str().map(str::to_string))
}

async fn read_index(cfg: &Config, object_id: &str, check_package: bool) -> Result<Head> {
    let object = read_object(cfg, object_id).await?;
    if !object.type_repr.ends_with("::memory_index::MemoryIndex") {
        let kind = if object.type_repr.is_empty() { "non-Move object" } else { object.type_repr.as_str() };
        return Err(anyhow!("Sui object {object_id} is a {kind}, not a MemoryIndex"));
    }
    if check_package && !cfg.package_id.is_empty() {
        let owner = object.type_repr.split("::").next().unwrap_or("");
        if norm_address(owner)? != norm_address(&cfg.package_id)? {
            return Err(anyhow!("MemoryIndex {object_id} belongs to package {owner}, not {}", cfg.package_id));
        }
    }
    let _ = object.version;
    let contents = &object.json;
    if !contents.is_object() {
        return Err(anyhow!("Sui object {object_id} has no Move content"));
    }
    let pubkey = fixed32(&as_bytes(&contents["pubkey"])?)?;
    let head = if contents["head"].is_null() {
        GENESIS_PREV
    } else {
        let raw = as_bytes(&contents["head"])?;
        if raw.is_empty() {
            GENESIS_PREV
        } else {
            fixed32(&raw)?
        }
    };
    let version = as_u64(&contents["version"]);
    let mut snapshots = Vec::new();
    for snap in contents["snapshots"].as_array().cloned().unwrap_or_default() {
        let signature = as_bytes(&snap["signature"])?;
        if signature.len() != 64 {
            return Err(anyhow!("snapshot signature is not 64 bytes"));
        }
        let mut sig = [0u8; 64];
        sig.copy_from_slice(&signature);
        snapshots.push(OnChainSnap {
            blob_id: String::from_utf8(as_bytes(&snap["blob_id"])?)?,
            session_id: String::from_utf8(as_bytes(&snap["session_id"])?)?,
            content_hash: fixed32(&as_bytes(&snap["content_hash"])?)?,
            prev_hash: fixed32(&as_bytes(&snap["prev_hash"])?)?,
            signature: sig,
            version: as_u64(&snap["version"]),
        });
    }
    snapshots.sort_by_key(|snap| snap.version);
    Ok(Head { version, head, pubkey, snapshots, source: object.source })
}

fn fixed32(bytes: &[u8]) -> Result<[u8; 32]> {
    let arr: [u8; 32] = bytes.try_into().map_err(|_| anyhow!("expected 32 bytes, got {}", bytes.len()))?;
    Ok(arr)
}

pub(crate) fn as_u64(value: &Value) -> u64 {
    value.as_u64().or_else(|| value.as_i64().map(|item| item as u64)).or_else(|| value.as_str().and_then(|text| text.parse().ok())).unwrap_or(0)
}

pub(crate) fn as_bytes(value: &Value) -> Result<Vec<u8>> {
    match value {
        Value::String(text) => {
            let trimmed = text.trim();
            let hex_text = trimmed.trim_start_matches("0x");
            if !hex_text.is_empty() && hex_text.chars().all(|ch| ch.is_ascii_hexdigit()) && hex_text.len() % 2 == 0 {
                return hex::decode(hex_text).map_err(|err| anyhow!(err));
            }
            base64::Engine::decode(&base64::engine::general_purpose::STANDARD, trimmed).map_err(|err| anyhow!(err))
        }
        Value::Array(items) => {
            let mut out = Vec::with_capacity(items.len());
            for item in items {
                let number = item.as_u64().or_else(|| item.as_i64().map(|value| value as u64)).context("byte list")?;
                out.push(number as u8);
            }
            Ok(out)
        }
        _ => Err(anyhow!("cannot decode Move bytes")),
    }
}

async fn graphql_balance(graphql_url: &str, address: Address) -> Result<u64> {
    let query = r#"
        query($a: SuiAddress!) {
          address(address: $a) { balance(coinType: "0x2::sui::SUI") { totalBalance } }
        }
    "#;
    let value = graphql(graphql_url, query, json!({"a": address.to_string()})).await?;
    let text = value["data"]["address"]["balance"]["totalBalance"].as_str().unwrap_or("0");
    Ok(text.parse().unwrap_or(0))
}

async fn sui_balance(cfg: &Config, address: Address) -> Result<u64> {
    match grpc_balance(cfg, address).await {
        Ok(value) => Ok(value),
        Err(fail) => {
            fallback_allowed(cfg, "balance", fail).await?;
            graphql_balance(&cfg.graphql_url, address).await
        }
    }
}

async fn fund_if_needed(cfg: &Config, address: Address, minimum: u64) -> Result<Value> {
    let balance = sui_balance(cfg, address).await.unwrap_or(0);
    if balance >= minimum && minimum > 0 {
        return Ok(json!({"funded": false, "balance": balance.to_string(), "sui_address": address.to_string()}));
    }
    if minimum == 0 && balance > 0 {
        return Ok(json!({"funded": false, "balance": balance.to_string(), "sui_address": address.to_string()}));
    }
    let mut last = String::from("faucet was not attempted");
    for _ in 0..3 {
        match request_faucet(&cfg.faucet_url, address).await {
            Ok(digest) => {
                tokio::time::sleep(Duration::from_secs(2)).await;
                let balance = sui_balance(cfg, address).await.unwrap_or(0);
                return Ok(json!({"funded": true, "digest": digest, "balance": balance.to_string(), "sui_address": address.to_string()}));
            }
            Err(err) => last = err.to_string(),
        }
    }
    Err(anyhow!(
        "{last}. Fund {} from the browser faucet if this environment is rate-limited or the faucet asks for a captcha, then rerun. https://faucet.sui.io",
        address
    ))
}

async fn request_faucet(faucet_url: &str, address: Address) -> Result<String> {
    let client = http_client(Duration::from_secs(30));
    let challenge_url = format!(
        "{}/v3/challenge?recipient={}",
        faucet_url.trim_end_matches('/'),
        address
    );
    let challenge = client.get(challenge_url).send().await?.error_for_status()?.json::<Value>().await?;
    let (nonce, hash_hex) = solve_faucet(&challenge)?;
    let body = json!({
        "recipient": address.to_string(),
        "checkpointSeq": as_u64(&challenge["checkpointSeq"]).to_string(),
        "nonce": nonce.to_string(),
        "hashHex": hash_hex,
    });
    let response = client
        .post(format!("{}/v3/gas", faucet_url.trim_end_matches('/')))
        .json(&body)
        .send()
        .await?;
    let status = response.status();
    let text = response.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(anyhow!("faucet HTTP {status}: {text}"));
    }
    let parsed: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
    Ok(parsed["digest"].as_str().unwrap_or("").to_string())
}

fn solve_faucet(challenge: &Value) -> Result<(u64, String)> {
    let memory = as_u64(&challenge["memorySize"]) as u32;
    let iterations = as_u64(&challenge["iterations"]) as u32;
    let parallelism = as_u64(&challenge["parallelism"]) as u32;
    let hash_len = as_u64(&challenge["hashLength"]) as usize;
    let threshold = as_u64(&challenge["threshold"]);
    let salt = challenge["salt"].as_str().unwrap_or("sui-faucet-pow-1");
    if hash_len < 8 || memory == 0 {
        return Err(anyhow!("faucet challenge is missing proof-of-work parameters"));
    }
    let params = Params::new(memory, iterations.max(1), parallelism.max(1), Some(hash_len))
        .map_err(|err| anyhow!("argon2 params: {err}"))?;
    let argon = Argon2::new(Algorithm::Argon2d, Version::V0x13, params);
    let mut seed = [0u8; 8];
    getrandom::getrandom(&mut seed).map_err(|err| anyhow!(err))?;
    let mut nonce = u64::from_le_bytes(seed);
    let started = std::time::Instant::now();
    let domain = challenge["domain"].as_str().unwrap_or("sui-faucet-pow/1");
    let chain_id = challenge["chainId"].as_str().unwrap_or("");
    let checkpoint = as_u64(&challenge["checkpointSeq"]);
    let digest = challenge["checkpointDigest"].as_str().unwrap_or("");
    let random_bytes = challenge["randomBytes"].as_str().unwrap_or("");
    let faucet_address = challenge["faucetAddress"].as_str().unwrap_or("");
    let recipient = challenge["recipient"].as_str().unwrap_or("");
    while started.elapsed() < Duration::from_secs(50) {
        let preimage = format!("{domain}\n{chain_id}\n{checkpoint}\n{digest}\n{random_bytes}\n{faucet_address}\n{recipient}\n{nonce}");
        let mut hash = vec![0u8; hash_len];
        argon
            .hash_password_into(preimage.as_bytes(), salt.as_bytes(), &mut hash)
            .map_err(|err| anyhow!("argon2: {err}"))?;
        let mut word = [0u8; 8];
        word.copy_from_slice(&hash[..8]);
        if u64::from_be_bytes(word) < threshold {
            return Ok((nonce, hex::encode(hash)));
        }
        nonce = nonce.wrapping_add(1);
    }
    Err(anyhow!("faucet proof-of-work exceeded the 50s challenge window"))
}

/// Refuse unless the RPC reports the Sui testnet chain id. Errors and
/// timeouts refuse too.
async fn require_testnet(client: &mut sui_rpc::Client, rpc: &str) -> Result<()> {
    let request = sui_rpc::proto::sui::rpc::v2::GetServiceInfoRequest::default();
    let reply = tokio::time::timeout(Duration::from_secs(15), client.ledger_client().get_service_info(request))
        .await
        .map_err(|_| anyhow!("refusing to sign: {rpc} did not report its chain id in time"))?
        .map_err(|err| anyhow!("refusing to sign: could not read the chain id of {rpc}: {}", err.message()))?
        .into_inner();
    let chain_id = reply.chain_id.unwrap_or_default();
    if chain_id != TESTNET_CHAIN_ID {
        let name = reply.chain.unwrap_or_default();
        return Err(anyhow!(
            "refusing to sign: {rpc} is on chain {} ({}), Mnemos only writes to Sui testnet",
            if name.is_empty() { "unknown" } else { name.as_str() },
            if chain_id.is_empty() { "no chain id" } else { chain_id.as_str() }
        ));
    }
    Ok(())
}

/// `require_testnet` on a fresh client, for a check before any other work.
pub(crate) async fn check_rpc(rpc: &str) -> Result<()> {
    let mut client = sui_rpc::Client::new(rpc).map_err(|err| anyhow!("sui rpc: {err}"))?;
    require_testnet(&mut client, rpc).await
}

async fn execute_tx(rpc: &str, signer: &Ed25519PrivateKey, tx: TransactionBuilder) -> Result<String> {
    Ok(execute_tx_effects(rpc, signer, tx).await?.0)
}

/// Sign and execute; return the digest and the (id, type) of every created object.
pub(crate) async fn execute_tx_effects(rpc: &str, signer: &Ed25519PrivateKey, tx: TransactionBuilder) -> Result<(String, Vec<(String, String)>)> {
    let mut client = sui_rpc::Client::new(rpc).map_err(|err| anyhow!("sui rpc: {err}"))?;
    require_testnet(&mut client, rpc).await?;
    let built = tx.build(&mut client).await.map_err(|err| anyhow!("transaction build: {err}"))?;
    let digest = built.digest().to_string();
    let signature = signer.sign_transaction(&built).map_err(|err| anyhow!("sign: {err}"))?;
    let request = sui_rpc::proto::sui::rpc::v2::ExecuteTransactionRequest::default()
        .with_transaction(built)
        .with_signatures(vec![signature.into()])
        .with_read_mask(FieldMask::from_paths(["digest", "effects.status", "effects.changed_objects"]));
    let reply = client
        .execute_transaction_and_wait_for_checkpoint(request, Duration::from_secs(60))
        .await
        .map_err(|err| anyhow!("execute {digest}: {err}"))?
        .into_inner();
    let mut created = Vec::new();
    if let Some(effects) = reply.transaction.and_then(|done| done.effects) {
        for change in effects.changed_objects {
            if change.id_operation == Some(2) {
                created.push((change.object_id.unwrap_or_default(), change.object_type.unwrap_or_default()));
            }
        }
    }
    Ok((digest, created))
}

pub(crate) fn one_created(digest: &str, created: &[(String, String)], matches: impl Fn(&str) -> bool) -> Result<Option<String>> {
    let found: Vec<&String> = created.iter().filter(|(_, kind)| matches(kind)).map(|(id, _)| id).collect();
    match found.len() {
        0 => Ok(None),
        1 => Ok(Some(found[0].clone())),
        n => Err(anyhow!("transaction {digest} created {n} matching objects")),
    }
}

async fn publish_package(cfg: &Config, signer: &Ed25519PrivateKey, sender: Address, modules_json: &str) -> Result<String> {
    let parsed: Value = serde_json::from_str(modules_json).context("bytecode json")?;
    let modules = parsed["modules"].as_array().context("modules")?;
    let mut bytecode = Vec::new();
    for module in modules {
        let text = module.as_str().context("module base64")?;
        bytecode.push(base64::engine::general_purpose::STANDARD.decode(text).context("module decode")?);
    }
    let mut dependencies = Vec::new();
    for dep in parsed["dependencies"].as_array().context("dependencies")? {
        let text = dep.as_str().context("dependency")?;
        dependencies.push(text.parse::<Address>().map_err(|err| anyhow!("dependency {text}: {err}"))?);
    }
    let mut tx = TransactionBuilder::new();
    tx.set_sender(sender);
    tx.set_gas_budget(100_000_000);
    let cap = tx.publish(bytecode, dependencies);
    let recipient = tx.pure(&sender);
    tx.transfer_objects(vec![cap], recipient);
    let (digest, created) = execute_tx_effects(&cfg.sui_rpc, signer, tx).await?;
    if let Some(package_id) = one_created(&digest, &created, |kind| kind == "package")? {
        return Ok(package_id);
    }
    wait_for_package(cfg, sender).await.with_context(|| format!("published in {digest} but the UpgradeCap was not visible"))
}

async fn create_index(cfg: &Config, signer: &Ed25519PrivateKey, sender: Address, package_id: &str, pubkey: &[u8; 32]) -> Result<String> {
    let package: Address = package_id.parse().context("package id")?;
    let mut tx = TransactionBuilder::new();
    tx.set_sender(sender);
    tx.set_gas_budget(30_000_000);
    let key_arg = tx.pure(&pubkey.to_vec());
    tx.move_call(
        Function::new(package, Identifier::from_static("memory_index"), Identifier::from_static("create")),
        vec![key_arg],
    );
    let (digest, created) = execute_tx_effects(&cfg.sui_rpc, signer, tx).await?;
    let index_id = one_created(&digest, &created, |kind| kind.ends_with("::memory_index::MemoryIndex"))?
        .ok_or_else(|| anyhow!("create transaction {digest} did not create a MemoryIndex"))?;
    let mut last = String::new();
    for _ in 0..20 {
        match read_object(cfg, &index_id).await {
            Ok(_) => return Ok(index_id),
            Err(err) => last = err.to_string(),
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    Err(anyhow!("MemoryIndex {index_id} from {digest} was not readable: {last}"))
}

async fn wait_for_package(cfg: &Config, owner: Address) -> Result<String> {
    for _ in 0..20 {
        for object in read_owned(cfg, owner, "0x2::package::UpgradeCap").await? {
            if let Some(package) = object.json["package"].as_str() {
                return Ok(package.to_string());
            }
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    Err(anyhow!("UpgradeCap was not visible"))
}

// ---- Reads: fullnode gRPC first, GraphQL only when provably fresh ----

const GRAPHQL_MAX_LAG: u64 = 100;
const VERIFY_TTL: Duration = Duration::from_secs(300);

pub(crate) struct ChainObject {
    pub(crate) version: u64,
    pub(crate) type_repr: String,
    pub(crate) json: Value,
    pub(crate) source: &'static str,
}

enum ReadFail {
    NotFound(String),
    Chain(anyhow::Error),
    Other(anyhow::Error),
}

impl From<ReadFail> for anyhow::Error {
    fn from(fail: ReadFail) -> Self {
        match fail {
            ReadFail::NotFound(text) => anyhow!(text),
            ReadFail::Chain(err) | ReadFail::Other(err) => err,
        }
    }
}

pub(crate) fn norm_address(text: &str) -> Result<String> {
    let raw = text.trim().trim_start_matches("0x").trim_start_matches("0X").to_lowercase();
    if raw.is_empty() || raw.len() > 64 || !raw.chars().all(|ch| ch.is_ascii_hexdigit()) {
        return Err(anyhow!("not a Sui address: {text:?}"));
    }
    Ok(format!("0x{raw:0>64}"))
}

fn verified_rpcs() -> &'static std::sync::Mutex<std::collections::HashMap<String, std::time::Instant>> {
    static CACHE: std::sync::OnceLock<std::sync::Mutex<std::collections::HashMap<String, std::time::Instant>>> =
        std::sync::OnceLock::new();
    CACHE.get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()))
}

/// A client for sui_rpc after confirming it is Sui testnet (cached a few minutes).
async fn read_client(cfg: &Config) -> std::result::Result<sui_rpc::Client, ReadFail> {
    let mut client = sui_rpc::Client::new(&cfg.sui_rpc).map_err(|err| ReadFail::Other(anyhow!("sui rpc: {err}")))?;
    let fresh = verified_rpcs()
        .lock()
        .unwrap_or_else(|err| err.into_inner())
        .get(&cfg.sui_rpc)
        .map(|at| at.elapsed() < VERIFY_TTL)
        .unwrap_or(false);
    if !fresh {
        require_testnet(&mut client, &cfg.sui_rpc).await.map_err(ReadFail::Chain)?;
        verified_rpcs()
            .lock()
            .unwrap_or_else(|err| err.into_inner())
            .insert(cfg.sui_rpc.clone(), std::time::Instant::now());
    }
    Ok(client)
}

fn object_mask() -> FieldMask {
    FieldMask::from_paths(["object_id", "version", "digest", "object_type", "json"])
}

fn proto_json(value: &prost_types::Value) -> Value {
    use prost_types::value::Kind;
    match &value.kind {
        None | Some(Kind::NullValue(_)) => Value::Null,
        Some(Kind::BoolValue(flag)) => Value::Bool(*flag),
        Some(Kind::NumberValue(number)) => serde_json::Number::from_f64(*number).map(Value::Number).unwrap_or(Value::Null),
        Some(Kind::StringValue(text)) => Value::String(text.clone()),
        Some(Kind::ListValue(list)) => Value::Array(list.values.iter().map(proto_json).collect()),
        Some(Kind::StructValue(map)) => Value::Object(map.fields.iter().map(|(key, item)| (key.clone(), proto_json(item))).collect()),
    }
}

fn chain_object(object: sui_rpc::proto::sui::rpc::v2::Object, source: &'static str) -> ChainObject {
    ChainObject {
        version: object.version.unwrap_or(0),
        type_repr: object.object_type.clone().unwrap_or_default(),
        json: object.json.as_deref().map(proto_json).unwrap_or(Value::Null),
        source,
    }
}

async fn grpc_object(cfg: &Config, object_id: &str) -> std::result::Result<ChainObject, ReadFail> {
    let mut client = read_client(cfg).await?;
    let request = sui_rpc::proto::sui::rpc::v2::GetObjectRequest::default()
        .with_object_id(object_id)
        .with_read_mask(object_mask());
    let reply = tokio::time::timeout(Duration::from_secs(20), client.ledger_client().get_object(request))
        .await
        .map_err(|_| ReadFail::Other(anyhow!("fullnode GetObject timed out")))?;
    let reply = match reply {
        Ok(reply) => reply.into_inner(),
        Err(status) if status.code() == tonic::Code::NotFound => {
            return Err(ReadFail::NotFound(format!("Sui object {object_id} was not found")))
        }
        Err(status) => return Err(ReadFail::Other(anyhow!("fullnode GetObject: {:?} {}", status.code(), status.message()))),
    };
    let object = reply.object.ok_or_else(|| ReadFail::Other(anyhow!("fullnode returned no object")))?;
    let returned = object.object_id.clone().unwrap_or_default();
    let same = matches!((norm_address(&returned), norm_address(object_id)), (Ok(a), Ok(b)) if a == b);
    if !same {
        return Err(ReadFail::Other(anyhow!("fullnode returned object {returned} for {object_id}")));
    }
    Ok(chain_object(object, "grpc"))
}

async fn grpc_owned(cfg: &Config, owner: Address, type_name: &str) -> std::result::Result<Vec<ChainObject>, ReadFail> {
    let mut client = read_client(cfg).await?;
    let mut out = Vec::new();
    let mut token: Option<Vec<u8>> = None;
    for _ in 0..20 {
        let mut request = sui_rpc::proto::sui::rpc::v2::ListOwnedObjectsRequest::default()
            .with_owner(owner.to_string())
            .with_object_type(type_name)
            .with_page_size(50u32)
            .with_read_mask(object_mask());
        if let Some(page) = token.take() {
            request = request.with_page_token(page);
        }
        let reply = tokio::time::timeout(Duration::from_secs(20), client.state_client().list_owned_objects(request))
            .await
            .map_err(|_| ReadFail::Other(anyhow!("fullnode ListOwnedObjects timed out")))?
            .map_err(|status| ReadFail::Other(anyhow!("fullnode ListOwnedObjects: {:?} {}", status.code(), status.message())))?
            .into_inner();
        out.extend(reply.objects.into_iter().map(|object| chain_object(object, "grpc")));
        match reply.next_page_token {
            Some(page) if !page.is_empty() => token = Some(page.to_vec()),
            _ => break,
        }
    }
    Ok(out)
}

async fn grpc_balance(cfg: &Config, owner: Address) -> std::result::Result<u64, ReadFail> {
    let mut client = read_client(cfg).await?;
    let request = sui_rpc::proto::sui::rpc::v2::GetBalanceRequest::default()
        .with_owner(owner.to_string())
        .with_coin_type("0x2::sui::SUI");
    let reply = tokio::time::timeout(Duration::from_secs(20), client.state_client().get_balance(request))
        .await
        .map_err(|_| ReadFail::Other(anyhow!("fullnode GetBalance timed out")))?
        .map_err(|status| ReadFail::Other(anyhow!("fullnode GetBalance: {:?} {}", status.code(), status.message())))?
        .into_inner();
    Ok(reply.balance.and_then(|balance| balance.balance).unwrap_or(0))
}

/// Ok(lag) when GraphQL is on Sui testnet and within graphql_max_lag of the fullnode.
async fn check_graphql_fresh(cfg: &Config) -> Result<u64> {
    let mut client = sui_rpc::Client::new(&cfg.sui_rpc).map_err(|err| anyhow!("sui rpc: {err}"))?;
    let info = tokio::time::timeout(
        Duration::from_secs(10),
        client.ledger_client().get_service_info(sui_rpc::proto::sui::rpc::v2::GetServiceInfoRequest::default()),
    )
    .await
    .map_err(|_| anyhow!("cannot check GraphQL freshness: fullnode timed out"))?
    .map_err(|status| anyhow!("cannot check GraphQL freshness: fullnode unreachable ({})", status.message()))?
    .into_inner();
    if info.chain_id.as_deref() != Some(TESTNET_CHAIN_ID) {
        return Err(anyhow!("cannot check GraphQL freshness: sui_rpc is not Sui testnet"));
    }
    let value = graphql(&cfg.graphql_url, "{ chainIdentifier checkpoint { sequenceNumber } }", json!({}))
        .await
        .map_err(|err| anyhow!("GraphQL unreachable ({err})"))?;
    let chain = value["data"]["chainIdentifier"].as_str().unwrap_or("");
    if chain != TESTNET_CHAIN_ID {
        return Err(anyhow!("GraphQL is on chain {chain:?}, not Sui testnet"));
    }
    let indexed = as_u64(&value["data"]["checkpoint"]["sequenceNumber"]);
    let lag = info.checkpoint_height.unwrap_or(0).saturating_sub(indexed);
    if lag > cfg.graphql_max_lag {
        return Err(anyhow!(
            "GraphQL is {lag} checkpoints behind the fullnode (limit {}); refusing a stale read",
            cfg.graphql_max_lag
        ));
    }
    Ok(lag)
}

/// Decide whether a failed fullnode read may be retried on GraphQL. Chain-guard
/// failures and not-found answers are final; so is read_source = grpc.
async fn fallback_allowed(cfg: &Config, what: &str, fail: ReadFail) -> Result<()> {
    let err = match fail {
        ReadFail::NotFound(text) => return Err(anyhow!(text)),
        ReadFail::Chain(err) => return Err(err),
        ReadFail::Other(err) => err,
    };
    match cfg.read_source.as_str() {
        "auto" => {}
        "grpc" => return Err(anyhow!("{what}: fullnode read failed ({err})")),
        other => return Err(anyhow!("read_source must be auto or grpc, not {other:?}")),
    }
    check_graphql_fresh(cfg)
        .await
        .map(|_| ())
        .map_err(|stale| anyhow!("{what}: fullnode read failed ({err}) and {stale}"))
}

pub(crate) async fn read_object(cfg: &Config, object_id: &str) -> Result<ChainObject> {
    match grpc_object(cfg, object_id).await {
        Ok(object) => Ok(object),
        Err(fail) => {
            fallback_allowed(cfg, &format!("read {object_id}"), fail).await?;
            let query = r#"
                query($id: SuiAddress!) {
                  object(address: $id) { address version asMoveObject { contents { type { repr } json } } }
                }
            "#;
            let value = graphql(&cfg.graphql_url, query, json!({"id": object_id})).await?;
            let object = &value["data"]["object"];
            if object.is_null() {
                return Err(anyhow!("Sui object {object_id} was not found"));
            }
            Ok(ChainObject {
                version: as_u64(&object["version"]),
                type_repr: object["asMoveObject"]["contents"]["type"]["repr"].as_str().unwrap_or("").to_string(),
                json: object["asMoveObject"]["contents"]["json"].clone(),
                source: "graphql",
            })
        }
    }
}

async fn read_owned(cfg: &Config, owner: Address, type_name: &str) -> Result<Vec<ChainObject>> {
    match grpc_owned(cfg, owner, type_name).await {
        Ok(objects) => Ok(objects),
        Err(fail) => {
            fallback_allowed(cfg, &format!("list {type_name}"), fail).await?;
            let query = r#"
                query($owner: SuiAddress!, $type: String!) {
                  objects(first: 50, filter: { owner: $owner, type: $type }) {
                    nodes { version asMoveObject { contents { type { repr } json } } }
                  }
                }
            "#;
            let value = graphql(&cfg.graphql_url, query, json!({"owner": owner.to_string(), "type": type_name})).await?;
            Ok(value["data"]["objects"]["nodes"]
                .as_array()
                .cloned()
                .unwrap_or_default()
                .iter()
                .map(|node| ChainObject {
                    version: as_u64(&node["version"]),
                    type_repr: node["asMoveObject"]["contents"]["type"]["repr"].as_str().unwrap_or("").to_string(),
                    json: node["asMoveObject"]["contents"]["json"].clone(),
                    source: "graphql",
                })
                .collect())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn addresses_normalize() {
        assert_eq!(norm_address("0x2").unwrap(), format!("0x{:0>64}", "2"));
        assert_eq!(norm_address("0xABC").unwrap(), norm_address("0x0abc").unwrap());
        assert!(norm_address("0xzz").is_err());
        assert!(norm_address("").is_err());
    }

    #[test]
    fn proto_json_matches_graphql_shape() {
        use prost_types::value::Kind;
        use prost_types::{ListValue, Struct, Value as PValue};
        let text = |s: &str| PValue { kind: Some(Kind::StringValue(s.into())) };
        let mut fields = std::collections::BTreeMap::new();
        fields.insert("version".to_string(), text("2"));
        fields.insert("snapshots".to_string(), PValue { kind: Some(Kind::ListValue(ListValue { values: vec![text("AA==")] })) });
        let value = PValue { kind: Some(Kind::StructValue(Struct { fields })) };
        assert_eq!(proto_json(&value), json!({"version": "2", "snapshots": ["AA=="]}));
    }

    fn cfg(mode: &str) -> Config {
        let mut cfg = parse_config("{}");
        cfg.read_source = mode.into();
        cfg.sui_rpc = "http://127.0.0.1:9".into();
        cfg.graphql_url = "http://127.0.0.1:9/graphql".into();
        cfg
    }

    #[test]
    fn not_found_and_chain_failures_never_fall_back() {
        let not_found = block_on(fallback_allowed(&cfg("auto"), "read", ReadFail::NotFound("gone".into())));
        assert_eq!(not_found.unwrap_err().to_string(), "gone");
        let chain = block_on(fallback_allowed(&cfg("auto"), "read", ReadFail::Chain(anyhow!("refusing to sign: mainnet"))));
        assert!(chain.unwrap_err().to_string().contains("refusing to sign"));
    }

    #[test]
    fn grpc_mode_never_falls_back() {
        let err = block_on(fallback_allowed(&cfg("grpc"), "read", ReadFail::Other(anyhow!("down")))).unwrap_err();
        assert!(err.to_string().contains("fullnode read failed (down)"));
    }

    #[test]
    fn unverifiable_freshness_refuses() {
        let err = block_on(fallback_allowed(&cfg("auto"), "read", ReadFail::Other(anyhow!("down")))).unwrap_err();
        assert!(err.to_string().contains("cannot check GraphQL freshness"), "{err}");
    }

    #[test]
    fn defaults() {
        let cfg = parse_config("{}");
        assert_eq!(cfg.read_source, "auto");
        assert_eq!(cfg.graphql_max_lag, GRAPHQL_MAX_LAG);
    }
}
