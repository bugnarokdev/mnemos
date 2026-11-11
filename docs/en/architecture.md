# Architecture

```text
+---------------- Hermes process ----------------+
|  MemoryProvider (mnemos/provider.py)           |
|    prefetch / queue_prefetch  --> SQLite only  |
|    sync_turn / on_memory_write --> outbox      |
|    on_pre_compress (API v2) --> flush, or raise|
|    tools: remember, recall, status, flush      |
+------------------------+-----------------------+
                         |
                         v
              +--------------------+
              | mnemos_core (Rust) |  preferred
              | or Python fallback |
              +---------+----------+
                        |
        +---------------+----------------+
        |               |                |
        v               v                v
   SQLite hot      Walrus PUT/GET    Sui gRPC
   store           ciphertext        (GraphQL fallback)
   $HERMES_HOME/   blob id           MemoryIndex
   mnemos/
```

## Pieces

- `plugin.yaml`, `__init__.py`, `cli.py`, `config_schema.py` are the Hermes surfaces.
- `mnemos/provider.py` implements `MemoryProvider`. Every `register()` call constructs a new instance. Paths come from the `hermes_home` argument, not from a hardcoded home.
- `mnemos/engine.py` uses the native module when `import mnemos_core` succeeds and the module reports `core_version()` 0.2.3 or newer (Argon2id keys and fullnode reads); an older core is ignored.
- `mnemos/py_engine.py` is the fallback: same snapshot bytes, Walrus HTTP, and Sui gRPC simulate-then-execute. Checkpoints commit in this mode.
- `mnemos/chain.py` holds the snapshot bytes and the key derivation (Argon2id, then HKDF per purpose), plus the read-only legacy derivation. `mnemos/sui_client.py` checks the RPC chain id before every signature. `mnemos/reads.py` reads chain state from the fullnode, with a guarded GraphQL fallback (see below).
- `mnemos/history.py` reads the snapshot list from the MemoryIndex and verifies each version (blob sha256, previous-hash link, Ed25519 signature) for `hermes mnemos history --verify`.
- `native/` is the PyO3 crate. It owns SQLite, XChaCha20-Poly1305, Ed25519, Walrus HTTP, the faucet proof-of-work, and Sui transactions (`sui-sdk-types`, `sui-crypto`, `sui-transaction-builder`, `sui-rpc`), with the same Argon2id derivation (`argon2` crate) and the same testnet chain check.
- `move/MemoryIndex` is the on-chain index.

## Local schema

`$HERMES_HOME/mnemos/mnemos.sqlite` holds `entries` (plaintext, this machine only), `snapshots` (ciphertext, blob id, hash, signature, sync bit), and `meta`.

## Turn path

`prefetch` returns a string already computed for that query, or computes it from SQLite if the cache misses. Either way it stays on this machine. `queue_prefetch` fills the cache on a Hermes context thread after the turn.

`sync_turn` inserts a bounded copy of the turn into `entries` and starts a background flush. The turn itself does not wait for Walrus or Sui. `on_session_end` waits at most 20 seconds. The Python backend and the Rust extension both commit that flush.

`on_pre_compress` is synchronous. It returns `checkpoint: <blob id>` only after the blob is stored and the index append has been accepted. A failure raises. Hermes, when `compression.checkpoint_required` is true, keeps the uncompressed transcript.

Subagent and cron contexts skip automatic writes. They still checkpoint in `on_pre_compress`, because that is the fail-closed contract.

## One snapshot per flush

Pending entries are canonicalized to JSON, sealed, uploaded, hashed, signed, and appended as one snapshot. A retry reuses the same ciphertext and blob id when the previous attempt uploaded but did not finish the chain update.

## Reads and availability

### Why

Every Mnemos operation that touches Sui reads chain state first: `flush` reads the index head and the gas balance, `restore` and `history` read the MemoryIndex, `init` reads the new objects, and a restored profile recovers `package_id` from the index type. Up to 0.2.2 all of those reads went through the Sui GraphQL indexer, while writes went to the fullnode. That made the indexer a second single point of failure, and a quiet one: an indexer that stops advancing keeps answering, but with old data.

This happened on 11 November 2026. From 22:24 (Paris time) the public testnet GraphQL stayed at checkpoint 405395971 while the fullnode kept producing checkpoints; two hours later it was more than 33,000 checkpoints behind, and owner and balance queries failed with `Request is outside consistent range`. The fullnode was fine the whole time, but every Mnemos read failed, so nothing could be written or restored.

### How

Reads now go to the same gRPC fullnode that executes transactions (`sui_rpc`): `LedgerService.GetObject` for the MemoryIndex and package recovery, `StateService.ListOwnedObjects` for owned objects, and `StateService.GetBalance` for gas. The fullnode is the source the indexer copies from, so it cannot be behind it, and a write already depends on it. Before the first read, the fullnode must report the Sui testnet chain id, the same check that guards signing (cached for five minutes per URL).

GraphQL is now only a fallback, and only when it is provably fresh:

1. Read from the fullnode. A found object is used as is. An object the fullnode reports as not found is final; GraphQL is never asked to contradict it. A chain-id failure is final too.
2. If the fullnode read fails (unreachable, timeout, other error) and `read_source` is `auto`, Mnemos checks GraphQL before trusting it: the fullnode checkpoint height and GraphQL's latest checkpoint are read, GraphQL must report the Sui testnet chain id, and the gap must be at most `graphql_max_lag` checkpoints (default 100, about a minute on testnet).
3. If any of that fails, the read is refused with both reasons, for example `GraphQL is 33313 checkpoints behind the fullnode (limit 100); refusing a stale read`. Mnemos never acts on data it cannot date.

Whatever the source, the result is checked before use: the returned object id must match the one requested, the type must be `<package>::memory_index::MemoryIndex`, and when writing, the package must match `package_id`. `init` takes the package and the new MemoryIndex id from the transaction effects instead of searching the owner's objects, so an address with several indexes cannot pick the wrong one.

Both backends implement the same order and the same checks. `hermes mnemos doctor` reports the index check with its source (`readable via grpc`) and a separate `graphql_fresh` check with the current lag. Walrus blobs are still read from the aggregator, and their sha256 is checked against the signed index.

Settings: `read_source` (`auto` by default; `grpc` disables the GraphQL fallback) and `graphql_max_lag` (default `100`). See [configuration](configuration.md#file-settings).
