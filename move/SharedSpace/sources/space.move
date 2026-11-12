// SPDX-License-Identifier: MIT

/// Mnemos shared space (Beta preview, testnet only).
///
/// A Space is a shared, append-only list of references to Seal-encrypted Walrus
/// blobs. Access is governed by one `seal_allowlist::allowlist::Allowlist`:
///
/// - only current members of the allowlist can publish a reference;
/// - the key id of a published entry must be `[allowlist id][current epoch][nonce]`,
///   so after a removal (which bumps the epoch) every new entry is encrypted to an
///   id the removed member never obtained a key for.
///
/// The Space holds references only. Ciphertexts live on Walrus; keys come from the
/// Seal key servers, which call `allowlist::seal_approve`.
module mnemos_share::space;

use seal_allowlist::allowlist::Allowlist;
use std::string::String;

const EWrongList: u64 = 1;
const ENotMember: u64 = 2;
const EBadKeyId: u64 = 3;
const EStaleEpoch: u64 = 4;
const EBadDigest: u64 = 5;

/// Bytes in an allowlist id prefix: object id (32) + epoch (u64, 8).
const PREFIX_LEN: u64 = 40;

public struct Entry has copy, drop, store {
    /// Walrus blob id of the Seal ciphertext.
    blob_id: String,
    /// Seal inner key id: [allowlist id][epoch][nonce].
    key_id: vector<u8>,
    /// Allowlist epoch the entry was encrypted under.
    epoch: u64,
    /// Member who published the entry.
    author: address,
    /// sha256 of the ciphertext stored on Walrus.
    blob_sha256: vector<u8>,
}

public struct Space has key {
    id: UID,
    list_id: ID,
    creator: address,
    entries: vector<Entry>,
}

/// Creates and shares a Space bound to `list`. Returns its id.
public fun create(list: &Allowlist, ctx: &mut TxContext): ID {
    let space = Space {
        id: object::new(ctx),
        list_id: object::id(list),
        creator: ctx.sender(),
        entries: vector[],
    };
    let id = object::id(&space);
    transfer::share_object(space);
    id
}

/// Appends a reference. The sender must be a current member and the key id must use
/// the current epoch of the allowlist.
public fun publish(
    space: &mut Space,
    list: &Allowlist,
    blob_id: String,
    key_id: vector<u8>,
    blob_sha256: vector<u8>,
    ctx: &TxContext,
) {
    assert!(object::id(list) == space.list_id, EWrongList);
    assert!(list.is_member(ctx.sender()), ENotMember);
    assert!(blob_sha256.length() == 32, EBadDigest);
    let epoch = list.epoch();
    check_key_id(list, &key_id, epoch);
    space.entries.push_back(Entry { blob_id, key_id, epoch, author: ctx.sender(), blob_sha256 });
}

fun check_key_id(list: &Allowlist, key_id: &vector<u8>, epoch: u64) {
    assert!(key_id.length() > PREFIX_LEN, EBadKeyId);
    let list_bytes = object::id(list).to_bytes();
    let mut i = 0;
    while (i < 32) {
        assert!(key_id[i] == list_bytes[i], EBadKeyId);
        i = i + 1;
    };
    let current = list.prefix(epoch);
    while (i < PREFIX_LEN) {
        assert!(key_id[i] == current[i], EStaleEpoch);
        i = i + 1;
    };
}

public fun list_id(space: &Space): ID { space.list_id }

public fun creator(space: &Space): address { space.creator }

public fun length(space: &Space): u64 { space.entries.length() }

public fun entry_epoch(space: &Space, i: u64): u64 { space.entries[i].epoch }

#[test_only]
use seal_allowlist::allowlist;

#[test_only]
fun key_id(list: &Allowlist, epoch: u64): vector<u8> {
    let mut id = list.prefix(epoch);
    id.push_back(7);
    id
}

#[test_only]
fun digest(): vector<u8> {
    let mut d = vector[];
    let mut i: u8 = 0;
    while (i < 32) {
        d.push_back(i);
        i = i + 1;
    };
    d
}

#[test_only]
fun destroy_space(space: Space) {
    let Space { id, .. } = space;
    object::delete(id);
}

#[test_only]
fun new_space(list: &Allowlist, ctx: &mut TxContext): Space {
    Space { id: object::new(ctx), list_id: object::id(list), creator: ctx.sender(), entries: vector[] }
}

