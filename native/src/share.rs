//! BETA (testnet preview): cooperative sharing lane, testnet only, `seal` feature.
//!
//! A shared space is a `mnemos_share::space::Space` bound to a revocable
//! `seal_allowlist::allowlist::Allowlist`. Entries are encrypted with Seal
//! (`sui-seal-client`) to the key id `[allowlist id][epoch][nonce]`, stored on Walrus,
//! and referenced from the Space. Removing a member bumps the allowlist epoch, so new
//! entries use ids the removed member never obtained a key for.
//!
//! Key servers are configurable per space (BETA): the Mysten open testnet servers
//! (default, 2-of-2, as in the first preview), the Mysten testnet committee (3-of-5 behind
//! the Mysten aggregator, seen as one server) or an explicit list with a threshold.
//! Readers do not need to know which: `pull` reads the key servers and the threshold from
//! each entry's encrypted header and only contacts servers it trusts.
//!
//! This module is separate from the personal memory path: it does not read or write the
//! MemoryIndex, the local store, or the keys derived from `MNEMOS_SECRET` for
//! encryption. It only reuses the transport helpers (Walrus, fullnode reads, the
//! testnet guard).

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::str::FromStr;
use std::sync::Mutex;
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use sui_crypto::ed25519::Ed25519PrivateKey;
use sui_crypto::SuiSigner;
use sui_rpc::field::{FieldMask, FieldMaskUtil};
use sui_sdk_types::{Address, Identifier};
use sui_seal_client::{
    ApprovalArg, ApprovalTx, DerivedKeys, SealClient, SealConfig, SessionKey, MYSTEN_TESTNET_1, MYSTEN_TESTNET_2,
    MYSTEN_TESTNET_AGGREGATOR, MYSTEN_TESTNET_COMMITTEE,
};
use sui_transaction_builder::{Function, ObjectInput, TransactionBuilder};

use crate::engine::{self, Config};

/// Allowlist package from sui-seal-client (owner `0xa0ea…8c06`, testnet).
pub const DEFAULT_SEAL_PACKAGE: &str = "0xe10fbae08333748f310adc926904980c6ced589a75a774bff4dfc462567d7f1b";
/// `mnemos_share::space` package (move/SharedSpace, testnet).
pub const DEFAULT_SPACE_PACKAGE: &str = "0xa40e74c6ae866f18e86e9dcdfc9d29af436597264991fc81b4a289519f3500d1";
/// Bytes of random nonce appended to the key id prefix.
pub const NONCE_LEN: usize = 16;
/// Domain tag of the plaintext format of one shared entry.
pub const ENTRY_FORMAT: &str = "mnemos-share-entry-v1";
const SESSION_TTL_MIN: u16 = 10;
const GAS_BUDGET: u64 = 50_000_000;

/// Key server profile of the first preview: Mysten open testnet servers, 2-of-2.
pub const PROFILE_TESTNET: &str = "testnet";
/// Mysten testnet committee (3-of-5 behind the Mysten aggregator), threshold 1. BETA.
pub const PROFILE_COMMITTEE: &str = "committee";

#[derive(Clone, Debug)]
pub struct ShareConfig {
    pub seal_package: Address,
    pub space_package: Address,
    /// `testnet` or `committee`: where `key_servers` and `threshold` default from.
    pub profile: String,
    /// Key servers new entries are encrypted to, in share order.
    pub key_servers: Vec<Address>,
    /// Number of `key_servers` needed to decrypt.
    pub threshold: u8,
    /// Aggregator URL of each committee key server. The Mysten testnet committee is
    /// always known; other committees must be listed here.
    pub aggregators: BTreeMap<Address, String>,
}

