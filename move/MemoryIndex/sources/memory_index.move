/// Owned index of encrypted Mnemos snapshots.
///
/// The object stores ordered blob ids, the sessions that produced them, a
/// version counter, and the hash-chain head. Each append must extend the head
/// and carry an Ed25519 signature over `b"mnemos-snapshot-v1" || content_hash`
/// from the key fixed at creation. The encrypted bytes live on Walrus; this
/// object only commits the verifiable chain.
module mnemos::memory_index;

use sui::ed25519;
use sui::event;

const E_BAD_PUBKEY: u64 = 1;
const E_BAD_HASH: u64 = 2;
const E_CHAIN: u64 = 3;
const E_SIG: u64 = 4;
const E_BLOB: u64 = 5;
const E_FULL: u64 = 6;
const E_VERSION: u64 = 7;
const MAX_SNAPSHOTS: u64 = 32;

public struct Snapshot has store, copy, drop {
    blob_id: vector<u8>,
    session_id: vector<u8>,
    content_hash: vector<u8>,
    prev_hash: vector<u8>,
    signature: vector<u8>,
    version: u64,
}

public struct MemoryIndex has key {
    id: UID,
    version: u64,
    head: vector<u8>,
    pubkey: vector<u8>,
    snapshots: vector<Snapshot>,
    sessions: vector<vector<u8>>,
}

public struct IndexCreated has copy, drop {
    id: ID,
    pubkey: vector<u8>,
}

public struct SnapshotAppended has copy, drop {
    index_id: ID,
    version: u64,
    content_hash: vector<u8>,
    prev_hash: vector<u8>,
    blob_id: vector<u8>,
}

fun genesis_head(): vector<u8> {
    let mut zeros = vector[];
    let mut i = 0u64;
    while (i < 32) {
        zeros.push_back(0u8);
        i = i + 1;
    };
    zeros
}

public fun create(pubkey: vector<u8>, ctx: &mut TxContext) {
    assert!(pubkey.length() == 32, E_BAD_PUBKEY);
    let index = MemoryIndex {
        id: object::new(ctx),
        version: 0,
        head: genesis_head(),
        pubkey,
        snapshots: vector[],
        sessions: vector[],
    };
    event::emit(IndexCreated { id: object::id(&index), pubkey: index.pubkey });
    transfer::transfer(index, ctx.sender());
}

fun remember_session(index: &mut MemoryIndex, session_id: vector<u8>) {
    let mut i = 0u64;
    let n = index.sessions.length();
    while (i < n) {
        if (index.sessions[i] == session_id) {
            return
        };
        i = i + 1;
    };
    index.sessions.push_back(session_id);
}

public fun append(
    index: &mut MemoryIndex,
    blob_id: vector<u8>,
    session_id: vector<u8>,
    content_hash: vector<u8>,
    prev_hash: vector<u8>,
    signature: vector<u8>,
    version: u64,
) {
    assert!(blob_id.length() > 0, E_BLOB);
    assert!(session_id.length() > 0, E_BLOB);
    assert!(content_hash.length() == 32, E_BAD_HASH);
    assert!(prev_hash.length() == 32, E_BAD_HASH);
    assert!(signature.length() == 64, E_SIG);
    assert!(version == index.version + 1, E_VERSION);
    assert!(index.snapshots.length() < MAX_SNAPSHOTS, E_FULL);
    assert!(prev_hash == index.head, E_CHAIN);
    let mut message = b"mnemos-snapshot-v1";
    message.append(content_hash);
    assert!(ed25519::ed25519_verify(&signature, &index.pubkey, &message), E_SIG);
    let snap = Snapshot {
        blob_id,
        session_id,
        content_hash,
        prev_hash,
        signature,
        version,
    };
    event::emit(SnapshotAppended {
        index_id: object::id(index),
        version,
        content_hash: snap.content_hash,
        prev_hash: snap.prev_hash,
        blob_id: snap.blob_id,
    });
    index.head = snap.content_hash;
    remember_session(index, snap.session_id);
    index.snapshots.push_back(snap);
    index.version = version;
}

public fun version(index: &MemoryIndex): u64 { index.version }

public fun head(index: &MemoryIndex): vector<u8> { index.head }

public fun pubkey(index: &MemoryIndex): vector<u8> { index.pubkey }
