# Restore and migration

Restore needs two things that are not stored in the wiped home:

1. The same `MNEMOS_SECRET` (and the same `MNEMOS_SUI_PRIVATE_KEY` only if you set one; restore itself only needs the secret).
2. The `MemoryIndex` object id.

```bash
export HERMES_HOME=/path/to/new/profile
# install the plugin and MNEMOS_SECRET the same way as on the old machine
hermes mnemos-sui restore --object-id 0xYOUR_INDEX
```

You can omit `--object-id` when `index_object_id` is already in `mnemos/config.json`.

What restore does:

1. Read the object through Sui GraphQL.
2. Check that the on-chain public key matches the key derived from this secret (Argon2id; for an index created before 0.2.2, the old HKDF key, see below).
3. Download each blob from the Walrus aggregator, in version order.
4. Recompute the content hash from the previous hash, the blob bytes, the version, the session id, and the blob id.
5. Check that hash against the object, check the Ed25519 signature, and check that each previous hash equals the prior content hash.
6. Decrypt. If the secret is wrong, decryption fails and the local database is left unchanged.
7. Replace the local entries with the decrypted facts.

The new profile can then `prefetch` and `mnemos_recall` without another network call.

## Moving a profile

Copy `MNEMOS_SECRET` into the new profile's secret store yourself. Do not commit it. Only `index_object_id` is needed: `hermes mnemos-sui restore --object-id` saves it, and the `package_id` is read from the object's Move type and saved too, so a later `flush` works without `init`. `hermes mnemos-sui restore` rebuilds `mnemos.sqlite`. The old SQLite file is not required.

`backup_paths()` returns an empty list because the provider state that lives outside the chain is under `HERMES_HOME`, which Hermes already archives. The chain is the backup that survives deleting that home.

## Indexes created before 0.2.2

An index created before 0.2.2 has a public key from the old derivation (a single HKDF). `restore` detects it, decrypts with the old key, and returns `"kdf": "legacy", "read_only": true`. `history --verify` works without the secret. A `flush` to that index is refused; run `hermes mnemos-sui init` to create a new index with the Argon2id key. New facts go there, and the old index stays readable. Details: [security](security.md#memories-created-before-022).

## Limits

One index holds at most 32 snapshots. When it is full, append aborts. Rotating to a new object that links the previous head is not in this release. Testnet Walrus and testnet Sui can be wiped by their operators. After a wipe, the object id will not restore.