impl ShareConfig {
    /// Reads the share settings from the config JSON:
    /// `seal_package`, `space_package`, `key_server_profile` (`testnet` | `committee`),
    /// `key_servers` (list of key server object ids), `threshold`, `aggregators`
    /// (`{key server id: https URL}`). Missing keys keep the first preview's behaviour.
    pub fn from_json(raw: &str) -> Result<Self> {
        let value: Value = serde_json::from_str(raw).unwrap_or(Value::Null);
        let text = |key: &str, default: &str| -> String {
            value.get(key).and_then(Value::as_str).filter(|item| !item.is_empty()).unwrap_or(default).to_string()
        };
        let seal_package = Address::from_str(&text("seal_package", DEFAULT_SEAL_PACKAGE)).context("seal_package")?;
        let space_package =
            Address::from_str(&text("space_package", DEFAULT_SPACE_PACKAGE)).context("space_package")?;
        let profile = text("key_server_profile", PROFILE_TESTNET);
        let (profile_servers, profile_threshold) = match profile.as_str() {
            PROFILE_TESTNET => {
                let preset = SealConfig::testnet();
                (preset.key_servers, preset.threshold)
            }
            PROFILE_COMMITTEE => (vec![committee_id()], 1),
            other => {
                return Err(anyhow!(
                    "key_server_profile {other:?}: expected {PROFILE_TESTNET:?} or {PROFILE_COMMITTEE:?}"
                ))
            }
        };
        let key_servers = match value.get("key_servers") {
            None | Some(Value::Null) => profile_servers,
            Some(Value::Array(items)) if !items.is_empty() => items
                .iter()
                .map(|item| Address::from_str(item.as_str().unwrap_or("")).context("key_servers"))
                .collect::<Result<Vec<_>>>()?,
            Some(_) => return Err(anyhow!("key_servers must be a non-empty list of key server object ids")),
        };
        let distinct: BTreeSet<&Address> = key_servers.iter().collect();
        if distinct.len() != key_servers.len() {
            return Err(anyhow!("key_servers lists the same server twice (weighted servers are not supported)"));
        }
        let threshold = match value.get("threshold") {
            None | Some(Value::Null) => profile_threshold,
            Some(item) => {
                let raw = item.as_u64().ok_or_else(|| anyhow!("threshold must be a positive integer"))?;
                u8::try_from(raw).map_err(|_| anyhow!("threshold out of range"))?
            }
        };
        if threshold == 0 || threshold as usize > key_servers.len() {
            return Err(anyhow!("threshold {threshold} must be between 1 and {}", key_servers.len()));
        }
        let mut aggregators = BTreeMap::from([(committee_id(), MYSTEN_TESTNET_AGGREGATOR.to_string())]);
        match value.get("aggregators") {
            None | Some(Value::Null) => {}
            Some(Value::Object(items)) => {
                for (id, url) in items {
                    let id = Address::from_str(id).context("aggregators: key server id")?;
                    let url = url.as_str().ok_or_else(|| anyhow!("aggregators: URL of {id} must be a string"))?;
                    if !url.starts_with("https://") || url.len() <= "https://".len() {
                        return Err(anyhow!("aggregators: URL of {id} must start with https://"));
                    }
                    aggregators.insert(id, url.trim_end_matches('/').to_string());
                }
            }
            Some(_) => return Err(anyhow!("aggregators must be an object {{key server id: https URL}}")),
        }
        Ok(Self { seal_package, space_package, profile, key_servers, threshold, aggregators })
    }

    /// Key servers `pull` accepts in an entry header: the configured ones, the Mysten
    /// testnet servers and committee, and the committees listed in `aggregators`.
    pub fn trusted_servers(&self) -> BTreeSet<Address> {
        let mut trusted: BTreeSet<Address> = self.key_servers.iter().copied().collect();
        trusted.extend(self.aggregators.keys().copied());
        for known in [MYSTEN_TESTNET_1, MYSTEN_TESTNET_2, MYSTEN_TESTNET_COMMITTEE] {
            trusted.insert(Address::from_str(known).expect("valid constant"));
        }
        trusted
    }

    /// The key server settings, as reported by `create`, `push` and `status`.
    pub fn seal_json(&self) -> Value {
        json!({
            "key_server_profile": self.profile,
            "key_servers": self.key_servers.iter().map(Address::to_string).collect::<Vec<_>>(),
            "threshold": self.threshold,
        })
    }
}

fn committee_id() -> Address {
    Address::from_str(MYSTEN_TESTNET_COMMITTEE).expect("valid constant")
}

/// Key servers and threshold of one encrypted entry, from its public header.
#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ServerSet {
    pub servers: Vec<Address>,
    pub threshold: u8,
}

impl ServerSet {
    /// Checks a header's server set against the trusted servers. Weighted sets (a server
    /// listed several times) are refused: this preview never writes them.
    pub fn from_header(servers: &[Address], threshold: u8, trusted: &BTreeSet<Address>) -> Result<Self> {
        let distinct: BTreeSet<&Address> = servers.iter().collect();
        if distinct.len() != servers.len() {
            return Err(anyhow!("weighted key servers are not supported by this preview"));
        }
        if threshold == 0 || threshold as usize > servers.len() {
            return Err(anyhow!("header threshold {threshold} is not between 1 and {}", servers.len()));
        }
        if let Some(unknown) = servers.iter().find(|id| !trusted.contains(id)) {
            return Err(anyhow!("untrusted key server {unknown} (add it to key_servers or aggregators to accept it)"));
        }
        Ok(Self { servers: servers.to_vec(), threshold })
    }

    fn to_json(&self) -> Value {
        json!({
            "key_servers": self.servers.iter().map(Address::to_string).collect::<Vec<_>>(),
            "threshold": self.threshold,
        })
    }
}

/// How a failed key request is reported.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FetchFailure {
    /// The approval used an owned object that the reader does not own (`wrong_owner`).
    WrongOwner,
    /// `seal_approve` refused (`no_access`).
    NoAccess,
    /// Anything else: transport, threshold not reached for other reasons... (fatal).
    Other,
}

pub fn classify_fetch_error(err: &sui_seal_client::Error) -> FetchFailure {
    if err.is_wrong_owner() {
        FetchFailure::WrongOwner
    } else if err.is_no_access() {
        FetchFailure::NoAccess
    } else {
        FetchFailure::Other
    }
}

/// `[list id (32)][epoch (u64 LE)][nonce]`, the id format `allowlist::seal_approve` checks.
pub fn key_id(list: &Address, epoch: u64, nonce: &[u8]) -> Vec<u8> {
    let mut id = Vec::with_capacity(40 + nonce.len());
    id.extend_from_slice(list.as_bytes());
    id.extend_from_slice(&epoch.to_le_bytes());
    id.extend_from_slice(nonce);
    id
}

