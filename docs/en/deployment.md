# Sui and Walrus testnet

## Endpoints

| Service | Default |
| --- | --- |
| Sui gRPC | `https://fullnode.testnet.sui.io:443` |
| Sui GraphQL | `https://graphql.testnet.sui.io/graphql` |
| Faucet | `https://faucet.testnet.sui.io` |
| Walrus publisher | `https://publisher.walrus-testnet.walrus.space` |
| Walrus aggregator | `https://aggregator.walrus-testnet.walrus.space` |

Override them in `mnemos/config.json`. Writes use gRPC (`sui-rpc` from the Mysten Rust SDK). Reads of the index use GraphQL because the object content comes back as JSON. The public fullnodes no longer serve JSON-RPC.

Walrus store is `PUT /v1/blobs?epochs=N` with the raw ciphertext. Read is `GET /v1/blobs/<blob id>`. The publisher response is either `newlyCreated.blobObject.blobId` or `alreadyCertified.blobId`.

## Move package

`move/MemoryIndex` defines `mnemos::memory_index::MemoryIndex`. The object is owned by the publisher. It stores:

- `version` counter
- `head`, the latest content hash, or 32 zero bytes
- `pubkey`, the snapshot verifying key
- `snapshots`, ordered records of blob id, session id, hashes, and signature
- `sessions`, the distinct session ids seen so far

`create` transfers the object to the transaction sender. `append` is the only mutator. At most 32 snapshots fit in the object.

Compile with the Sui CLI (the framework dependency is the `framework/testnet` revision):

```bash
cd move/MemoryIndex
sui move build --dump-bytecode-as-base64
```

`hermes mnemos-sui init --bytecode` publishes that JSON and calls `create`.

## Faucet

The current testnet faucet (`/v3/challenge` then `/v3/gas`) is a proof-of-work, not a browser captcha. Both backends solve Argon2d for the challenge and submit it. `init` prints the Sui address it is funding.

A human has to step in when:

- the faucet returns a rate limit or a captcha-style error (the v2 endpoint now answers that CI compatibility is disabled; a future v3 may add a Turnstile check),
- the proof-of-work does not finish inside the challenge window.

In that case fund the printed address from the browser faucet at <https://faucet.sui.io> (that page can require a captcha), keep the same `MNEMOS_SECRET`, and run `init` again. If the address already has gas, `init` skips the faucet.

Testnet SUI and testnet WAL have no value. The public publisher pays Walrus storage on testnet. Do not point this build at mainnet expecting a free publisher: Walrus has no public unauthenticated mainnet publisher.

## Gas

Publish asks for a budget of 0.1 SUI. Each append asks for 0.03 SUI. One faucet grant of 1 SUI covers publish, create, and several snapshots. When the balance is already at least 0.2 SUI, `init` does not call the faucet.
