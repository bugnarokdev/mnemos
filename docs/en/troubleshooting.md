# Troubleshooting

## Doctor says `backend: python`

That is a working mode. `reason` says why the native module was not used (native download is opt-in and off by default, no matching wheel, or `MNEMOS_BACKEND=python`). Chain writes still go to Sui and Walrus. To prefer Rust, set `MNEMOS_NATIVE_AUTO=1` (official release only, pinned sha256) or run `scripts/build_core.sh` with `MNEMOS_PYTHON` set to the interpreter Hermes uses.

## `hermes` cannot import its own dependencies under a new `HERMES_HOME`

Dependency state lives in the Hermes home that installed the checkout (`<home>/installs/...`). A brand-new directory does not see it. Use a profile under that home (`<home>/profiles/<name>`), which is what `scripts/demo.sh` does.

## Faucet failed

The error includes the Sui address. Fund it from <https://faucet.sui.io> if the proof-of-work or the rate limit blocked `init`. Keep the same secret so the derived address does not change. Then rerun `init`.

## `MoveAbort` code 7 on `append`

Code 7 is `E_VERSION`: the client built a snapshot against a stale head. Flush retries the GraphQL read until it is at least the version recorded locally. If this still appears, wait a few seconds and run `hermes mnemos flush` again. The outbox entry stays local until an append succeeds.

## `MoveAbort` code 6

The index already has 32 snapshots. This release does not rotate the object. Create a new one with a new package call only if you are starting a new history. Restoring the full old index still works until the testnet is wiped.

## Prefetch is empty

The fact is encrypted at rest off-box, but recall reads the local cache. After restore, the cache has to contain the fact. Run `hermes mnemos restore` before expecting prefetch to hit. Queries ignore tokens shorter than 4 characters, so a query of `ok` recalls nothing on purpose.

## `hermes plugins validate` warns about binaries

`native/target/` contains build artifacts. They are gitignored and must not be committed. Validate a clean tree. A warning that names `libmnemos_core.so` means a build directory is still inside the plugin path.

## Doctor says the index is missing

`index_object_id` is empty, or GraphQL cannot see the object yet. `init` waits for the object after the transaction is in a checkpoint. If you pasted an id, check the network is `testnet` and the RPC and GraphQL URLs match that network.

## `refusing to sign: ... Mnemos only writes to Sui testnet`

The `sui_rpc` in `mnemos/config.json` did not report the Sui testnet chain id, or could not be reached in time. Nothing was signed or uploaded. Point `sui_rpc` back to `https://fullnode.testnet.sui.io:443` (or another testnet fullnode) and rerun. The entries stay in the local outbox until a flush succeeds.

## `MemoryIndex uses the pre-0.2.2 key derivation and is read-only`

The index was created before Argon2id. Restore and `history --verify` still work; writes do not. Run `hermes mnemos init` to create a new index. See [restore](restore.md#indexes-created-before-022).

## Checkpoint raised and compaction stopped

That is the API v2 behavior when `compression.checkpoint_required` is true. Fix Walrus or Sui, then retry. The transcript is still uncompressed. With the setting off, Hermes logs the error and compacts anyway.
