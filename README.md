# Mnemos

Mnemos is a memory provider plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It gives the agent a sovereign memory: facts are encrypted on the machine that writes them, stored as blobs on Walrus testnet, and indexed by an owned Sui object. Another machine restores that memory with the same user secret and the Sui object id.

```text
Hermes  -->  local SQLite cache  -->  prefetch (no network)
                |
                +--> outbox --> XChaCha20-Poly1305
                                  |
                                  +--> Walrus testnet  (ciphertext blob)
                                  +--> Sui testnet     (MemoryIndex: blob ids,
                                                        hash-chain head, signatures)
```

## Documentation

English and French are kept in sync.

| Topic | English | Français |
| --- | --- | --- |
| Overview | [docs/en/overview.md](docs/en/overview.md) | [docs/fr/overview.md](docs/fr/overview.md) |
| Installation | [docs/en/installation.md](docs/en/installation.md) | [docs/fr/installation.md](docs/fr/installation.md) |
| Configuration | [docs/en/configuration.md](docs/en/configuration.md) | [docs/fr/configuration.md](docs/fr/configuration.md) |
| Architecture | [docs/en/architecture.md](docs/en/architecture.md) | [docs/fr/architecture.md](docs/fr/architecture.md) |
| Security and encryption | [docs/en/security.md](docs/en/security.md) | [docs/fr/security.md](docs/fr/security.md) |
| Restore and migration | [docs/en/restore.md](docs/en/restore.md) | [docs/fr/restore.md](docs/fr/restore.md) |
| Verifiable history | [docs/en/verifiable-history.md](docs/en/verifiable-history.md) | [docs/fr/verifiable-history.md](docs/fr/verifiable-history.md) |
| Sui and Walrus testnet | [docs/en/deployment.md](docs/en/deployment.md) | [docs/fr/deployment.md](docs/fr/deployment.md) |
| Troubleshooting | [docs/en/troubleshooting.md](docs/en/troubleshooting.md) | [docs/fr/troubleshooting.md](docs/fr/troubleshooting.md) |

## Install

Hermes 0.23.1 or newer. Put this repository at `$HERMES_HOME/plugins/mnemos` and set `memory.provider: mnemos`. Full steps are in the installation guide.

`hermes plugins validate --install-deps` installs the Python dependencies, including the gRPC client that writes to Sui and Walrus. A Rust extension is optional. By default Mnemos uses the Python backend and downloads nothing. Set `MNEMOS_NATIVE_AUTO=1` to opt in to the native wheel: it comes only from this repository's official GitHub release and must match a sha256 pinned in the code (that call is disclosed below). Both backends produce the same ciphertext, content hash, and signature. See [installation](docs/en/installation.md).

## Verify the history

`hermes mnemos history` lists every snapshot in the MemoryIndex. `hermes mnemos history --verify` downloads each blob from Walrus, recomputes its content hash and the link to the previous one, checks the Ed25519 signature, prints `OK` or `BROKEN` per version, and exits non-zero on any failure. It needs no secret. See [verifiable history](docs/en/verifiable-history.md).

## Demo

`scripts/demo.sh` writes a fact on profile A, publishes an encrypted blob and updates the Sui index, wipes that profile, restores into profile B, and checks that Hermes prefetch recalls the fact. It also prints local prefetch latency.

## Network disclosure

When a flush, checkpoint, restore, init, doctor, or history runs, Mnemos calls:

- Walrus testnet publisher `https://publisher.walrus-testnet.walrus.space` (`PUT /v1/blobs`)
- Walrus testnet aggregator `https://aggregator.walrus-testnet.walrus.space` (`GET /v1/blobs/<id>`)
- Sui testnet gRPC `https://fullnode.testnet.sui.io:443`
- Sui testnet GraphQL `https://graphql.testnet.sui.io/graphql`
- Sui testnet faucet `https://faucet.testnet.sui.io` during `hermes mnemos init` only
- Opt-in only: the native wheel from `https://github.com/bugnarokdev/mnemos/releases/download/mnemos-native-v0.2.1/` when `MNEMOS_NATIVE_AUTO=1` and `mnemos_core` is not already installed. The URL is fixed and the wheel must match a sha256 pinned in `mnemos/native_wheel.py`; nothing else is fetched. Off by default.

Prefetch and recall do not use the network. Payloads sent to Walrus are ciphertext. The Sui object stores blob ids, hashes, and signatures, not plaintext. There is no telemetry.

## License

MIT. See [LICENSE](LICENSE).
