# Verifiable history

Each snapshot's content hash commits to the previous snapshot's content hash. The `MemoryIndex` stores that hash as its head and checks the link before it accepts an append.

## Content hash

```text
preimage = "MNEMOS1\0"
         || prev_hash          (32 bytes)
         || sha256(blob)       (32 bytes)
         || version            (u64 little-endian)
         || u32le(len) || session_id
         || u32le(len) || blob_id
content_hash = SHA-256(preimage)
```

The signature is Ed25519 over `mnemos-snapshot-v1 || content_hash`, using the snapshot key derived from `MNEMOS_SECRET`. The object is created with that public key. `append` aborts unless:

- the previous hash equals the current head (32 zero bytes at genesis),
- the version is exactly `head_version + 1`,
- `ed25519_verify` accepts the signature over that message.

The contract does not download the Walrus blob. A full check, including "this blob id really is this ciphertext", happens in the client during restore: recompute the hash from the downloaded bytes and compare. Anyone with the blobs and the object can do that without the secret. Reading the facts still requires the secret.

## What is next

Sharing a read capability through Seal, and a small UI that walks the chain, are not in this release. The object layout and the hash link are the parts those features need, and they are enforced now.
