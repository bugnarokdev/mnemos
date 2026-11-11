# Security and encryption

The user secret never leaves the process, and plaintext never leaves the machine.

## Keys

`MNEMOS_SECRET` is a passphrase, and it is stretched before use. Argon2id (RFC 9106, version 0x13) turns it into a 32-byte master key with these parameters:

| Parameter | Value |
| --- | --- |
| Memory | 64 MiB (`m = 65536` KiB) |
| Passes | 3 (`t = 3`) |
| Lanes | 1 (`p = 1`) |
| Salt | the fixed string `mnemos-kdf-v2/argon2id` |
| Output | 32 bytes |

The salt is fixed on purpose: a restore on a new machine has only the passphrase and the object id, and the signing public key must be known before the MemoryIndex exists. The cost of each guess is the protection, so pick a long passphrase (four or more random words). Argon2id runs once per process and the result is cached in memory.

HKDF-SHA256 (RFC 5869) then derives three 32-byte values from the master key:

| Salt | Info | Use |
| --- | --- | --- |
| `mnemos-aead-v2` | `xchacha20-poly1305` | Blob encryption |
| `mnemos-sign-v2` | `ed25519-seed` | Snapshot signatures |
| `mnemos-sui-v2` | `ed25519-seed` | Sui gas key, only if `MNEMOS_SUI_PRIVATE_KEY` is unset |

The Python and Rust backends derive the same bytes; `tests/test_kdf.py` and the `native/src/chain.rs` tests pin the same vectors.

The snapshot public key is stored in the `MemoryIndex` at creation. Later appends must verify against that key. Someone who steals only the Sui gas key cannot append a snapshot the contract will accept. Someone who steals only the secret can decrypt blobs they can download, and can produce signatures, but cannot append unless they also control the object.

### Memories created before 0.2.2

Before 0.2.2 the three keys came from a single HKDF over the passphrase (salts `mnemos-aead-v1`, `mnemos-sign-v1`, `mnemos-sui-v1`). That is fast to brute-force offline, because the public key and the ciphertext are public. Mnemos still reads those indexes: `restore` sees that the on-chain public key matches the old derivation and decrypts with the old key, and `history --verify` needs no secret at all. Such an index is read-only: no new snapshot is signed with the old keys. To keep writing, run `hermes mnemos init` to create a new MemoryIndex, which gets the Argon2id key. New facts go to the new index; the old one stays readable and verifiable for the earlier history. There is no setting to choose the old derivation.

## Chain guard

Every transaction that Mnemos signs (init, flush, checkpoint, and the model-callable `mnemos_flush`) first asks the configured `sui_rpc` for its chain id (`LedgerService.GetServiceInfo`) and refuses unless it is the Sui testnet genesis digest `69WiPg3DAQiwdxfncX6wYQ2siKwAe6L9BZthQea3JNMD` (`4c78adac` in short form). The check fails closed: an error, a timeout, a missing chain id, or any other chain stops the write before anything is signed or uploaded. Both backends run it, once before the Walrus upload and again right before signing. There is no override and no mainnet mode, so there is no non-testnet write to approve.

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

The native wheel download is off by default. With `MNEMOS_NATIVE_AUTO=1` it fetches only from this repository's official `mnemos-native-v0.2.2` GitHub release, and the wheel must match a sha256 pinned in `mnemos/native_wheel.py`; there is no URL override and no checksum is read from the network. The wheel is extracted under `$HERMES_HOME/mnemos/native/mnemos-native-v0.2.2/` and that directory is appended to `sys.path`, so it never shadows another module. A `mnemos_core` older than 0.2.2 (without the Argon2id derivation) is ignored and the Python backend is used. Each wheel is built and attested by the repository's public GitHub Actions workflow from the `mnemos-native-v0.2.2` tag; the commit after the tag only adds the checksum table to `mnemos/native_wheel.py`, which is not part of the wheel.

Secrets are declared in `plugin.yaml` and read only through `get_secret`. The plugin does not read `auth.json`, another profile's files, or a shell child in YOLO mode. Doctor and faucet calls use timeouts. `on_session_end` stops waiting after 20 seconds.