/// Splits a key id into (list id, epoch). Fails on ids too short to carry a nonce.
pub fn parse_key_id(id: &[u8]) -> Result<(Address, u64)> {
    if id.len() <= 40 {
        return Err(anyhow!("key id has {} bytes, expected more than 40", id.len()));
    }
    let list = Address::from_bytes(&id[..32]).map_err(|err| anyhow!("key id: {err}"))?;
    let mut epoch = [0u8; 8];
    epoch.copy_from_slice(&id[32..40]);
    Ok((list, u64::from_le_bytes(epoch)))
}

/// Plaintext of one entry: a small JSON document with a format tag.
pub fn entry_plaintext(text: &str, kind: &str, author: &Address, ts_ms: i64) -> Vec<u8> {
    json!({"format": ENTRY_FORMAT, "text": text, "kind": kind, "author": author.to_string(), "ts": ts_ms})
        .to_string()
        .into_bytes()
}

pub fn parse_entry_plaintext(bytes: &[u8]) -> Result<Value> {
    let value: Value = serde_json::from_slice(bytes).context("entry plaintext is not JSON")?;
    if value["format"] != ENTRY_FORMAT {
        return Err(anyhow!("entry plaintext has format {}, expected {ENTRY_FORMAT}", value["format"]));
    }
    Ok(value)
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SpaceEntry {
    pub index: usize,
    pub blob_id: String,
    pub key_id: Vec<u8>,
    pub epoch: u64,
    pub author: String,
    pub blob_sha256: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SpaceState {
    pub list_id: String,
    pub creator: String,
    pub entries: Vec<SpaceEntry>,
}

/// Parses the JSON of a `space::Space` object (fullnode or GraphQL shape).
pub fn parse_space(json_value: &Value) -> Result<SpaceState> {
    let list_id = engine::norm_address(json_value["list_id"].as_str().unwrap_or(""))?;
    let creator = engine::norm_address(json_value["creator"].as_str().unwrap_or(""))?;
    let mut entries = Vec::new();
    for (index, item) in json_value["entries"].as_array().cloned().unwrap_or_default().iter().enumerate() {
        let blob_sha256: [u8; 32] = engine::as_bytes(&item["blob_sha256"])?
            .try_into()
            .map_err(|_| anyhow!("entry {index}: blob_sha256 is not 32 bytes"))?;
        let key_id = engine::as_bytes(&item["key_id"])?;
        let epoch = engine::as_u64(&item["epoch"]);
        let (list, id_epoch) = parse_key_id(&key_id)?;
        if engine::norm_address(&list.to_string())? != list_id || id_epoch != epoch {
            return Err(anyhow!("entry {index}: key id does not match the space allowlist and epoch"));
        }
        entries.push(SpaceEntry {
            index,
            blob_id: item["blob_id"].as_str().unwrap_or("").to_string(),
            key_id,
            epoch,
            author: engine::norm_address(item["author"].as_str().unwrap_or(""))?,
            blob_sha256,
        });
    }
    Ok(SpaceState { list_id, creator, entries })
}

/// Holds one signer, a Seal client, a session key and the keys fetched so far.
///
/// Keys live only in memory for the lifetime of this object; nothing is written to disk.
pub struct Share {
    cfg: Config,
    share: ShareConfig,
    signer: Ed25519PrivateKey,
    address: Address,
    runtime: tokio::runtime::Runtime,
    clients: Mutex<HashMap<ServerSet, SealClient>>,
    session: Mutex<Option<SessionKey>>,
    keys: Mutex<DerivedKeys>,
}

impl Share {
    pub fn new(config_json: &str, signer_key: &str, mnemos_secret: &str) -> Result<Self> {
        let cfg = engine::parse_config(config_json);
        let share = ShareConfig::from_json(config_json)?;
        let signer = if signer_key.trim().is_empty() {
            if mnemos_secret.is_empty() {
                return Err(anyhow!("no signer: pass a share key or MNEMOS_SECRET"));
            }
            let keys = crate::chain::derive_keys(mnemos_secret)?;
            Ed25519PrivateKey::new(keys.sui_seed)
        } else {
            engine::load_sui_key(signer_key, [0u8; 32])?
        };
        let address = signer.public_key().derive_address();
        let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().worker_threads(2).build()?;
        Ok(Self {
            cfg,
            share,
            signer,
            address,
            runtime,
            clients: Mutex::new(HashMap::new()),
            session: Mutex::new(None),
            keys: Mutex::new(DerivedKeys::default()),
        })
    }

    pub fn address(&self) -> String {
        self.address.to_string()
    }

    /// Seal client for one server set, connected once and reused.
    fn seal_client(&self, set: &ServerSet) -> Result<SealClient> {
        let mut slot = self.clients.lock().unwrap_or_else(|err| err.into_inner());
        if let Some(client) = slot.get(set) {
            return Ok(client.clone());
        }
        let mut builder = SealConfig::builder().threshold(set.threshold).fullnode_url(self.cfg.sui_rpc.clone());
        for id in &set.servers {
            builder = match self.share.aggregators.get(id) {
                Some(url) => builder.committee(*id, url.clone()),
                None => builder.key_server(*id),
            };
        }
        let config = builder.build().map_err(|err| anyhow!("seal config: {err}"))?;
        let rpc = self.cfg.sui_rpc.clone();
        let client = self.runtime.block_on(async move {
            engine::check_rpc(&rpc).await?;
            SealClient::connect(config).await.map_err(|err| anyhow!("seal: {err}"))
        })?;
        slot.insert(set.clone(), client.clone());
        Ok(client)
    }

    fn write_set(&self) -> ServerSet {
        ServerSet { servers: self.share.key_servers.clone(), threshold: self.share.threshold }
    }

    fn list_epoch(&self, list: &Address) -> Result<u64> {
        let object = self.runtime.block_on(engine::read_object(&self.cfg, &list.to_string()))?;
        if !object.type_repr.ends_with("::allowlist::Allowlist") {
            return Err(anyhow!("{list} is a {}, not an allowlist", object.type_repr));
        }
        if !object.type_repr.starts_with(&self.share.seal_package.to_string()) {
            return Err(anyhow!("allowlist {list} is not from package {}", self.share.seal_package));
        }
        Ok(engine::as_u64(&object.json["epoch"]))
    }

    pub fn space(&self, space: &str) -> Result<SpaceState> {
        let object = self.runtime.block_on(engine::read_object(&self.cfg, space))?;
        let expected = format!("{}::space::Space", self.share.space_package);
        if object.type_repr != expected {
            return Err(anyhow!("{space} is a {}, not a {expected}", object.type_repr));
        }
        parse_space(&object.json)
    }

    fn execute(&self, tx: TransactionBuilder) -> Result<(String, Vec<(String, String)>)> {
        let rpc = self.cfg.sui_rpc.clone();
        let signer = self.signer.clone();
        self.runtime.block_on(execute_checked(rpc, signer, tx))
    }

    /// Creates an allowlist, adds the sender as its first member, and creates a Space
    /// bound to it, in one transaction.
    pub fn create(&self) -> Result<Value> {
        let seal = self.share.seal_package;
        let mut tx = TransactionBuilder::new();
        tx.set_sender(self.address);
        tx.set_gas_budget(GAS_BUDGET);
        let created = tx.move_call(Function::new(seal, ident("allowlist"), ident("create")), vec![]);
        let parts = created.to_nested(2);
        let (cap, list) = (parts[0], parts[1]);
        let me = tx.pure(&self.address);
        tx.move_call(Function::new(seal, ident("allowlist"), ident("add")), vec![list, cap, me]);
        tx.move_call(Function::new(self.share.space_package, ident("space"), ident("create")), vec![list]);
        tx.move_call(Function::new(seal, ident("allowlist"), ident("share")), vec![list]);
        let to = tx.pure(&self.address);
        tx.transfer_objects(vec![cap], to);
        let (digest, created) = self.execute(tx)?;
        let find = |suffix: &str| -> Result<String> {
            engine::one_created(&digest, &created, |kind| kind.ends_with(suffix))?
                .ok_or_else(|| anyhow!("transaction {digest} created no {suffix}"))
        };
        let list_id = find("::allowlist::Allowlist")?;
        let cap_id = find("::allowlist::Cap")?;
        let space_id = find("::space::Space")?;
        self.wait_readable(&space_id)?;
        Ok(json!({
            "space_id": space_id,
            "list_id": list_id,
            "cap_id": cap_id,
            "owner": self.address(),
            "epoch": 0,
            "digest": digest,
            "seal_package": self.share.seal_package.to_string(),
            "space_package": self.share.space_package.to_string(),
            "seal": self.share.seal_json(),
        }))
    }

    fn wait_readable(&self, object_id: &str) -> Result<()> {
        let mut last = String::new();
        for _ in 0..20 {
            match self.runtime.block_on(engine::read_object(&self.cfg, object_id)) {
                Ok(_) => return Ok(()),
                Err(err) => last = err.to_string(),
            }
            std::thread::sleep(Duration::from_secs(1));
        }
        Err(anyhow!("{object_id} was not readable: {last}"))
    }

    fn admin(&self, function: &'static str, list: &str, cap: &str, member: &str) -> Result<Value> {
        let list_addr = Address::from_str(list).context("list id")?;
        let cap_addr = Address::from_str(cap).context("cap id")?;
        let member_addr = Address::from_str(member).context("member address")?;
        let before = self.list_epoch(&list_addr)?;
        let mut tx = TransactionBuilder::new();
        tx.set_sender(self.address);
        tx.set_gas_budget(GAS_BUDGET);
        let list_arg = tx.object(ObjectInput::new(list_addr).with_mutable(true));
        let cap_arg = tx.object(ObjectInput::new(cap_addr));
        let member_arg = tx.pure(&member_addr);
        tx.move_call(
            Function::new(self.share.seal_package, ident("allowlist"), Identifier::from_static(function)),
            vec![list_arg, cap_arg, member_arg],
        );
        let (digest, _) = self.execute(tx)?;
        let expected = if function == "remove" { before + 1 } else { before };
        let mut epoch = before;
        for _ in 0..20 {
            epoch = self.list_epoch(&list_addr)?;
            if epoch == expected {
                break;
            }
            std::thread::sleep(Duration::from_secs(1));
        }
        if epoch != expected {
            return Err(anyhow!("{function} in {digest}: epoch is {epoch}, expected {expected}"));
        }
        Ok(json!({"digest": digest, "member": member_addr.to_string(), "epoch_before": before, "epoch": epoch}))
    }

    pub fn add(&self, list: &str, cap: &str, member: &str) -> Result<Value> {
        self.admin("add", list, cap, member)
    }

    pub fn remove(&self, list: &str, cap: &str, member: &str) -> Result<Value> {
        self.admin("remove", list, cap, member)
    }

    /// Encrypts each entry under the current epoch, uploads it to Walrus and publishes
    /// the references in one transaction.
    pub fn push(&self, space: &str, entries_json: &str) -> Result<Value> {
        let items: Vec<Value> = serde_json::from_str(entries_json).context("entries json")?;
        if items.is_empty() {
            return Err(anyhow!("nothing to push"));
        }
        let state = self.space(space)?;
        let list = Address::from_str(&state.list_id)?;
        let epoch = self.list_epoch(&list)?;
        let client = self.seal_client(&self.write_set())?;
        let mut prepared = Vec::new();
        for item in &items {
            let text = item["text"].as_str().ok_or_else(|| anyhow!("entry without text"))?;
            let kind = item["kind"].as_str().unwrap_or("fact");
            let mut nonce = [0u8; NONCE_LEN];
            getrandom::getrandom(&mut nonce).map_err(|err| anyhow!("nonce: {err}"))?;
            let id = key_id(&list, epoch, &nonce);
            let plaintext = entry_plaintext(text, kind, &self.address, now_ms());
            let ciphertext = client
                .encrypt(self.share.seal_package, &id, &plaintext)
                .map_err(|err| anyhow!("seal encrypt: {err}"))?;
            let digest: [u8; 32] = Sha256::digest(&ciphertext).into();
            let blob_id =
                self.runtime.block_on(engine::walrus_put(&self.cfg.walrus_publisher, &ciphertext, self.cfg.epochs))?;
            prepared.push((blob_id, id, digest));
        }
        let space_addr = Address::from_str(space).context("space id")?;
        let mut tx = TransactionBuilder::new();
        tx.set_sender(self.address);
        tx.set_gas_budget(GAS_BUDGET);
        let space_arg = tx.object(ObjectInput::new(space_addr).with_mutable(true));
        let list_arg = tx.object(ObjectInput::new(list).with_mutable(false));
        for (blob_id, id, digest) in &prepared {
            let blob_arg = tx.pure(&blob_id.clone());
            let id_arg = tx.pure(id);
            let digest_arg = tx.pure(&digest.to_vec());
            tx.move_call(
                Function::new(self.share.space_package, ident("space"), ident("publish")),
                vec![space_arg, list_arg, blob_arg, id_arg, digest_arg],
            );
        }
        let (digest, _) = self.execute(tx)?;
        let first = state.entries.len();
        let mut published = Vec::new();
        for (offset, (blob_id, id, sha)) in prepared.iter().enumerate() {
            published.push(json!({
                "index": first + offset,
                "blob_id": blob_id,
                "key_id": hex::encode(id),
                "epoch": epoch,
                "blob_sha256": hex::encode(sha),
            }));
        }
        let expected = first + prepared.len();
        for _ in 0..20 {
            if self.space(space)?.entries.len() >= expected {
                break;
            }
            std::thread::sleep(Duration::from_secs(1));
        }
        Ok(json!({"digest": digest, "epoch": epoch, "entries": published, "seal": self.share.seal_json()}))
    }

    fn fetch(
        &self,
        client: &SealClient,
        list_arg: &ApprovalArg,
        ids: &[Vec<u8>],
    ) -> std::result::Result<DerivedKeys, sui_seal_client::Error> {
        let mut approval = ApprovalTx::new(self.share.seal_package);
        for id in ids {
            approval = approval.call("allowlist", "seal_approve", id, vec![list_arg.clone()])?;
        }
        let mut slot = self.session.lock().unwrap_or_else(|err| err.into_inner());
        if slot.as_ref().is_none_or(SessionKey::is_expired) {
            let session =
                self.runtime.block_on(client.session_key(self.share.seal_package, SESSION_TTL_MIN, &self.signer))?;
            *slot = Some(session);
        }
        let session = slot.as_ref().expect("session set above");
        self.runtime.block_on(client.fetch_keys(session, &approval))
    }

    /// Reads every entry of the space. Keys already held in memory are reused; missing
    /// keys are requested from the key servers named in each entry's header (`fetch =
    /// true`) or reported as `no_cached_key` (`fetch = false`, offline check of what was
    /// fetched before). Entries whose header names an untrusted or weighted server set are
    /// reported as `error` without contacting anyone.
    pub fn pull(&self, space: &str, fetch: bool) -> Result<Value> {
        let state = self.space(space)?;
        let list = Address::from_str(&state.list_id)?;
        let trusted = self.share.trusted_servers();
        let mut blobs = Vec::new();
        let mut sets: Vec<std::result::Result<ServerSet, String>> = Vec::new();
        for entry in &state.entries {
            let blob = self.runtime.block_on(engine::walrus_get(&self.cfg.walrus_aggregator, &entry.blob_id))?;
            let digest: [u8; 32] = Sha256::digest(&blob).into();
            if digest != entry.blob_sha256 {
                return Err(anyhow!("entry {}: Walrus blob {} does not match its sha256", entry.index, entry.blob_id));
            }
            let info = sui_seal_client::parse_encrypted_object(&blob)
                .map_err(|err| anyhow!("entry {}: {err}", entry.index))?;
            if info.package_id != self.share.seal_package || info.id != entry.key_id {
                return Err(anyhow!("entry {}: ciphertext header does not match the published key id", entry.index));
            }
            sets.push(
                ServerSet::from_header(&info.key_servers, info.threshold, &trusted).map_err(|err| err.to_string()),
            );
            blobs.push(blob);
        }
        let groups: BTreeSet<ServerSet> = sets.iter().filter_map(|set| set.as_ref().ok().cloned()).collect();
        let mut clients: HashMap<ServerSet, SealClient> = HashMap::new();
        for set in &groups {
            clients.insert(set.clone(), self.seal_client(set)?);
        }
        let mut refused: Vec<usize> = Vec::new();
        let mut wrong_owner: Vec<usize> = Vec::new();
        let mut errors: Vec<(usize, String)> = Vec::new();
        if fetch && !groups.is_empty() {
            let mut list_arg: Option<ApprovalArg> = None;
            for set in &groups {
                let client = &clients[set];
                let missing: Vec<usize> = {
                    let keys = self.keys.lock().unwrap_or_else(|err| err.into_inner());
                    state
                        .entries
                        .iter()
                        .zip(&sets)
                        .filter(|(_, entry_set)| entry_set.as_ref().ok() == Some(set))
                        .filter(|(entry, _)| {
                            keys.servers_for(&self.share.seal_package, &entry.key_id) < set.threshold as usize
                        })
                        .map(|(entry, _)| entry.index)
                        .collect()
                };
                if missing.is_empty() {
                    continue;
                }
                if list_arg.is_none() {
                    list_arg = Some(
                        self.runtime.block_on(client.shared_object(list)).map_err(|err| anyhow!("allowlist: {err}"))?,
                    );
                }
                let list_arg = list_arg.as_ref().expect("set above");
                let ids: Vec<Vec<u8>> = missing.iter().map(|i| state.entries[*i].key_id.clone()).collect();
                match self.fetch(client, list_arg, &ids) {
                    Ok(fetched) => self.keys.lock().unwrap_or_else(|err| err.into_inner()).extend(fetched),
                    Err(err) => match classify_fetch_error(&err) {
                        FetchFailure::WrongOwner => wrong_owner.extend(&missing),
                        FetchFailure::NoAccess => {
                            // Classify entry by entry: some ids may be approvable, others not.
                            for index in &missing {
                                match self.fetch(client, list_arg, std::slice::from_ref(&state.entries[*index].key_id))
                                {
                                    Ok(fetched) => {
                                        self.keys.lock().unwrap_or_else(|err| err.into_inner()).extend(fetched)
                                    }
                                    Err(err) => match classify_fetch_error(&err) {
                                        FetchFailure::NoAccess => refused.push(*index),
                                        FetchFailure::WrongOwner => wrong_owner.push(*index),
                                        FetchFailure::Other => errors.push((*index, err.to_string())),
                                    },
                                }
                            }
                        }
                        FetchFailure::Other => return Err(anyhow!("fetch keys: {err}")),
                    },
                }
            }
        }
        let keys = self.keys.lock().unwrap_or_else(|err| err.into_inner());
        let mut out = Vec::new();
        let mut counts = json!({"ok": 0, "no_access": 0, "wrong_owner": 0, "no_cached_key": 0, "error": 0});
        for ((entry, blob), set) in state.entries.iter().zip(&blobs).zip(&sets) {
            let mut row = json!({
                "index": entry.index,
                "epoch": entry.epoch,
                "author": entry.author,
                "blob_id": entry.blob_id,
                "key_id": hex::encode(&entry.key_id),
            });
            let status = match set {
                Err(message) => {
                    row["error"] = json!(message);
                    "error"
                }
                Ok(set) => {
                    row["seal"] = set.to_json();
                    if refused.contains(&entry.index) {
                        "no_access"
                    } else if wrong_owner.contains(&entry.index) {
                        row["error"] = json!(
                            "the key request used an object owned by another address; check the space and allowlist ids"
                        );
                        "wrong_owner"
                    } else if let Some((_, message)) = errors.iter().find(|(index, _)| *index == entry.index) {
                        row["error"] = json!(message);
                        "error"
                    } else if keys.servers_for(&self.share.seal_package, &entry.key_id) < set.threshold as usize {
                        "no_cached_key"
                    } else {
                        let client = &clients[set];
                        match client
                            .decrypt(blob, &keys)
                            .map_err(|err| anyhow!("{err}"))
                            .and_then(|plain| parse_entry_plaintext(&plain))
                        {
                            Ok(value) => {
                                row["text"] = value["text"].clone();
                                row["kind"] = value["kind"].clone();
                                row["ts"] = value["ts"].clone();
                                if value["author"].as_str().map(engine::norm_address).transpose()?.as_deref()
                                    != Some(entry.author.as_str())
                                {
                                    row["error"] = json!("author in plaintext does not match the publisher");
                                    "error"
                                } else {
                                    "ok"
                                }
                            }
                            Err(err) => {
                                row["error"] = json!(err.to_string());
                                "error"
                            }
                        }
                    }
                }
            };
            row["status"] = json!(status);
            counts[status] = json!(counts[status].as_u64().unwrap_or(0) + 1);
            out.push(row);
        }
        Ok(json!({
            "space_id": space,
            "list_id": state.list_id,
            "reader": self.address(),
            "fetched": fetch,
            "entries": out,
            "counts": counts,
        }))
    }

    pub fn status(&self, space: &str) -> Result<Value> {
        let state = self.space(space)?;
        let epoch = self.list_epoch(&Address::from_str(&state.list_id)?)?;
        let mut per_epoch = std::collections::BTreeMap::new();
        for entry in &state.entries {
            *per_epoch.entry(entry.epoch).or_insert(0u64) += 1;
        }
        Ok(json!({
            "space_id": space,
            "list_id": state.list_id,
            "creator": state.creator,
            "epoch": epoch,
            "entries": state.entries.len(),
            "entries_per_epoch": per_epoch,
            "address": self.address(),
            "seal": self.share.seal_json(),
        }))
    }
}

fn ident(name: &'static str) -> Identifier {
    Identifier::from_static(name)
}

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as i64)
        .unwrap_or(0)
}

