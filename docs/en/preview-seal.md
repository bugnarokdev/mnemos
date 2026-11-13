# Shared spaces with Seal: BETA (testnet preview)

> **BETA (testnet preview).** Not production software. This lane lives on the `preview/seal` branch only. It runs on Sui
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
   [`sui-seal-client`](https://github.com/bugnarokdev/sui-seal-client) v0.2.0 (BETA), stored on Walrus,
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
| Seal key servers (default) | `mysten-testnet-1`, `mysten-testnet-2`, threshold 2 of 2 |
| Seal key servers (`--committee`, BETA) | Mysten testnet committee `0xb012378c9f3799fb5b1a7083da74a4069e3c3f1c93de0b27212a5799ce1e1e98`, 3-of-5 behind `https://seal-aggregator-testnet.mystenlabs.com` |

The native core pins `sui-seal-client` by commit to the `v0.2.0` tag
(`da579f64223ff3f7a83897272937cfc4d2d55153`). `move/SharedSpace` still depends on the
allowlist at `f4ffa2f`, the commit the Space package was built and published from; the
allowlist Move sources are byte-for-byte identical in `v0.2.0`. The published package
links to `0xe10f…7f1b` version 1.

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

## Key servers (BETA)

Each space picks the key servers its entries are encrypted to. The choice is made at
`create` (or `join`) and stored with the space in `<HERMES_HOME>/mnemos/share.json`
(`spaces.<name>.seal`); `push` uses it without any flag.

| Option | Key servers | Threshold |
| --- | --- | --- |
| none (default) | `mysten-testnet-1`, `mysten-testnet-2` | 2 of 2, as in the first preview |
| `--committee` | Mysten testnet committee, reached through the Mysten aggregator | 1 (the committee itself is 3-of-5) |
| `--threshold N` | the default servers | N |
| `--key-server ID [--key-server ID…] [--threshold N]` | the listed key server objects | N (default 2) |

Readers need no option: `pull` reads the key servers and the threshold from each entry's
encrypted header, then asks those servers. It only contacts servers it trusts: the
Mysten testnet servers and committee, plus the servers listed in the profile's settings.
An entry naming any other server, or a weighted server set, is reported as `error`
without any request.

Spaces created before this change have no `seal` setting and keep the default 2-of-2;
their entries still decrypt (the coop end-to-end run re-reads a space written by the
previous client version).

Profile-wide settings go in `share.json` under `config`: `key_server_profile`
(`testnet` or `committee`), `key_servers`, `threshold`, and `aggregators`
(`{"<committee key server id>": "https://…"}`) for committees other than Mysten's.
Settings are validated before any transaction; an impossible threshold is refused with
`share settings: …`.

**Wrong owner.** If key servers answer that the approval used an object owned by another
address (`Error::is_wrong_owner` in sui-seal-client), `pull` marks the entries
`wrong_owner`, does not retry them, and exits with code 4. Allowlist spaces only use the
shared allowlist object, so this should not happen; it would point at a wrong space or
allowlist id.

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
| `share create [--name N] [--committee \| --key-server ID… ] [--threshold N]` | creator | Allowlist + Space, creator is member and holds the Cap; key servers as above |
| `share join --space <id> [--name N] [key server options]` | member | Registers someone else's space locally (options only matter if you push) |
| `share add <address>` | creator | Adds a member (epoch unchanged) |
| `share remove <address>` | creator | Removes a member, epoch + 1 |
| `share push --text T [--text T…]` | member | Encrypts, uploads, publishes |
| `share push --from-memory QUERY [--limit N]` | member | Shares personal entries matching QUERY (read only) |
| `share pull` | member | Fetches keys, decrypts, caches; exit code 3 if any entry is refused, 4 on `wrong_owner` |
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
- Mysten's open testnet key servers and committee are free, rate-limited and without
  availability guarantee. There is no automatic failover between server sets: a space
  keeps the servers chosen at creation; a lower threshold (for example 1 of 2) tolerates
  one server down at the cost of trusting a single server.
- Weighted key servers and API keys (permissioned servers), available in
  sui-seal-client v0.2.0, are not exposed here.
- The `seal-sdk` and `crypto` crates are git dependencies pinned by commit, so this
  feature cannot be published to crates.io yet.

## Roadmap (indicative)

This roadmap is indicative only: no dates, no commitments, and the order may change.

1. **v0.1 beta:** allowlist-based cooperative sharing on testnet.
2. **Key servers (this branch, BETA, on sui-seal-client v0.2.0):** Mysten testnet
   committee through the aggregator, configurable servers and threshold, header-driven
   reads. Next: failover between server sets, permissioned servers.
3. **More access policies:** subscription and time-limited access exist in
   sui-seal-client v0.2.0; wiring them into spaces is next.
4. **Python backend sharing** through the sui-seal-client v0.2.0 Python bindings.
5. **crates.io publication** once Mysten publishes `seal-sdk` and `crypto`.
6. **Security review** of the lane and of the Move packages.
7. **Then mainnet**, only after the review.

## Tests

- `cargo test --release --features seal`: the 8 existing tests and 8 for this lane
  (key id layout, config validation, key server profiles, refused settings, header
  server sets, fetch error classification including wrong owner, Space parsing, entry
  format).
- `move/SharedSpace`: 7 Move tests (`sui move test`).
- `tests/test_share.py`: 13 offline tests (refusal on the Python backend, config
  isolation, cache, CLI wiring, key server flags and per-space settings, settings
  refused before any transaction, signer derivation, key file never printed).
- `scripts/e2e_coop.sh`: the testnet scenario above, a committee-backed space, a 1-of-2
  space, a re-read of an earlier run's space, with A's personal MemoryIndex created,
  restored and verified around it. See the
  [preview test report](test-report-preview-seal.md).