#[test]
fun member_publishes_current_epoch() {
    let mut ctx = tx_context::new_from_hint(@0xA, 0, 0, 0, 0);
    let (cap, mut list) = allowlist::create(&mut ctx);
    list.add(&cap, @0xA);
    let mut space = new_space(&list, &mut ctx);
    publish(&mut space, &list, b"blob".to_string(), key_id(&list, 0), digest(), &ctx);
    assert!(space.length() == 1, 0);
    assert!(space.entry_epoch(0) == 0, 1);
    list.add(&cap, @0xB);
    list.remove(&cap, @0xB);
    publish(&mut space, &list, b"blob2".to_string(), key_id(&list, 1), digest(), &ctx);
    assert!(space.entry_epoch(1) == 1, 2);
    destroy_space(space);
    allowlist::destroy_for_testing(list, cap);
}

#[test, expected_failure(abort_code = ENotMember)]
fun outsider_cannot_publish() {
    let mut ctx = tx_context::new_from_hint(@0xC, 0, 0, 0, 0);
    let (cap, list) = allowlist::create(&mut ctx);
    let mut space = new_space(&list, &mut ctx);
    publish(&mut space, &list, b"blob".to_string(), key_id(&list, 0), digest(), &ctx);
    destroy_space(space);
    allowlist::destroy_for_testing(list, cap);
}

#[test, expected_failure(abort_code = EStaleEpoch)]
fun stale_epoch_refused_after_removal() {
    let mut ctx = tx_context::new_from_hint(@0xA, 0, 0, 0, 0);
    let (cap, mut list) = allowlist::create(&mut ctx);
    list.add(&cap, @0xA);
    list.add(&cap, @0xB);
    let old = key_id(&list, 0);
    list.remove(&cap, @0xB);
    let mut space = new_space(&list, &mut ctx);
    publish(&mut space, &list, b"blob".to_string(), old, digest(), &ctx);
    destroy_space(space);
    allowlist::destroy_for_testing(list, cap);
}

#[test, expected_failure(abort_code = EStaleEpoch)]
fun future_epoch_refused() {
    let mut ctx = tx_context::new_from_hint(@0xA, 0, 0, 0, 0);
    let (cap, mut list) = allowlist::create(&mut ctx);
    list.add(&cap, @0xA);
    let future = key_id(&list, 1);
    let mut space = new_space(&list, &mut ctx);
    publish(&mut space, &list, b"blob".to_string(), future, digest(), &ctx);
    destroy_space(space);
    allowlist::destroy_for_testing(list, cap);
}

#[test, expected_failure(abort_code = EBadKeyId)]
fun foreign_list_prefix_refused() {
    let mut ctx = tx_context::new_from_hint(@0xA, 0, 0, 0, 0);
    let (cap, mut list) = allowlist::create(&mut ctx);
    let (cap2, list2) = allowlist::create(&mut ctx);
    list.add(&cap, @0xA);
    let mut space = new_space(&list, &mut ctx);
    publish(&mut space, &list, b"blob".to_string(), key_id(&list2, 0), digest(), &ctx);
    destroy_space(space);
    allowlist::destroy_for_testing(list, cap);
    allowlist::destroy_for_testing(list2, cap2);
}

#[test, expected_failure(abort_code = EWrongList)]
fun wrong_list_refused() {
    let mut ctx = tx_context::new_from_hint(@0xA, 0, 0, 0, 0);
    let (cap, mut list) = allowlist::create(&mut ctx);
    let (cap2, mut list2) = allowlist::create(&mut ctx);
    list.add(&cap, @0xA);
    list2.add(&cap2, @0xA);
    let mut space = new_space(&list, &mut ctx);
    publish(&mut space, &list2, b"blob".to_string(), key_id(&list2, 0), digest(), &ctx);
    destroy_space(space);
    allowlist::destroy_for_testing(list, cap);
    allowlist::destroy_for_testing(list2, cap2);
}

#[test, expected_failure(abort_code = EBadDigest)]
fun bad_digest_refused() {
    let mut ctx = tx_context::new_from_hint(@0xA, 0, 0, 0, 0);
    let (cap, mut list) = allowlist::create(&mut ctx);
    list.add(&cap, @0xA);
    let mut space = new_space(&list, &mut ctx);
    publish(&mut space, &list, b"blob".to_string(), key_id(&list, 0), b"short", &ctx);
    destroy_space(space);
    allowlist::destroy_for_testing(list, cap);
}