/// Like `engine::execute_tx_effects`, but fails when the transaction aborted.
async fn execute_checked(
    rpc: String,
    signer: Ed25519PrivateKey,
    tx: TransactionBuilder,
) -> Result<(String, Vec<(String, String)>)> {
    let mut client = sui_rpc::Client::new(&rpc).map_err(|err| anyhow!("sui rpc: {err}"))?;
    engine::check_rpc(&rpc).await?;
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
    let effects = reply.transaction.and_then(|done| done.effects).ok_or_else(|| anyhow!("{digest}: no effects"))?;
    let status = effects.status.clone().unwrap_or_default();
    if status.success != Some(true) {
        let error = status.error.map(|err| format!("{err:?}")).unwrap_or_else(|| "unknown".into());
        return Err(anyhow!("transaction {digest} failed: {error}"));
    }
    let created = effects
        .changed_objects
        .into_iter()
        .filter(|change| change.id_operation == Some(2))
        .map(|change| (change.object_id.unwrap_or_default(), change.object_type.unwrap_or_default()))
        .collect();
    Ok((digest, created))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn list() -> Address {
        Address::from_str("0x5c1b").unwrap()
    }

    #[test]
    fn key_id_layout_matches_allowlist() {
        let id = key_id(&list(), 3, &[9u8; NONCE_LEN]);
        assert_eq!(id.len(), 32 + 8 + NONCE_LEN);
        assert_eq!(&id[..32], list().as_bytes());
        assert_eq!(&id[32..40], &[3, 0, 0, 0, 0, 0, 0, 0]);
        assert_eq!(parse_key_id(&id).unwrap(), (list(), 3));
        assert!(parse_key_id(&id[..40]).is_err(), "an id without nonce must be refused");
    }

    #[test]
    fn share_config_defaults_and_validation() {
        let cfg = ShareConfig::from_json("{}").unwrap();
        assert_eq!(cfg.seal_package.to_string(), DEFAULT_SEAL_PACKAGE);
        assert_eq!(cfg.space_package.to_string(), DEFAULT_SPACE_PACKAGE);
        assert_eq!(cfg.key_servers.len(), 2);
        assert_eq!(cfg.threshold, 2);
        assert!(ShareConfig::from_json(r#"{"threshold": 3}"#).is_err());
        assert!(ShareConfig::from_json(r#"{"threshold": 0}"#).is_err());
        assert!(ShareConfig::from_json(r#"{"seal_package": "0xzz"}"#).is_err());
    }

    fn addr(raw: &str) -> Address {
        Address::from_str(raw).unwrap()
    }

    #[test]
    fn share_config_profiles_and_custom_servers() {
        // Default: the first preview's 2-of-2 on the Mysten open testnet servers.
        let cfg = ShareConfig::from_json("{}").unwrap();
        assert_eq!(cfg.profile, PROFILE_TESTNET);
        assert_eq!(cfg.key_servers, vec![addr(MYSTEN_TESTNET_1), addr(MYSTEN_TESTNET_2)]);
        assert_eq!(cfg.threshold, 2);
        assert_eq!(cfg.aggregators.get(&addr(MYSTEN_TESTNET_COMMITTEE)).unwrap(), MYSTEN_TESTNET_AGGREGATOR);
        // Committee: one server (3-of-5 inside), threshold 1.
        let cfg = ShareConfig::from_json(r#"{"key_server_profile": "committee"}"#).unwrap();
        assert_eq!(cfg.key_servers, vec![addr(MYSTEN_TESTNET_COMMITTEE)]);
        assert_eq!(cfg.threshold, 1);
        assert_eq!(cfg.seal_json()["key_server_profile"], "committee");
        // Custom threshold on the default servers.
        let cfg = ShareConfig::from_json(r#"{"threshold": 1}"#).unwrap();
        assert_eq!((cfg.key_servers.len(), cfg.threshold), (2, 1));
        // Explicit list, hybrid committee + open server.
        let raw = json!({"key_servers": [MYSTEN_TESTNET_COMMITTEE, MYSTEN_TESTNET_1], "threshold": 2}).to_string();
        let cfg = ShareConfig::from_json(&raw).unwrap();
        assert_eq!(cfg.key_servers, vec![addr(MYSTEN_TESTNET_COMMITTEE), addr(MYSTEN_TESTNET_1)]);
        // Null values keep the defaults (backward compatible share.json).
        let cfg = ShareConfig::from_json(r#"{"key_servers": null, "threshold": null}"#).unwrap();
        assert_eq!((cfg.key_servers.len(), cfg.threshold), (2, 2));
    }

    #[test]
    fn share_config_refuses_bad_settings() {
        for bad in [
            r#"{"key_server_profile": "mainnet"}"#,
            r#"{"key_server_profile": "committee", "threshold": 2}"#,
            r#"{"key_servers": []}"#,
            r#"{"key_servers": "0x1"}"#,
            r#"{"key_servers": ["0x1", "0x1"]}"#,
            r#"{"key_servers": ["0xzz"]}"#,
            r#"{"threshold": "2"}"#,
            r#"{"threshold": -1}"#,
            r#"{"threshold": 256}"#,
            r#"{"aggregators": {"0x1": "http://plain.example"}}"#,
            r#"{"aggregators": {"0x1": 3}}"#,
            r#"{"aggregators": ["https://a.example"]}"#,
            r#"{"aggregators": {"nope": "https://a.example"}}"#,
        ] {
            assert!(ShareConfig::from_json(bad).is_err(), "{bad} must be refused");
        }
        let cfg = ShareConfig::from_json(r#"{"aggregators": {"0x1": "https://agg.example/"}}"#).unwrap();
        assert_eq!(cfg.aggregators.get(&addr("0x1")).unwrap(), "https://agg.example");
        assert!(cfg.trusted_servers().contains(&addr("0x1")));
    }

    #[test]
    fn header_server_sets_are_checked() {
        let cfg = ShareConfig::from_json("{}").unwrap();
        let trusted = cfg.trusted_servers();
        let (one, two, committee) = (addr(MYSTEN_TESTNET_1), addr(MYSTEN_TESTNET_2), addr(MYSTEN_TESTNET_COMMITTEE));
        // Entries of the first preview: 2-of-2 on the open servers.
        let set = ServerSet::from_header(&[one, two], 2, &trusted).unwrap();
        assert_eq!(set.to_json()["threshold"], 2);
        // A committee entry is readable with the default config.
        assert!(ServerSet::from_header(&[committee], 1, &trusted).is_ok());
        assert!(ServerSet::from_header(&[one, two], 1, &trusted).is_ok());
        let unknown = ServerSet::from_header(&[one, addr("0xbad")], 1, &trusted).unwrap_err();
        assert!(unknown.to_string().contains("untrusted key server"), "{unknown}");
        let weighted = ServerSet::from_header(&[one, one, two], 2, &trusted).unwrap_err();
        assert!(weighted.to_string().contains("weighted"), "{weighted}");
        assert!(ServerSet::from_header(&[one, two], 3, &trusted).is_err());
        assert!(ServerSet::from_header(&[one, two], 0, &trusted).is_err());
    }

    #[test]
    fn fetch_errors_are_classified() {
        use sui_seal_client::{Error, Failures, KeyServerErrorKind};
        let refused = |kind: KeyServerErrorKind, message: &str| Error::KeyServer {
            server: "s".into(),
            kind,
            message: message.into(),
        };
        let owner =
            || refused(KeyServerErrorKind::InvalidParameter, "Transaction was not signed by the correct sender");
        assert_eq!(classify_fetch_error(&owner()), FetchFailure::WrongOwner);
        assert_eq!(classify_fetch_error(&refused(KeyServerErrorKind::NoAccess, "denied")), FetchFailure::NoAccess);
        assert_eq!(classify_fetch_error(&refused(KeyServerErrorKind::Failure, "boom")), FetchFailure::Other);
        assert_eq!(classify_fetch_error(&Error::Transport("down".into())), FetchFailure::Other);
        let all_owner = Error::ThresholdNotReached { got: 0, threshold: 2, failures: Failures(vec![owner(), owner()]) };
        assert_eq!(classify_fetch_error(&all_owner), FetchFailure::WrongOwner);
        let mixed = Error::ThresholdNotReached {
            got: 0,
            threshold: 2,
            failures: Failures(vec![owner(), refused(KeyServerErrorKind::NoAccess, "denied")]),
        };
        assert_eq!(classify_fetch_error(&mixed), FetchFailure::NoAccess);
    }

    #[test]
    fn space_json_parses_and_checks_ids() {
        let id = key_id(&list(), 1, &[7u8; NONCE_LEN]);
        let good = json!({
            "list_id": list().to_string(),
            "creator": "0xa",
            "entries": [{
                "blob_id": "blob",
                "key_id": base64::Engine::encode(&base64::engine::general_purpose::STANDARD, &id),
                "epoch": "1",
                "author": "0xa",
                "blob_sha256": hex::encode([1u8; 32]),
            }],
        });
        let state = parse_space(&good).unwrap();
        assert_eq!(state.entries.len(), 1);
        assert_eq!(state.entries[0].epoch, 1);
        assert_eq!(state.entries[0].key_id, id);
        let mut wrong_epoch = good.clone();
        wrong_epoch["entries"][0]["epoch"] = json!("0");
        assert!(parse_space(&wrong_epoch).is_err());
        let mut short_digest = good.clone();
        short_digest["entries"][0]["blob_sha256"] = json!("0102");
        assert!(parse_space(&short_digest).is_err());
        let mut other_list = good;
        other_list["list_id"] = json!("0x5c1c");
        assert!(parse_space(&other_list).is_err());
    }

    #[test]
    fn entry_plaintext_roundtrip() {
        let author = Address::from_str("0xa").unwrap();
        let bytes = entry_plaintext("the lighthouse key", "fact", &author, 42);
        let value = parse_entry_plaintext(&bytes).unwrap();
        assert_eq!(value["text"], "the lighthouse key");
        assert_eq!(value["ts"], 42);
        assert!(parse_entry_plaintext(br#"{"format":"other","text":"x"}"#).is_err());
        assert!(parse_entry_plaintext(b"not json").is_err());
    }
}
