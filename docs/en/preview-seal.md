# Shared spaces with Seal (Beta preview, testnet)

> **Beta preview.** This lane lives on the `preview/seal` branch only. It runs on Sui
> testnet, Walrus testnet and the Mysten Seal testnet key servers. It is not part of a
> release, the formats may change, and testnet can be wiped. Do not share anything you
> cannot afford to lose or to see leaked.

Personal memory stays exactly as it is: `MNEMOS_SECRET`, Argon2id, XChaCha20-Poly1305
blobs, your owned `MemoryIndex`, Walrus. The sharing lane is a separate path, compiled
only with the `seal` cargo feature. It never writes the MemoryIndex, the local store or
`config.json`.

## What it does

A **shared space** lets several Hermes profiles read the same memory entries without a
shared password:

1. A creates a space. One transaction creates a Seal **allowlist** (A holds its `Cap`
   and is its first member) and a **Space** object bound to it.
2. A adds B's Sui address to the allowlist.
3. A (or any member) pushes entries. Each entry is encrypted with
   [Seal](https://github.com/MystenLabs/seal) through
   [`sui-seal-client`](https://github.com/bugnarokdev/sui-seal-client), stored on Walrus,
   and referenced in the Space (blob id, key id, epoch, author, blob sha256).
4. B pulls: the Seal key servers check `allowlist::seal_approve` on chain and release
   the keys only to members. C, who is not a member, is refused.
5. A removes B. The allowlist epoch goes up by one, and every new entry is encrypted to
   the new epoch.

## On-chain pieces

| Piece | Testnet id |
| --- | --- |
| Allowlist package `seal_allowlist::allowlist` (from sui-seal-client, owner `0xa0ea…8c06`) | `0xe10fbae08333748f310adc926904980c6ced589a75a774bff4dfc462567d7f1b` |
| Space package `mnemos_share::space` (`move/SharedSpace`) | `0xa40e74c6ae866f18e86e9dcdfc9d29af436597264991fc81b4a289519f3500d1` |
| Space package UpgradeCap (held by the owner address `0xa0ea…8c06`) | `0x27a9d878f827ca3693cb5fdc963ce7d87897ffc742de8b0183f8a6040f6f1b96` |
| Space package publish transaction | `Bwx3yVdYci6XQXMXecpt3eYHCyvZr46maSQuyZfSbddM` |
| Seal key servers | `mysten-testnet-1`, `mysten-testnet-2`, threshold 2 of 2 |

`move/SharedSpace` depends on the allowlist by git commit
(`sui-seal-client` `f4ffa2f`), and the published package links to
`0xe10f…7f1b` version 1.

The Space only accepts a reference when:

- the sender is a **current member** of the allowlist (`ENotMember` otherwise);
- the key id starts with the allowlist id (`EBadKeyId`) and carries the **current
  epoch** (`EStaleEpoch`): a stale or future epoch is refused;
- the sha256 is 32 bytes (`EBadDigest`).

Key id layout: `[allowlist id: 32 bytes][epoch: u64 little-endian][nonce: 16 random bytes]`.

## Revocation: what removal does and does not do

Seal keys cannot be taken back. A key for a key id opens everything encrypted to that id,
forever. So:

- **After removal, B cannot fetch any new key**, for old or new entries.
- **New entries are out of B's reach**: they use the new epoch, and B never had a key
  for it. The Space refuses to publish an entry under an old epoch.
- **What B already read, B keeps.** The CLI caches what a member decrypted in
  `<HERMES_HOME>/mnemos/shared/<space>.json`, and any keys B fetched before the removal
  still decrypt those old entries. The coop end-to-end test asserts this explicitly.
- Remaining members still read old and new entries.

Removal protects the future, not the past. If an entry must stay secret from a removed
member, it should not have been shared with them.

## Commands

Build the native core with the feature:

```sh
MNEMOS_FEATURES=seal scripts/build_core.sh
```

The commands are under `hermes mnemos-sui share`. The same code runs outside Hermes with
`python -m mnemos.share --home <HERMES_HOME> …`. Every command prints JSON.

| Command | Who | Effect |
| --- | --- | --- |
| `share whoami` | anyone | Prints the signer address |
| `share create [--name N]` | creator | Allowlist + Space, creator is member and holds the Cap |
| `share join --space <id> [--name N]` | member | Registers someone else's space locally |
| `share add <address>` | creator | Adds a member (epoch unchanged) |
| `share remove <address>` | creator | Removes a member, epoch + 1 |
| `share push --text T [--text T…]` | member | Encrypts, uploads, publishes |
| `share push --from-memory QUERY [--limit N]` | member | Shares personal entries matching QUERY (read only) |
| `share pull` | member | Fetches keys, decrypts, caches; exit code 3 if any entry is refused |
| `share status` | anyone | Epoch, number of entries per epoch |

**Signer.** `MNEMOS_SHARE_KEY_FILE` (or `--key-file`) points to a Sui key file
(suiprivkey, keystore base64 or 32-byte hex). The file is read, passed to the native
core and never printed. Without it the signer is the profile's own Mnemos Sui address
(derived from `MNEMOS_SECRET`). Session keys and fetched keys stay in memory only.

**Gas.** `create`, `add`, `remove` and `push` are Sui transactions paid by the signer.
`pull` and `status` cost nothing.

**Safety.** Every write first checks that the RPC is Sui testnet, like the personal
path. Transactions that abort on chain are reported as errors.

## Limits of this preview

- **Rust only.** The Python backend has no Seal implementation; with
  `MNEMOS_BACKEND=python` or a core built without `seal`, `share` refuses with a clear
  message. Personal memory works with both backends, as before.
- Entries are plain text items, not full snapshots, and are not merged into personal
  memory: they live in the space and in the per-space cache.
- The Space keeps references in a vector inside one object (Sui object size limit,
  roughly a few thousand entries).
- Only the creator holds the Cap; there is no admin transfer command.
- Mysten's open testnet key servers are free, rate-limited and without availability
  guarantee. Committee (aggregator) servers are not supported yet.
- The `seal-sdk` and `crypto` crates are git dependencies pinned by commit, so this
  feature cannot be published to crates.io yet.

## Tests

- `cargo test --release --features seal`: the 8 existing tests and 4 for this lane
  (key id layout, config validation, Space parsing, entry format).
- `move/SharedSpace`: 7 Move tests (`sui move test`).
- `tests/test_share.py`: 9 offline tests (refusal on the Python backend, config
  isolation, cache, CLI wiring, signer derivation, key file never printed).
- `scripts/e2e_coop.sh`: the testnet scenario above, with A's personal MemoryIndex
  created, restored and verified around it. See the
  [preview test report](test-report-preview-seal.md).
