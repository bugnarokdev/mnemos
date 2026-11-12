# Test report

A record of the tests run before tagging `mnemos-native-v0.2.3`. Every chain write below is public: open the links to check the transaction, the MemoryIndex object, and the ciphertext blob. Blobs are encrypted; nothing here can decrypt them. Testnet can be wiped, so links may expire.

| | |
| --- | --- |
| When | 12 November 2026, 00:20 to 00:40 Paris time (UTC+1) |
| Tested commit | [`82fba1327b29`](https://github.com/bugnarokdev/mnemos/commit/82fba1327b29ed6f1441bccea4e97c070818359c) (tag `mnemos-native-v0.2.3`) |
| Network | Sui testnet (chain id `69WiPg3DAQiwdxfncX6wYQ2siKwAe6L9BZthQea3JNMD`), Walrus testnet |
| Toolchain | Linux x86_64, rustc 1.99.1, cargo 1.99.1, Python 3.12.14, pytest 9.2.0, grpcio 1.85.1, protobuf 6.33.6, argon2-cffi 25.1.0, mnemos_core 0.2.3 (built from the tested commit) |

## Unit tests

| Suite | Result |
| --- | --- |
| `cargo test --release` (native core) | 8 passed: Argon2id and legacy key vectors, address normalization, protobuf-to-JSON mapping, read fallback rules (not-found and chain failures final, `grpc` mode, unverifiable freshness), defaults |
| `pytest` with the Rust core required (`MNEMOS_REQUIRE_RUST=1`) | 49 passed |
| `pytest` with `MNEMOS_BACKEND=python` | 48 passed, 1 skipped (the Rust-only hash comparison) |

The pytest suites are `test_chain` (6), `test_chain_guard` (7), `test_cross` (4), `test_history` (12), `test_kdf` (7) and `test_reads` (13). `test_reads` uses mocks for the fullnode and GraphQL: fullnode first, fresh fallback, a stale GraphQL refused (19,107 checkpoints behind), a configurable limit, GraphQL on the wrong chain, freshness that cannot be checked, `grpc`-only mode, not-found final, chain-guard failure not retried, wrong type, wrong package, mismatched object id, chain checked once.

## End-to-end on testnet

Script: two facts are written, each flushed as one snapshot (Walrus upload, then a signed `append` on Sui); the local store is deleted; `restore` rebuilds it from Sui and Walrus; `history --verify` checks every version (blob sha256, previous-hash link, Ed25519 signature) without the secret. Package: [`0xc1222936…1f4a`](https://testnet.suivision.xyz/package/0xc1222936acba3d4b12e87341b43c689d932c04a026805ed1b3758f32a37f1f4a).

### Rust core

MemoryIndex [`0x772bef4a…48bd`](https://testnet.suivision.xyz/object/0x772bef4afb843eec39dc880b3c0def75c7c363d3dc3942d8ac546931bf5d48bd), created in [`EsrNZ2H9n1…`](https://testnet.suivision.xyz/txblock/EsrNZ2H9n1LBDFNquubJRsjMTDxhGxockYcaShJyZgaQ).

| Version | Content hash | Walrus blob | Sui transaction |
| --- | --- | --- | --- |
| 1 | `fdb11b68cb3386a4` | [`k7xbplKaLg…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/k7xbplKaLg_6X9VHmiHx6IzMmY1HQ8q6IfxfytE3V-M) | [`45Zx4EPBN2…`](https://testnet.suivision.xyz/txblock/45Zx4EPBN2tgXwvzF9BuEyTNyKd7yn5tc2TzHS2yTbZX) |
| 2 | `577cd51205e7f5a1` | [`IWdN2tdMe_…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/IWdN2tdMe_iMto_WsNzvfu-s2ca3DIdgA38abPxpCXg) | [`hjYsfFs7BF…`](https://testnet.suivision.xyz/txblock/hjYsfFs7BFuBBLz2sFkwACWrECbHWSLa1dEX4DGdSvw) |

Result: local store wiped; restore returned 2 entries, 2 snapshots, `kdf: argon2id`; `history --verify`: `OK: 2 versions verified`. The index was read via the fullnode.

### Python backend

MemoryIndex [`0x5daf1aef…a0dc`](https://testnet.suivision.xyz/object/0x5daf1aefe8217a1505832792a627c3279d51e874996ebc68a46a2602eaeca0dc), created in [`3GMejNZzdS…`](https://testnet.suivision.xyz/txblock/3GMejNZzdS2Xzp2qwhNBJHy6wJjbZAm7XcQ2v2XYvTVN).

| Version | Content hash | Walrus blob | Sui transaction |
| --- | --- | --- | --- |
| 1 | `3699abdb7070d969` | [`z95OaPSMO6…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/z95OaPSMO6qNtCEuYPOvAS_an8cQIlI15eYO74aadlw) | [`DJxyqM4J9R…`](https://testnet.suivision.xyz/txblock/DJxyqM4J9RhGX6Cewn1yAwdriKjAKG59eSBLtcH9jjGh) |
| 2 | `22c4415235437e31` | [`YBc2QYHKJo…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/YBc2QYHKJoTBbZq2vRZNH3G5PMnKfP9ePjBvGxmAUao) | [`AUpNzs8b6S…`](https://testnet.suivision.xyz/txblock/AUpNzs8b6Sdc9BzEERnkgMwyCT3mGzsyQ9aXg1BJ1JEH) |

Result: index created by this run, ids taken from the transaction effects; local store wiped; restore returned 2 entries, 2 snapshots, `kdf: argon2id`; `history --verify`: `OK: 2 versions verified`, read via the fullnode.

### Cross-backend

Each backend restores and then extends the other backend's index, and the other backend verifies the result. Ciphertext, content hash and signature formats are the same, so the chain stays valid.

- Rust restores the Python index [`0x5daf1aef…a0dc`](https://testnet.suivision.xyz/object/0x5daf1aefe8217a1505832792a627c3279d51e874996ebc68a46a2602eaeca0dc) (2 entries) and appends version 3: [`tK-fUg1r2w…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/tK-fUg1r2w2jIAkId_vNmYZN6Q-hDDxcyyFudzLnfeQ), [`GwG6gTeXwk…`](https://testnet.suivision.xyz/txblock/GwG6gTeXwkDaee58LjNFdJyY7ZPxsKDSWwWWXUFpJ4gL).
- Python restores the Rust index [`0x772bef4a…48bd`](https://testnet.suivision.xyz/object/0x772bef4afb843eec39dc880b3c0def75c7c363d3dc3942d8ac546931bf5d48bd) (2 entries) and appends version 3: [`46DO0SCDR0…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/46DO0SCDR0uAz0VWBzYihPr9MgJmUzeNdfEATmtbsX0), [`GC8hRBVa6q…`](https://testnet.suivision.xyz/txblock/GC8hRBVa6qcyRbPxjfj6n6a5ZcRWzCHdqCpSfTmqYNkj).

After each cross write, the first backend restores 3 entries and `history --verify` reports `OK: 3 versions verified`.

## Chain guard

Each backend was pointed at a non-testnet or unreachable `sui_rpc` and asked to `init` and to `flush`. Every attempt was refused before anything was uploaded or signed.

| `sui_rpc` | Backend | `init` and `flush` |
| --- | --- | --- |
| mainnet `https://fullnode.mainnet.sui.io:443` | Python, Rust | `refusing to sign: ... is on chain mainnet (4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S), Mnemos only writes to Sui testnet` |
| devnet `https://fullnode.devnet.sui.io:443` | Python, Rust | `refusing to sign: ... is on chain unknown (9ovasb4imJNPrhAjLcXjanDMwcybDczZR8W5jnWxpVhK), Mnemos only writes to Sui testnet` |
| unreachable `https://127.0.0.1:9` | Python, Rust | `refusing to sign: could not read the chain id of https://127.0.0.1:9` (Python: `_InactiveRpcError`, Rust: `tcp connect error`) |

## GraphQL outage and the read fallback

During this session the public testnet GraphQL stopped advancing at checkpoint 405395971 (from 11 November, 22:24 Paris time) while the fullnode continued. Owner and balance queries on GraphQL returned `Request is outside consistent range`. This is why 0.2.3 reads from the fullnode; see [reads and availability](architecture.md#reads-and-availability).

Observed with the tested commit:

- All end-to-end reads above (index, owned objects, balance, package recovery) were served by the fullnode while GraphQL was stale; both backends' `doctor` reported the index `readable via grpc`.
- The freshness check refused GraphQL: `GraphQL is 34416 checkpoints behind the fullnode (limit 100); refusing a stale read` (00:39). `doctor` reports the same as a failed `graphql_fresh` check, with the note that reads use the fullnode.
- With `sui_rpc` unreachable, a MemoryIndex read was refused in both `auto` and `grpc` modes (`refusing to sign: could not read the chain id of https://127.0.0.1:9`). GraphQL was not used on its own.

## Release

The wheels of [`mnemos-native-v0.2.3`](https://github.com/bugnarokdev/mnemos/releases/tag/mnemos-native-v0.2.3) were built by the public GitHub Actions workflow from the tested commit. Each matches the release `SHA256SUMS` and the table pinned in `mnemos/native_wheel.py`, and `gh attestation verify --repo bugnarokdev/mnemos` confirms it was built from `refs/tags/mnemos-native-v0.2.3` at `82fba1327b29`.

| Wheel | sha256 |
| --- | --- |
| `macosx_10_12_x86_64` | `2b0ec16f5d02df7759fdb080cc8c826f4bfc88d0f5c7410daf6ee3375e0b26fe` |
| `macosx_11_0_arm64` | `ff28f82f1b010ec459e3720591ebfc8f9a85dffa84d75aa882ae0fb0f9cb9038` |
| `manylinux_2_34_aarch64` | `3bd1b5245781f31cb3391712bc54ee1ea19ad3032ce7a701391d16d85ab07c78` |
| `manylinux_2_34_x86_64` | `d7bd9ea2f9cd55bbde33822b4b39670bcfa40ad929232bae6d6455b24844ea01` |
| `win_amd64` | `97f1190fb50d76b13ed19dd2beae89c96b6607a549ebf37db921bd0ebd7207e7` |

## Reproduce

Run `pytest`, `cargo test --release` in `native/`, then `hermes mnemos-sui init`, a few `remember` calls, `hermes mnemos-sui flush`, `hermes mnemos-sui restore` on a fresh profile and `hermes mnemos-sui history --verify`. For the cross-backend part, run the same steps once with `MNEMOS_BACKEND=python` and once with the Rust core on the same secret.

## Catalog rename (`mnemos-sui`)

Recorded after the catalog id was renamed from `mnemos` to `mnemos-sui`, so it does not collide with another catalog entry of that name. Version `0.2.3`, the native wheels, the data directory `$HERMES_HOME/mnemos/`, and `provider.name()` (`mnemos`) are unchanged. What changed is the catalog id, the install path (`$HERMES_HOME/plugins/mnemos-sui`), the `memory.provider` value (`mnemos-sui`), and the CLI command (`hermes mnemos-sui`).

The native extension source is unchanged, so the attested v0.2.3 wheels match the pinned source exactly. Two rare native error messages still say `hermes mnemos init` and will be updated with the next native release (v0.2.4).

| | |
| --- | --- |
| When | 12 November 2026, 10:50 to 11:15 Paris time (UTC+1) |
| Network | Sui testnet, Walrus testnet (same as above) |
| Toolchain | Linux x86_64, rustc 1.99.1, cargo 1.99.1, Python 3.12.14, pytest 9.2.0 |

### Unit tests

| Suite | Result |
| --- | --- |
| `cargo test --release` (native core) | 8 passed |
| `pytest` with the Rust core required (`MNEMOS_REQUIRE_RUST=1`) | 49 passed |
| `pytest` with `MNEMOS_BACKEND=python` | 48 passed, 1 skipped (the Rust-only hash comparison) |

### Install and coexistence

In an isolated `HERMES_HOME`, this plugin was installed at `plugins/mnemos-sui` next to the other catalog entry at `plugins/mnemos`.

- `hermes plugins validate` passed on both trees.
- `memory.provider: mnemos-sui` loaded this plugin; `memory.provider: mnemos` loaded the other.
- `hermes mnemos-sui --help` and `hermes mnemos-sui status` ran (status reported that the secret was unset, as expected). `hermes mnemos` was not registered while this plugin was active.

### End-to-end on testnet

Same script as above, on a fresh MemoryIndex per backend so every assertion passed unmodified (two facts, wipe, restore, `history --verify`, then each backend extends the other's index). Package: [`0xc1222936…1f4a`](https://testnet.suivision.xyz/package/0xc1222936acba3d4b12e87341b43c689d932c04a026805ed1b3758f32a37f1f4a).

#### Rust core

MemoryIndex [`0x0a844d3a…26da`](https://testnet.suivision.xyz/object/0x0a844d3acf44c788b878a64a788179dcd677929816177bd3d83784711c9726da), created in [`5GZ3KFKsTt…`](https://testnet.suivision.xyz/txblock/5GZ3KFKsTtPuqhBdSWvGGZdnWdBvY56LzU5GVxgXtHRs).

| Version | Content hash | Walrus blob | Sui transaction |
| --- | --- | --- | --- |
| 1 | `c15823c3f75dccb3` | [`Jw_lT0cIYc…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/Jw_lT0cIYcYY_eGavqs_dL4jIK8fUK3-1d-j0aDwKW4) | [`AfDDTV9BYg…`](https://testnet.suivision.xyz/txblock/AfDDTV9BYg4dXV3SBTHz13pNutiXKpWsTuyQb2BbzwRV) |
| 2 | `65572e7c9669b1e4` | [`sMFTuS-fr8…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/sMFTuS-fr8IV28QFP5NOtxyq2NJFIHXLnr374Lo8Lms) | [`CeR1v25FEX…`](https://testnet.suivision.xyz/txblock/CeR1v25FEX44kM8zhDL4zx5oYVsAFtNouWAT7EcZs96J) |

Result: local store wiped; restore returned 2 entries, 2 snapshots, `kdf: argon2id`; `history --verify`: `OK: 2 versions verified`. The index was read via the fullnode.

#### Python backend

MemoryIndex [`0x3d9a398d…f8e3`](https://testnet.suivision.xyz/object/0x3d9a398d710b53fdb2b5c02be50d00eb6547ddeeaaf9251dd67f6aa702e4f8e3), created in [`6qccZU3id4…`](https://testnet.suivision.xyz/txblock/6qccZU3id4W692jg7ZMnHdMnkPKd9rrubKW4F4MNBa61).

| Version | Content hash | Walrus blob | Sui transaction |
| --- | --- | --- | --- |
| 1 | `4b902e15b0e77c1a` | [`XsPJJ9eM4b…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/XsPJJ9eM4bZdgKD4QSyAKxLTkEDsIB28lK-nonNnjFQ) | [`DX5gEG2igL…`](https://testnet.suivision.xyz/txblock/DX5gEG2igLqN3SHcGyLqXt4mjqUoR41hhrqKu3QZQ8H9) |
| 2 | `c49137bc2929509d` | [`6Otf-WaYhj…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/6Otf-WaYhjyYIHiBzdUUvx4xobLMnQ9j4lz10I-3tC8) | [`A1iJJbBXDY…`](https://testnet.suivision.xyz/txblock/A1iJJbBXDYCBc7YY6YCVqJSoYc1NfgN9p2o4kxBG3mkb) |

Result: local store wiped; restore returned 2 entries, 2 snapshots, `kdf: argon2id`; `history --verify`: `OK: 2 versions verified`, read via the fullnode.

#### Cross-backend

Each backend restores and then extends the other backend's index, and the other backend verifies the result.

- Rust restores the Python index [`0x3d9a398d…f8e3`](https://testnet.suivision.xyz/object/0x3d9a398d710b53fdb2b5c02be50d00eb6547ddeeaaf9251dd67f6aa702e4f8e3) (2 entries) and appends version 3: [`utsan8-bgH…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/utsan8-bgHJxxYeMYBVSgolQkmrMIWlWfGVy_dcZhww), [`3PPioHmvnU…`](https://testnet.suivision.xyz/txblock/3PPioHmvnUueDwTuvRgrywjLntBH28wDouVnmtJLCvFp).
- Python restores the Rust index [`0x0a844d3a…26da`](https://testnet.suivision.xyz/object/0x0a844d3acf44c788b878a64a788179dcd677929816177bd3d83784711c9726da) (2 entries) and appends version 3: [`NWi3OmFG13…`](https://aggregator.walrus-testnet.walrus.space/v1/blobs/NWi3OmFG13dt2gYmKtrF3iVIUSZySOVRADXkBv2Ow-c), [`634bmLUCkq…`](https://testnet.suivision.xyz/txblock/634bmLUCkqYXhpbiEAf6KZEwzzNfqCT3LYTBcaBptFvY).

After each cross write, the first backend restores 3 entries and `history --verify` reports `OK: 3 versions verified`.

#### Chain guard

Each backend was pointed at a non-testnet or unreachable `sui_rpc` and asked to `init` and to `flush`. Every attempt was refused before anything was uploaded or signed.

| `sui_rpc` | Backend | `init` and `flush` |
| --- | --- | --- |
| mainnet `https://fullnode.mainnet.sui.io:443` | Python, Rust | `refusing to sign: ... is on chain mainnet (4btiuiMPvEENsttpZC7CZ53DruC3MAgfznDbASZ7DR6S), Mnemos only writes to Sui testnet` |
| devnet `https://fullnode.devnet.sui.io:443` | Python, Rust | `refusing to sign: ... is on chain unknown (9ovasb4imJNPrhAjLcXjanDMwcybDczZR8W5jnWxpVhK), Mnemos only writes to Sui testnet` |
| unreachable `https://127.0.0.1:9` | Python, Rust | `refusing to sign: could not read the chain id of https://127.0.0.1:9` (Python: `_InactiveRpcError`, Rust: `tcp connect error`) |
