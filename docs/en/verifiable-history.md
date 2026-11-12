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

## Check it yourself

```bash
hermes mnemos-sui history                       # list every version
hermes mnemos-sui history --verify              # check every version
hermes mnemos-sui history --verify --object-id 0x...  --json
```

`--object-id` defaults to `index_object_id` from the config. No secret is needed. For each snapshot, `--verify`:

1. checks the version is the previous one plus 1 (1 at genesis),
2. checks `prev_hash` equals the previous snapshot's content hash (32 zero bytes at genesis),
3. downloads the blob from the Walrus aggregator and recomputes `content_hash` from its sha256,
4. verifies the Ed25519 signature against the public key stored in the object.

It prints one line per version, `OK` or `BROKEN` with the reason, then a verdict. The index head must equal the last content hash. Any failure exits with status 1. A swapped blob, an edited hash, a broken link, or a forged signature is reported at the version where it happens.

```text
MemoryIndex 0x40028382ba48edaff90240696fb0441854046d9bee10195d296d2046a72668e3  pubkey c5f2488e608132c3
v1    OK     03053bacf1305209  blob ymHOBfSei9ECmnM_CoA-4Q2oLtTX3tYgVgvN4Hngxiw
OK: 1 version verified
```

## What is next

Sharing a read capability through Seal, and a graphical UI that walks the chain, are not in this release. The object layout and the hash link are the parts those features need, and they are enforced now.
