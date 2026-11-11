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
   SQLite hot      Walrus PUT/GET    Sui gRPC write
   store           ciphertext        GraphQL read
   $HERMES_HOME/   blob id           MemoryIndex
   mnemos/
```

## Pieces

- `plugin.yaml`, `__init__.py`, `cli.py`, `config_schema.py` are the Hermes surfaces.
- `mnemos/provider.py` implements `MemoryProvider`. Every `register()` call constructs a new instance. Paths come from the `hermes_home` argument, not from a hardcoded home.
- `mnemos/engine.py` uses the native module when `import mnemos_core` succeeds.
- `mnemos/py_engine.py` is the fallback: same snapshot bytes, Walrus HTTP, and Sui gRPC simulate-then-execute. Checkpoints commit in this mode.
- `mnemos/history.py` reads the snapshot list from the MemoryIndex and verifies each version (blob sha256, previous-hash link, Ed25519 signature) for `hermes mnemos history --verify`.
- `native/` is the PyO3 crate. It owns SQLite, XChaCha20-Poly1305, Ed25519, Walrus HTTP, the faucet proof-of-work, and Sui transactions (`sui-sdk-types`, `sui-crypto`, `sui-transaction-builder`, `sui-rpc`).
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
