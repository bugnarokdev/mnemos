# Security and encryption

The user secret never leaves the process, and plaintext never leaves the machine.

## Keys

`MNEMOS_SECRET` is the input keying material. HKDF-SHA256 (RFC 5869) derives three 32-byte values:

| Salt | Info | Use |
| --- | --- | --- |
| `mnemos-aead-v1` | `xchacha20-poly1305` | Blob encryption |
| `mnemos-sign-v1` | `ed25519-seed` | Snapshot signatures |
| `mnemos-sui-v1` | `ed25519-seed` | Sui gas key, only if `MNEMOS_SUI_PRIVATE_KEY` is unset |

The snapshot public key is stored in the `MemoryIndex` at creation. Later appends must verify against that key. Someone who steals only the Sui gas key cannot append a snapshot the contract will accept. Someone who steals only the secret can decrypt blobs they can download, and can produce signatures, but cannot append unless they also control the object.

## Blob

```text
"MNMS" || version 1 || 24-byte nonce || XChaCha20-Poly1305(ciphertext || tag)
```

The associated data is the fixed string `mnemos-blob-v1`. The plaintext is canonical JSON:

```json
{"entries":[{"id":"...","kind":"fact","session_id":"...","text":"...","ts":0}],"v":1}
```

Walrus stores those bytes and returns a blob id. The blob id is not secret. The bytes are not readable without the secret.

## What is public

Sui objects are public. Anyone who knows the object id can read blob ids, session ids, content hashes, previous hashes, signatures, and the snapshot public key. They cannot read the facts unless they also have `MNEMOS_SECRET`.

## What prefetch sends to the model

Prefetch injects matching plaintext from the local cache into the Hermes prompt. That is the point of memory. It is not sent to Walrus or Sui. Tool calls and tool results that Hermes mirrors through `on_memory_write` or `sync_turn` can contain workspace text; treat the secret as protecting that text at rest off the machine, not as hiding it from the model on this machine.

## Catalog notes

Secrets are declared in `plugin.yaml` and read only through `get_secret`. The plugin does not read `auth.json`, another profile's files, or a shell child in YOLO mode. Doctor and faucet calls use timeouts. `on_session_end` stops waiting after 20 seconds.
