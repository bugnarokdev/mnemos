# Configuration

Non-secret settings live in `$HERMES_HOME/mnemos/config.json`. Secrets live in the profile secret store and are read with `get_secret`.

## Secrets

| Variable | Required | Role |
| --- | --- | --- |
| `MNEMOS_SECRET` | yes | Passphrase. HKDF-SHA256 derives the XChaCha20-Poly1305 key, the snapshot Ed25519 seed, and, when no Sui key is set, the gas key. |
| `MNEMOS_SUI_PRIVATE_KEY` | no | Optional gas key. The Rust extension accepts `suiprivkey` bech32, Sui keystore base64, or a 32-byte hex seed. The Python backend accepts a 32-byte hex seed. When empty, both derive the gas key from `MNEMOS_SECRET`. |
| `MNEMOS_BACKEND` | no | `python` forces the Python gRPC writer. Empty prefers `mnemos_core` when it can be loaded. |
| `MNEMOS_NATIVE_AUTO` | no | Default on. Set `0` to skip the release-wheel download. |
| `MNEMOS_NATIVE_BASE` | no | Override the release URL used for that download. Default is the `mnemos-native-v0.2.1` release on `https://github.com/bugnarokdev/mnemos`. |

`hermes memory setup` prompts for those two secrets and for `index_object_id`.

## File settings

Defaults are the public testnet endpoints.

| Key | Default |
| --- | --- |
| `network` | `testnet` |
| `sui_rpc` | `https://fullnode.testnet.sui.io:443` |
| `graphql_url` | `https://graphql.testnet.sui.io/graphql` |
| `faucet_url` | `https://faucet.testnet.sui.io` |
| `walrus_publisher` | `https://publisher.walrus-testnet.walrus.space` |
| `walrus_aggregator` | `https://aggregator.walrus-testnet.walrus.space` |
| `epochs` | `5` |
| `package_id` | empty until `hermes mnemos init` |
| `index_object_id` | empty until init, or set to an existing object for restore |

`sui_rpc` is the gRPC fullnode. JSON-RPC on the public fullnodes is retired. A later mainnet deployment can point `sui_rpc` at another gRPC endpoint, including a dRPC URL, without a code change.

`epochs` is how many Walrus epochs the publisher should store each blob. Testnet epochs are short and the network can be wiped. Do not treat testnet blobs as durable.

The dashboard panel is `config_schema.py`. It imports Hermes's memory config types, which is the supported way to declare the panel. `hermes plugins validate` may warn that those imports are private; the plugin still loads.

## Commands

| Command | What it does |
| --- | --- |
| `hermes memory status` | Built-in status. Shows the Mnemos section from `get_status_config`. |
| `hermes mnemos status` | Local entry count, chain head, backend (`rust` or `python`). |
| `hermes mnemos doctor` | Secret, store, Walrus, GraphQL, and the index object. Exit code 1 when the local store is not usable. |
| `hermes mnemos init --bytecode FILE` | Faucet, publish, create the owned object. |
| `hermes mnemos flush` | Commit the outbox. |
| `hermes mnemos restore --object-id 0x...` | Rebuild the local cache. |

These `hermes mnemos` commands appear only when `memory.provider` is `mnemos`.
