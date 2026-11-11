# Configuration

Non-secret settings live in `$HERMES_HOME/mnemos/config.json`. Secrets live in the profile secret store and are read with `get_secret`.

## Secrets

| Variable | Required | Role |
| --- | --- | --- |
| `MNEMOS_SECRET` | yes | Passphrase. Argon2id (64 MiB, 3 passes) stretches it, then HKDF-SHA256 derives the XChaCha20-Poly1305 key, the snapshot Ed25519 seed, and, when no Sui key is set, the gas key. See [security](security.md). |
| `MNEMOS_SUI_PRIVATE_KEY` | no | Optional gas key. The Rust extension accepts `suiprivkey` bech32, Sui keystore base64, or a 32-byte hex seed. The Python backend accepts a 32-byte hex seed. When empty, both derive the gas key from `MNEMOS_SECRET`. |
| `MNEMOS_BACKEND` | no | `python` forces the Python gRPC writer. Empty prefers `mnemos_core` when it can be loaded. |
| `MNEMOS_NATIVE_AUTO` | no | Default off. Set `1` to opt in to downloading the native wheel from the official `mnemos-native-v0.2.2` release on `https://github.com/bugnarokdev/mnemos`. The URL cannot be changed and the wheel must match a pinned sha256. |

`hermes memory setup` prompts for those two secrets and for `index_object_id`.

## File settings

Defaults are the public testnet endpoints.

| Key | Default |
| --- | --- |
| `network` | `testnet` |
| `sui_rpc` | `https://fullnode.testnet.sui.io:443` (must report the Sui testnet chain id, or every write is refused) |
| `graphql_url` | `https://graphql.testnet.sui.io/graphql` |
| `faucet_url` | `https://faucet.testnet.sui.io` |
| `walrus_publisher` | `https://publisher.walrus-testnet.walrus.space` |
| `walrus_aggregator` | `https://aggregator.walrus-testnet.walrus.space` |
| `epochs` | `5` |
| `package_id` | empty until `hermes mnemos init`; after a restore it is read from the MemoryIndex type on Sui |
| `index_object_id` | empty until init, or set to an existing object for restore |

`sui_rpc` is the gRPC fullnode. JSON-RPC on the public fullnodes is retired. A later mainnet deployment can point `sui_rpc` at another gRPC endpoint, including a dRPC URL, without a code change.

`epochs` is how many Walrus epochs the publisher should store each blob. Testnet epochs are short and the network can be wiped. Do not treat testnet blobs as durable.

The dashboard panel is `config_schema.py`. It declares `CONFIG_SCHEMA` as plain data; Hermes rebuilds that record into its panel types, so the file does not import Hermes internals.

## Commands

| Command | What it does |
| --- | --- |
| `hermes memory status` | Built-in status. Shows the Mnemos section from `get_status_config`. |
| `hermes mnemos status` | Local entry count, chain head, backend (`rust` or `python`). |
| `hermes mnemos doctor` | Secret, store, Walrus, GraphQL, and the index object. Exit code 1 when the local store is not usable. |
| `hermes mnemos init --bytecode FILE` | Faucet, publish, create the owned object. |
| `hermes mnemos flush` | Commit the outbox. |
| `hermes mnemos restore --object-id 0x...` | Rebuild the local cache. |
| `hermes mnemos history [--verify] [--object-id 0x...] [--json]` | List the snapshots; `--verify` checks hashes, links, and signatures against Walrus without the secret. Exit code 1 on any failure. |

These `hermes mnemos` commands appear only when `memory.provider` is `mnemos`.
