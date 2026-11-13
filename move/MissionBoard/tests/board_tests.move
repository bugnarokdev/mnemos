// SPDX-License-Identifier: MIT

/// Unit tests for the mission board (BETA, testnet preview). Local only: phase P1 of
/// docs/fr/design-mission-board.md. Actors: A project lead, B and C contributors,
/// D third party, E spare address.
///
/// Test `TxContext`s are global in this framework: `ctx.sender()` and fresh object ids
/// come from the most recently created context. Every call therefore builds its context
/// right before the call (`cx`), and every context that creates objects gets a hint
/// derived from a unique object id, so ids never collide.
///
/// The `&Clock` API of the module is kept (it is what Seal key servers pass in the dry
/// run, as in the design); the tests drive that Clock with the test-only setters, hence
/// `deprecated_usage` is allowed here.
#[test_only, allow(deprecated_usage)]
module mnemos_board::board_tests;

use mnemos_board::board::{Self, Board, BoardAdminCap, Mission, DeliveryReceipt, BlobRef};
use std::bcs;
use std::string::String;
use sui::clock::{Self, Clock};
use sui::test_scenario as ts;

const A: address = @0xA;
const B: address = @0xB;
const C: address = @0xC;
const D: address = @0xD;
const E: address = @0xE;

const T0: u64 = 1_000;
const H72: u64 = 259_200_000;
const D7: u64 = 604_800_000;
const D14: u64 = 1_209_600_000;
const H24: u64 = 86_400_000;

// === Helpers ===

fun cx(who: address, hint: u64): TxContext { tx_context::new_from_hint(who, hint, 0, 0, 0) }

fun s(bytes: vector<u8>): String { bytes.to_string() }

/// 48 bits of an object id, as a context hint base.
fun tag(id: ID): u64 {
    let b = id.to_bytes();
    let mut t = 0u64;
    let mut i = 0u64;
    while (i < 6) {
        t = (t << 8) | (b[i] as u64);
        i = i + 1;
    };
    t << 10
}

/// Mission created by the current lead with its own context.
fun mk(
    board: &mut Board,
    title: String,
    labels: vector<String>,
    ttl_ms: u64,
    min_reputation: u64,
    clock: &Clock,
): Mission {
    let mut c = cx(board.lead(), tag(object::id(board)) + 2 * board.next_seq() + 2);
    board::new_mission_for_testing(board, title, labels, ttl_ms, min_reputation, clock, &mut c)
}

fun other_board(name: String): (Board, BoardAdminCap) {
    let mut c = cx(A, 999_999);
    board::new_board_for_testing(name, board::default_params(), &mut c)
}

fun setup(): (TxContext, Clock, Board, BoardAdminCap) {
    let mut ctx = cx(A, 1);
    let mut clock = clock::create_for_testing(&mut ctx);
    clock.set_for_testing(T0);
    let (board, cap) = board::new_board_for_testing(s(b"board"), board::default_params(), &mut ctx);
    (ctx, clock, board, cap)
}

fun digest(): vector<u8> {
    let mut d = vector[];
    let mut i: u8 = 0;
    while (i < 32) {
        d.push_back(i);
        i = i + 1;
    };
    d
}

fun kid(m: &Mission, epoch: u64, kind: u8): vector<u8> {
    let mut id = object::id(m).to_bytes();
    id.append(bcs::to_bytes(&epoch));
    id.push_back(kind);
    let mut i = 0u64;
    while (i < 16) {
        id.push_back(0x5a);
        i = i + 1;
    };
    id
}

fun blob(m: &Mission, epoch: u64, kind: u8): BlobRef {
    board::new_blob_ref(s(b"walrus-blob"), kid(m, epoch, kind), digest())
}

fun post(board: &mut Board, clock: &Clock, _ctx: &mut TxContext): Mission {
    mk(board, s(b"mission"), vector[s(b"rust")], 0, 0, clock)
}

/// Posted and briefed at the current epoch: `Open`.
fun open(board: &mut Board, clock: &Clock, ctx: &mut TxContext): Mission {
    let mut m = post(board, clock, ctx);
    brief(board, &mut m);
    m
}

fun brief(board: &Board, m: &mut Mission) {
    let b = blob(m, m.key_epoch(), board::kind_brief());
    board::publish_brief(board, m, b, &mut cx(board.lead(), 50));
}

fun claim_as(board: &mut Board, m: &mut Mission, who: address, clock: &Clock) {
    board::claim(board, m, option::none(), clock, &mut cx(who, 60));
}

fun submit_as(board: &Board, m: &mut Mission, who: address, clock: &Clock) {
    let out = blob(m, m.claim_epoch(), board::kind_output());
    board::submit(board, m, out, vector[], clock, &mut cx(who, 61));
}

fun accept_as(board: &mut Board, m: &mut Mission, who: address, clock: &Clock) {
    let mut c = cx(who, tag(object::id(m)) + 1);
    board::accept(board, m, clock, &mut c);
}

/// Open, claimed by `who`, submitted and accepted by the current lead.
fun accepted(board: &mut Board, who: address, clock: &Clock, ctx: &mut TxContext): Mission {
    let mut m = open(board, clock, ctx);
    claim_as(board, &mut m, who, clock);
    submit_as(board, &mut m, who, clock);
    let lead = board.lead();
    accept_as(board, &mut m, lead, clock);
    m
}

fun approve(board: &Board, m: &Mission, epoch: u64, kind: u8, who: address, clock: &Clock) {
    board::seal_approve_for_testing(kid(m, epoch, kind), board, m, clock, &mut cx(who, 70));
}

fun done(clock: Clock, board: Board, cap: BoardAdminCap) {
    clock.destroy_for_testing();
    board::destroy_board_for_testing(board);
    board::destroy_cap_for_testing(cap);
}

fun drop_m(m: Mission) { board::destroy_mission_for_testing(m) }

fun rep(board: &Board, who: address): (u64, u64, u64, u64, u64) { board.reputation(who) }

// === Board lifecycle and parameters ===

#[test]
fun defaults_match_design_q9() {
    let (_ctx, clock, board, cap) = setup();
    let p = board.params();
    assert!(p.default_claim_ttl_ms() == H72);
    assert!(p.max_claim_ttl_ms() == D14);
    assert!(p.review_window_ms() == D7);
    assert!(p.max_active_claims() == 2);
    assert!(p.reclaim_cooldown_ms() == H24);
    assert!(p.min_reputation_to_claim() == 0);
    assert!(board.lead() == A && board.creator() == A);
    assert!(board.admin_cap_id() == object::id(&cap));
    assert!(cap.cap_board_id() == object::id(&board));
    assert!(board.pending_lead().is_none());
    assert!(!board.is_frozen() && board.next_seq() == 0 && board.version() == 1);
    assert!(board.name() == s(b"board"));
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::EBadParams)]
fun params_zero_ttl_refused() {
    board::new_params(0, D14, D7, 2, H24, 0);
}

#[test, expected_failure(abort_code = board::EBadParams)]
fun params_default_above_max_refused() {
    board::new_params(D14 + 1, D14, D7, 2, H24, 0);
}

#[test, expected_failure(abort_code = board::EBadParams)]
fun params_zero_review_refused() {
    board::new_params(H72, D14, 0, 2, H24, 0);
}

#[test, expected_failure(abort_code = board::EBadParams)]
fun params_zero_claims_refused() {
    board::new_params(H72, D14, D7, 0, H24, 0);
}

#[test, expected_failure(abort_code = board::ETooLong)]
fun board_name_too_long_refused() {
    let mut ctx = cx(A, 1);
    let (_b, _c) = board::new_board_for_testing(
        s(b"0123456789012345678901234567890123456789012345678901234567890123x"),
        board::default_params(),
        &mut ctx,
    );
    abort 0
}

#[test]
fun lead_sets_params() {
    let (_ctx, clock, mut board, cap) = setup();
    let p = board::new_params(1_000, 10_000, 5_000, 3, 100, 1);
    board::set_params(&mut board, &cap, p, &mut cx(A, 2));
    assert!(board.params().max_active_claims() == 3);
    assert!(board.params().min_reputation_to_claim() == 1);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun set_params_by_other_refused() {
    let (_ctx, _clock, mut board, cap) = setup();
    board::set_params(&mut board, &cap, board::default_params(), &mut cx(B, 2));
    abort 0
}

#[test, expected_failure(abort_code = board::EWrongBoard)]
fun cap_of_other_board_refused() {
    let (_ctx, _clock, mut board, _cap) = setup();
    let (_board2, cap2) = other_board(s(b"other"));
    board::set_params(&mut board, &cap2, board::default_params(), &mut cx(A, 2));
    abort 0
}

#[test]
fun freeze_stops_posts_and_claims_only() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::freeze_board(&mut board, &cap, &mut cx(A, 2));
    assert!(board.is_frozen());
    // Work in progress continues.
    submit_as(&board, &mut m, B, &clock);
    accept_as(&mut board, &mut m, A, &clock);
    assert!(m.status() == board::status_accepted());
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::EFrozen)]
fun frozen_board_refuses_post() {
    let (mut ctx, clock, mut board, cap) = setup();
    board::freeze_board(&mut board, &cap, &mut cx(A, 2));
    let _m = post(&mut board, &clock, &mut ctx);
    abort 0
}

#[test, expected_failure(abort_code = board::EFrozen)]
fun frozen_board_refuses_claim() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    board::freeze_board(&mut board, &cap, &mut cx(A, 2));
    claim_as(&mut board, &mut m, B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun freeze_by_other_refused() {
    let (_ctx, _clock, mut board, cap) = setup();
    board::freeze_board(&mut board, &cap, &mut cx(B, 2));
    abort 0
}

#[test, expected_failure(abort_code = board::EWrongVersion)]
fun wrong_version_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    board.set_version_for_testing(2);
    let _m = post(&mut board, &clock, &mut ctx);
    abort 0
}

#[test, expected_failure(abort_code = board::EWrongVersion)]
fun wrong_version_refuses_claim() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    board.set_version_for_testing(2);
    claim_as(&mut board, &mut m, B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun wrong_version_refuses_seal() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let m = open(&mut board, &clock, &mut ctx);
    board.set_version_for_testing(2);
    approve(&board, &m, 0, board::kind_brief(), A, &clock);
    abort 0
}

// === Shared objects end to end (test_scenario) ===

#[test]
fun scenario_create_post_claim_accept_receipt() {
    let mut sc = ts::begin(A);
    {
        let clock = clock::create_for_testing(sc.ctx());
        clock.share_for_testing();
        board::create(s(b"board"), board::default_params(), sc.ctx());
    };
    sc.next_tx(A);
    let mission_id;
    {
        let mut b = sc.take_shared<Board>();
        let cap = sc.take_from_sender<BoardAdminCap>();
        assert!(b.admin_cap_id() == object::id(&cap));
        let clock = sc.take_shared<Clock>();
        mission_id = board::post(&mut b, s(b"m1"), vector[], 0, 0, &clock, sc.ctx());
        ts::return_shared(b);
        ts::return_shared(clock);
        sc.return_to_sender(cap);
    };
    sc.next_tx(A);
    {
        let b = sc.take_shared<Board>();
        let mut m = sc.take_shared_by_id<Mission>(mission_id);
        assert!(m.status() == board::status_rekey() && m.seq() == 0);
        let br = blob(&m, 0, board::kind_brief());
        board::publish_brief(&b, &mut m, br, sc.ctx());
        assert!(m.status() == board::status_open());
        ts::return_shared(b);
        ts::return_shared(m);
    };
    sc.next_tx(B);
    {
        let mut b = sc.take_shared<Board>();
        let mut m = sc.take_shared_by_id<Mission>(mission_id);
        let clock = sc.take_shared<Clock>();
        board::claim(&mut b, &mut m, option::some(E), &clock, sc.ctx());
        assert!(m.claim_operator() == option::some(E));
        let out = blob(&m, 0, board::kind_output());
        board::submit(&b, &mut m, out, digest(), &clock, sc.ctx());
        ts::return_shared(b);
        ts::return_shared(m);
        ts::return_shared(clock);
    };
    sc.next_tx(A);
    {
        let mut b = sc.take_shared<Board>();
        let mut m = sc.take_shared_by_id<Mission>(mission_id);
        let clock = sc.take_shared<Clock>();
        board::accept(&mut b, &mut m, &clock, sc.ctx());
        ts::return_shared(b);
        ts::return_shared(m);
        ts::return_shared(clock);
    };
    sc.next_tx(B);
    {
        let receipt = sc.take_from_sender<DeliveryReceipt>();
        assert!(receipt.receipt_contributor() == B);
        assert!(receipt.receipt_accepted_by() == A);
        assert!(receipt.receipt_mission_id() == mission_id);
        sc.return_to_sender(receipt);
    };
    sc.end();
}

#[test]
fun scenario_transfer_mints_fresh_cap() {
    let mut sc = ts::begin(A);
    board::create(s(b"board"), board::default_params(), sc.ctx());
    sc.next_tx(A);
    let old_cap_id;
    {
        let mut b = sc.take_shared<Board>();
        let cap = sc.take_from_sender<BoardAdminCap>();
        old_cap_id = object::id(&cap);
        board::propose_transfer(&mut b, &cap, B, sc.ctx());
        assert!(b.pending_lead() == option::some(B));
        ts::return_shared(b);
        sc.return_to_sender(cap);
    };
    sc.next_tx(B);
    {
        let mut b = sc.take_shared<Board>();
        board::accept_transfer(&mut b, sc.ctx());
        assert!(b.lead() == B && b.pending_lead().is_none() && b.creator() == A);
        ts::return_shared(b);
    };
    sc.next_tx(B);
    {
        let mut b = sc.take_shared<Board>();
        let cap = sc.take_from_sender<BoardAdminCap>();
        assert!(object::id(&cap) == b.admin_cap_id());
        assert!(object::id(&cap) != old_cap_id);
        board::set_params(&mut b, &cap, board::default_params(), sc.ctx());
        ts::return_shared(b);
        sc.return_to_sender(cap);
    };
    sc.next_tx(A);
    {
        let b = sc.take_shared<Board>();
        let stale = sc.take_from_sender<BoardAdminCap>();
        assert!(object::id(&stale) == old_cap_id);
        board::destroy_stale_cap(&b, stale);
        ts::return_shared(b);
    };
    sc.end();
}

// === Posting, editing, briefs ===

#[test]
fun post_starts_in_rekey_with_default_ttl() {
    let (mut ctx, clock, mut board, cap) = setup();
    let m = post(&mut board, &clock, &mut ctx);
    assert!(m.status() == board::status_rekey());
    assert!(m.claim_ttl_ms() == H72 && m.seq() == 0 && m.key_epoch() == 0);
    assert!(!m.has_brief() && m.created_by() == A && m.mission_board_id() == object::id(&board));
    assert!(m.title() == s(b"mission") && m.labels() == vector[s(b"rust")]);
    let m2 = mk(&mut board, s(b"t"), vector[], 1_000, 3, &clock);
    assert!(m2.seq() == 1 && m2.claim_ttl_ms() == 1_000 && m2.min_reputation() == 3);
    assert!(board.next_seq() == 2);
    drop_m(m);
    drop_m(m2);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun post_by_other_refused() {
    let (_ctx, clock, mut board, _cap) = setup();
    let mut c = cx(B, 3);
    let _m = board::new_mission_for_testing(&mut board, s(b"t"), vector[], 0, 0, &clock, &mut c);
    abort 0
}

#[test, expected_failure(abort_code = board::ETtlTooLong)]
fun post_ttl_above_max_refused() {
    let (_ctx, clock, mut board, _cap) = setup();
    let _m = mk(&mut board, s(b"t"), vector[], D14 + 1, 0, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ETooLong)]
fun title_too_long_refused() {
    let (_ctx, clock, mut board, _cap) = setup();
    let mut t = vector[];
    let mut i = 0u64;
    while (i < 129) {
        t.push_back(0x61);
        i = i + 1;
    };
    let _m = mk(&mut board, t.to_string(), vector[], 0, 0, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ETooLong)]
fun too_many_labels_refused() {
    let (_ctx, clock, mut board, _cap) = setup();
    let mut l = vector[];
    let mut i = 0u64;
    while (i < 9) {
        l.push_back(s(b"x"));
        i = i + 1;
    };
    let _m = mk(&mut board, s(b"t"), l, 0, 0, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ETooLong)]
fun label_too_long_refused() {
    let (_ctx, clock, mut board, _cap) = setup();
    let l = vector[s(b"012345678901234567890123456789012")];
    let _m = mk(&mut board, s(b"t"), l, 0, 0, &clock);
    abort 0
}

#[test]
fun publish_brief_opens_and_can_be_replaced() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = post(&mut board, &clock, &mut ctx);
    brief(&board, &mut m);
    assert!(m.status() == board::status_open() && m.has_brief());
    brief(&board, &mut m);
    assert!(m.status() == board::status_open());
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun publish_brief_by_other_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = post(&mut board, &clock, &mut ctx);
    let b = blob(&m, 0, board::kind_brief());
    board::publish_brief(&board, &mut m, b, &mut cx(B, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadKeyId)]
fun brief_with_foreign_prefix_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = post(&mut board, &clock, &mut ctx);
    let other = post(&mut board, &clock, &mut ctx);
    let b = blob(&other, 0, board::kind_brief());
    board::publish_brief(&board, &mut m, b, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadKeyId)]
fun brief_with_wrong_kind_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = post(&mut board, &clock, &mut ctx);
    let b = blob(&m, 0, board::kind_notes());
    board::publish_brief(&board, &mut m, b, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EStaleBrief)]
fun brief_with_wrong_epoch_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = post(&mut board, &clock, &mut ctx);
    let b = blob(&m, 1, board::kind_brief());
    board::publish_brief(&board, &mut m, b, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadKeyId)]
fun brief_with_short_key_id_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = post(&mut board, &clock, &mut ctx);
    let mut k = object::id(&m).to_bytes();
    k.append(bcs::to_bytes(&0u64));
    k.push_back(0);
    let b = board::new_blob_ref(s(b"x"), k, digest());
    board::publish_brief(&board, &mut m, b, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadDigest)]
fun blob_ref_bad_digest_refused() {
    board::new_blob_ref(s(b"x"), vector[], b"short");
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun brief_while_claimed_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    brief(&board, &mut m);
    abort 0
}

#[test]
fun edit_updates_public_fields() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    let labels = option::some(vector[s(b"a"), s(b"b")]);
    board::edit(&board, &mut m, option::some(s(b"new")), labels, option::some(5_000), &mut cx(A, 3));
    assert!(m.title() == s(b"new") && m.labels().length() == 2 && m.claim_ttl_ms() == 5_000);
    board::edit(&board, &mut m, option::none(), option::none(), option::none(), &mut cx(A, 4));
    assert!(m.title() == s(b"new"));
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun edit_with_active_claim_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::edit(&board, &mut m, option::some(s(b"x")), option::none(), option::none(), &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::ETtlTooLong)]
fun edit_ttl_above_max_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    board::edit(&board, &mut m, option::none(), option::none(), option::some(D14 + 1), &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadParams)]
fun edit_zero_ttl_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    board::edit(&board, &mut m, option::none(), option::none(), option::some(0), &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun edit_by_other_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    board::edit(&board, &mut m, option::none(), option::none(), option::none(), &mut cx(B, 3));
    abort 0
}

// === Dependencies ===

#[test]
fun dependency_blocks_then_unlock_opens() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut parent = open(&mut board, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 3));
    assert!(child.status() == board::status_blocked());
    assert!(child.deps() == vector[object::id(&parent)] && child.deps_met_count() == 0);
    claim_as(&mut board, &mut parent, B, &clock);
    submit_as(&board, &mut parent, B, &clock);
    accept_as(&mut board, &mut parent, A, &clock);
    board::unlock(&board, &mut child, &parent, &mut cx(D, 4));
    assert!(child.status() == board::status_open() && child.deps_met_count() == 1);
    drop_m(parent);
    drop_m(child);
    done(clock, board, cap);
}

#[test]
fun unlock_without_brief_goes_to_rekey() {
    let (mut ctx, clock, mut board, cap) = setup();
    let parent = accepted(&mut board, B, &clock, &mut ctx);
    let mut parent2 = open(&mut board, &clock, &mut ctx);
    let mut child = post(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent2, &mut cx(A, 3));
    assert!(child.status() == board::status_blocked());
    claim_as(&mut board, &mut parent2, C, &clock);
    submit_as(&board, &mut parent2, C, &clock);
    accept_as(&mut board, &mut parent2, A, &clock);
    board::unlock(&board, &mut child, &parent2, &mut cx(D, 4));
    assert!(child.status() == board::status_rekey());
    // An already accepted parent is met at once and does not block.
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 5));
    assert!(child.status() == board::status_rekey() && child.deps_met_count() == 2);
    brief(&board, &mut child);
    assert!(child.status() == board::status_open());
    drop_m(parent);
    drop_m(parent2);
    drop_m(child);
    done(clock, board, cap);
}

#[test]
fun partial_unlock_stays_blocked() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut p1 = open(&mut board, &clock, &mut ctx);
    let p2 = open(&mut board, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &p1, &mut cx(A, 3));
    board::add_dependency(&board, &mut child, &p2, &mut cx(A, 4));
    claim_as(&mut board, &mut p1, B, &clock);
    submit_as(&board, &mut p1, B, &clock);
    accept_as(&mut board, &mut p1, A, &clock);
    board::unlock(&board, &mut child, &p1, &mut cx(D, 5));
    assert!(child.status() == board::status_blocked() && child.deps_met_count() == 1);
    drop_m(p1);
    drop_m(p2);
    drop_m(child);
    done(clock, board, cap);
}

#[test]
fun cancelled_parent_then_remove_dependency() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut parent = open(&mut board, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 3));
    board::cancel(&mut board, &mut parent, &mut cx(A, 4));
    assert!(child.status() == board::status_blocked());
    board::remove_dependency(&board, &mut child, object::id(&parent), &mut cx(A, 5));
    assert!(child.status() == board::status_open() && child.deps().length() == 0);
    drop_m(parent);
    drop_m(child);
    done(clock, board, cap);
}

#[test]
fun remove_met_dependency_from_open_child() {
    let (mut ctx, clock, mut board, cap) = setup();
    let parent = accepted(&mut board, B, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 3));
    assert!(child.status() == board::status_open() && child.deps_met_count() == 1);
    board::remove_dependency(&board, &mut child, object::id(&parent), &mut cx(A, 4));
    assert!(child.status() == board::status_open() && child.deps_met_count() == 0);
    drop_m(parent);
    drop_m(child);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENotDependency)]
fun remove_unknown_dependency_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let parent = open(&mut board, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::remove_dependency(&board, &mut child, object::id(&parent), &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun remove_dependency_with_claim_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let parent = accepted(&mut board, B, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 3));
    claim_as(&mut board, &mut child, C, &clock);
    board::remove_dependency(&board, &mut child, object::id(&parent), &mut cx(A, 4));
    abort 0
}

#[test, expected_failure(abort_code = board::ECycle)]
fun dependency_on_later_mission_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut first = open(&mut board, &clock, &mut ctx);
    let second = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut first, &second, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EDuplicateDep)]
fun duplicate_dependency_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let parent = open(&mut board, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 3));
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 4));
    abort 0
}

#[test, expected_failure(abort_code = board::ETooManyDeps)]
fun ninth_dependency_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut parents = vector[];
    let mut i = 0u64;
    while (i < 9) {
        parents.push_back(open(&mut board, &clock, &mut ctx));
        i = i + 1;
    };
    let mut child = open(&mut board, &clock, &mut ctx);
    let mut j = 0u64;
    while (j < 9) {
        board::add_dependency(&board, &mut child, &parents[j], &mut cx(A, 100 + j));
        j = j + 1;
    };
    abort 0
}

#[test, expected_failure(abort_code = board::EWrongBoard)]
fun dependency_across_boards_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let (mut board2, _cap2) = other_board(s(b"b2"));
    let parent = open(&mut board2, &clock, &mut ctx);
    let _x = open(&mut board, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun dependency_on_claimed_child_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let parent = open(&mut board, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut child, B, &clock);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun dependency_by_other_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let parent = open(&mut board, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent, &mut cx(B, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EDepsUnmet)]
fun unlock_before_parent_accepted_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let parent = open(&mut board, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 3));
    board::unlock(&board, &mut child, &parent, &mut cx(D, 4));
    abort 0
}

#[test, expected_failure(abort_code = board::ENotDependency)]
fun unlock_unrelated_parent_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let parent = accepted(&mut board, B, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::unlock(&board, &mut child, &parent, &mut cx(D, 4));
    abort 0
}

#[test, expected_failure(abort_code = board::EAlreadyMet)]
fun unlock_twice_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let parent = accepted(&mut board, B, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 3));
    board::unlock(&board, &mut child, &parent, &mut cx(D, 4));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun unlock_cancelled_child_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut parent = open(&mut board, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 3));
    board::cancel(&mut board, &mut child, &mut cx(A, 4));
    claim_as(&mut board, &mut parent, B, &clock);
    submit_as(&board, &mut parent, B, &clock);
    accept_as(&mut board, &mut parent, A, &clock);
    board::unlock(&board, &mut child, &parent, &mut cx(D, 5));
    abort 0
}

// === Claims ===

#[test]
fun claim_sets_lock_and_counters() {
    let (mut ctx, mut clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    clock.set_for_testing(5_000);
    board::claim(&mut board, &mut m, option::some(E), &clock, &mut cx(B, 3));
    assert!(m.status() == board::status_claimed());
    assert!(m.claimer() == B && m.claim_epoch() == 0 && m.claim_operator() == option::some(E));
    assert!(m.claim_deadline_ms() == 5_000 + H72 && m.attempts() == 1);
    let (acc, rej, exp, rel, active) = rep(&board, B);
    assert!(acc == 0 && rej == 0 && exp == 0 && rel == 0 && active == 1);
    assert!(board.active_claims(B) == 1 && board.active_claims(C) == 0);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ELeadCannotClaim)]
fun lead_cannot_claim_q5() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, A, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::EAlreadyClaimed)]
fun second_claimer_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    claim_as(&mut board, &mut m, C, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::EAlreadyClaimed)]
fun claim_while_submitted_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    claim_as(&mut board, &mut m, C, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun claim_in_rekey_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = post(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun claim_blocked_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let parent = open(&mut board, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 3));
    claim_as(&mut board, &mut child, B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun claim_cancelled_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    board::cancel(&mut board, &mut m, &mut cx(A, 3));
    claim_as(&mut board, &mut m, B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun claim_accepted_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = accepted(&mut board, B, &clock, &mut ctx);
    claim_as(&mut board, &mut m, C, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun claim_after_expiry_needs_rekey() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    clock.set_for_testing(T0 + H72);
    board::expire(&mut board, &mut m, &clock, &mut cx(D, 3));
    claim_as(&mut board, &mut m, C, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ETooManyClaims)]
fun third_active_claim_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m1 = open(&mut board, &clock, &mut ctx);
    let mut m2 = open(&mut board, &clock, &mut ctx);
    let mut m3 = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m1, B, &clock);
    claim_as(&mut board, &mut m2, B, &clock);
    claim_as(&mut board, &mut m3, B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ELowReputation)]
fun mission_min_reputation_enforced() {
    let (_ctx, clock, mut board, _cap) = setup();
    let mut m = mk(&mut board, s(b"t"), vector[], 0, 1, &clock);
    brief(&board, &mut m);
    claim_as(&mut board, &mut m, B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ELowReputation)]
fun board_min_reputation_enforced() {
    let (mut ctx, clock, mut board, cap) = setup();
    board::set_params(&mut board, &cap, board::new_params(H72, D14, D7, 2, H24, 1), &mut cx(A, 2));
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    abort 0
}

#[test]
fun reputation_unlocks_gated_mission() {
    let (mut ctx, clock, mut board, cap) = setup();
    let first = accepted(&mut board, B, &clock, &mut ctx);
    let mut gated = mk(&mut board, s(b"t"), vector[], 0, 1, &clock);
    brief(&board, &mut gated);
    claim_as(&mut board, &mut gated, B, &clock);
    assert!(gated.claimer() == B);
    drop_m(first);
    drop_m(gated);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ECooldown)]
fun expired_claimer_cooldown_enforced() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    clock.set_for_testing(T0 + H72);
    board::expire(&mut board, &mut m, &clock, &mut cx(D, 3));
    brief(&board, &mut m);
    clock.set_for_testing(T0 + H72 + H24 - 1);
    claim_as(&mut board, &mut m, B, &clock);
    abort 0
}

#[test]
fun cooldown_spares_others_and_ends() {
    let (mut ctx, mut clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    clock.set_for_testing(T0 + H72);
    board::expire(&mut board, &mut m, &clock, &mut cx(D, 3));
    brief(&board, &mut m);
    // C is not subject to B's cooldown.
    claim_as(&mut board, &mut m, C, &clock);
    board::release(&mut board, &mut m, &mut cx(C, 4));
    brief(&board, &mut m);
    clock.set_for_testing(T0 + H72 + H24);
    claim_as(&mut board, &mut m, B, &clock);
    assert!(m.claimer() == B && m.claim_epoch() == 2 && m.attempts() == 3);
    drop_m(m);
    done(clock, board, cap);
}

// === Notes, submission, extension ===

#[test]
fun claimer_adds_notes() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    let n = blob(&m, 0, board::kind_notes());
    board::add_note(&board, &mut m, n, &clock, &mut cx(B, 3));
    assert!(m.note_count() == 1);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ETooManyNotes)]
fun seventeenth_note_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    let mut i = 0u64;
    while (i < 17) {
        let n = blob(&m, 0, board::kind_notes());
        board::add_note(&board, &mut m, n, &clock, &mut cx(B, 3));
        i = i + 1;
    };
    abort 0
}

#[test, expected_failure(abort_code = board::ENotClaimer)]
fun note_by_other_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    let n = blob(&m, 0, board::kind_notes());
    board::add_note(&board, &mut m, n, &clock, &mut cx(C, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EExpired)]
fun note_at_deadline_refused() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    clock.set_for_testing(T0 + H72);
    let n = blob(&m, 0, board::kind_notes());
    board::add_note(&board, &mut m, n, &clock, &mut cx(B, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadKeyId)]
fun note_with_output_kind_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    let n = blob(&m, 0, board::kind_output());
    board::add_note(&board, &mut m, n, &clock, &mut cx(B, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EStaleBrief)]
fun note_with_other_epoch_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    let n = blob(&m, 1, board::kind_notes());
    board::add_note(&board, &mut m, n, &clock, &mut cx(B, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun note_when_not_claimed_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    let n = blob(&m, 0, board::kind_notes());
    board::add_note(&board, &mut m, n, &clock, &mut cx(B, 3));
    abort 0
}

#[test]
fun submit_opens_review_window() {
    let (mut ctx, mut clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    clock.set_for_testing(T0 + H72 - 1);
    let out = blob(&m, 0, board::kind_output());
    board::submit(&board, &mut m, out, digest(), &clock, &mut cx(B, 3));
    assert!(m.status() == board::status_submitted());
    assert!(m.delivery_author() == B && m.review_deadline_ms() == T0 + H72 - 1 + D7);
    assert!(m.rejections() == 0 && !m.is_overdue() && m.accepted_by().is_none());
    assert!(board.active_claims(B) == 1);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::EExpired)]
fun submit_at_deadline_refused() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    clock.set_for_testing(T0 + H72);
    submit_as(&board, &mut m, B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENotClaimer)]
fun submit_by_other_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, C, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::EBadDigest)]
fun submit_bad_summary_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    let out = blob(&m, 0, board::kind_output());
    board::submit(&board, &mut m, out, b"abc", &clock, &mut cx(B, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadKeyId)]
fun submit_with_brief_kind_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    let out = blob(&m, 0, board::kind_brief());
    board::submit(&board, &mut m, out, vector[], &clock, &mut cx(B, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun submit_twice_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    abort 0
}

#[test]
fun lead_extends_up_to_max() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::extend(&board, &mut m, T0 + D14, &clock, &mut cx(A, 3));
    assert!(m.claim_deadline_ms() == T0 + D14);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ETtlTooLong)]
fun extend_beyond_max_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::extend(&board, &mut m, T0 + D14 + 1, &clock, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadDeadline)]
fun extend_not_later_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::extend(&board, &mut m, T0 + H72, &clock, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EExpired)]
fun extend_after_deadline_refused() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    clock.set_for_testing(T0 + H72);
    board::extend(&board, &mut m, T0 + H72 + 1, &clock, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun extend_by_other_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::extend(&board, &mut m, T0 + H72 + 1, &clock, &mut cx(B, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun extend_unclaimed_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    board::extend(&board, &mut m, T0 + H72, &clock, &mut cx(A, 3));
    abort 0
}

// === End of claim: release, expire, revoke, cancel ===

#[test]
fun release_rotates_epoch_and_counts() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::release(&mut board, &mut m, &mut cx(B, 3));
    assert!(m.status() == board::status_rekey() && m.key_epoch() == 1 && !m.has_claim());
    let (_, _, exp, rel, active) = rep(&board, B);
    assert!(rel == 1 && exp == 0 && active == 0);
    brief(&board, &mut m);
    assert!(m.status() == board::status_open());
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENotClaimer)]
fun release_by_other_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::release(&mut board, &mut m, &mut cx(C, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun release_when_submitted_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    board::release(&mut board, &mut m, &mut cx(B, 3));
    abort 0
}

#[test]
fun anyone_expires_at_deadline() {
    let (mut ctx, mut clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    clock.set_for_testing(T0 + H72);
    board::expire(&mut board, &mut m, &clock, &mut cx(D, 3));
    assert!(m.status() == board::status_rekey() && m.key_epoch() == 1);
    let (_, _, exp, rel, active) = rep(&board, B);
    assert!(exp == 1 && rel == 0 && active == 0);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENotExpired)]
fun expire_before_deadline_refused() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    clock.set_for_testing(T0 + H72 - 1);
    board::expire(&mut board, &mut m, &clock, &mut cx(D, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun expire_unclaimed_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    board::expire(&mut board, &mut m, &clock, &mut cx(D, 3));
    abort 0
}

#[test]
fun revoke_is_not_counted_against_contributor() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::revoke(&mut board, &mut m, &mut cx(A, 3));
    assert!(m.status() == board::status_rekey() && m.key_epoch() == 1);
    let (acc, rej, exp, rel, active) = rep(&board, B);
    assert!(acc == 0 && rej == 0 && exp == 0 && rel == 0 && active == 0);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun revoke_by_other_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::revoke(&mut board, &mut m, &mut cx(C, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun revoke_unclaimed_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    board::revoke(&mut board, &mut m, &mut cx(A, 3));
    abort 0
}

#[test]
fun cancel_open_rekey_blocked_and_claimed() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut open_m = open(&mut board, &clock, &mut ctx);
    let mut rekey_m = post(&mut board, &clock, &mut ctx);
    let mut blocked_m = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut blocked_m, &open_m, &mut cx(A, 3));
    let mut claimed_m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut claimed_m, B, &clock);
    board::cancel(&mut board, &mut open_m, &mut cx(A, 4));
    board::cancel(&mut board, &mut rekey_m, &mut cx(A, 5));
    board::cancel(&mut board, &mut blocked_m, &mut cx(A, 6));
    board::cancel(&mut board, &mut claimed_m, &mut cx(A, 7));
    assert!(open_m.status() == board::status_cancelled());
    assert!(rekey_m.status() == board::status_cancelled());
    assert!(blocked_m.status() == board::status_cancelled());
    assert!(claimed_m.status() == board::status_cancelled() && claimed_m.key_epoch() == 1);
    let (acc, rej, exp, rel, active) = rep(&board, B);
    assert!(acc == 0 && rej == 0 && exp == 0 && rel == 0 && active == 0);
    drop_m(open_m);
    drop_m(rekey_m);
    drop_m(blocked_m);
    drop_m(claimed_m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun cancel_submitted_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    board::cancel(&mut board, &mut m, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun cancel_accepted_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = accepted(&mut board, B, &clock, &mut ctx);
    board::cancel(&mut board, &mut m, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun cancel_twice_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    board::cancel(&mut board, &mut m, &mut cx(A, 3));
    board::cancel(&mut board, &mut m, &mut cx(A, 4));
    abort 0
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun cancel_by_other_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    board::cancel(&mut board, &mut m, &mut cx(B, 3));
    abort 0
}

// === Review: accept, reject, late review ===

#[test]
fun accept_counts_and_frees_slot() {
    let (mut ctx, clock, mut board, cap) = setup();
    let m = accepted(&mut board, B, &clock, &mut ctx);
    assert!(m.status() == board::status_accepted() && m.accepted_by() == option::some(A));
    let (acc, rej, exp, rel, active) = rep(&board, B);
    assert!(acc == 1 && rej == 0 && exp == 0 && rel == 0 && active == 0);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun accept_by_other_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    accept_as(&mut board, &mut m, B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun accept_unsubmitted_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    accept_as(&mut board, &mut m, A, &clock);
    abort 0
}

#[test]
fun reject_rework_keeps_epoch_then_accept() {
    let (mut ctx, mut clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    clock.set_for_testing(T0 + 10);
    board::reject(&mut board, &mut m, true, &clock, &mut cx(A, 3));
    assert!(m.status() == board::status_claimed() && m.key_epoch() == 0);
    assert!(m.claimer() == B && m.claim_deadline_ms() == T0 + 10 + H72 && m.rejections() == 1);
    assert!(board.active_claims(B) == 1);
    submit_as(&board, &mut m, B, &clock);
    assert!(m.rejections() == 1);
    accept_as(&mut board, &mut m, A, &clock);
    let (acc, rej, _, _, active) = rep(&board, B);
    assert!(acc == 1 && rej == 0 && active == 0);
    drop_m(m);
    done(clock, board, cap);
}

#[test]
fun reject_reopen_rotates_and_counts() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    board::reject(&mut board, &mut m, false, &clock, &mut cx(A, 3));
    assert!(m.status() == board::status_rekey() && m.key_epoch() == 1);
    assert!(!m.has_claim() && !m.has_delivery());
    let (acc, rej, _, _, active) = rep(&board, B);
    assert!(acc == 0 && rej == 1 && active == 0);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun reject_by_other_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    board::reject(&mut board, &mut m, true, &clock, &mut cx(C, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun reject_unsubmitted_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::reject(&mut board, &mut m, false, &clock, &mut cx(A, 3));
    abort 0
}

#[test]
fun late_review_flags_only_and_holds_slot() {
    let (mut ctx, mut clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    let mut other = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    clock.set_for_testing(T0 + D7);
    board::expire_review(&board, &mut m, &clock, &mut cx(D, 3));
    // Decision Q3: nothing automatic. Still Submitted, flagged, slot still held.
    assert!(m.status() == board::status_submitted() && m.is_overdue());
    assert!(board.active_claims(B) == 1);
    claim_as(&mut board, &mut other, B, &clock);
    assert!(board.active_claims(B) == 2);
    // The lead can still decide; rework clears the flag.
    board::reject(&mut board, &mut m, true, &clock, &mut cx(A, 4));
    assert!(!m.is_overdue() && m.status() == board::status_claimed());
    drop_m(m);
    drop_m(other);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ETooManyClaims)]
fun late_review_slot_still_counts() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    let mut m2 = open(&mut board, &clock, &mut ctx);
    let mut m3 = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    clock.set_for_testing(T0 + D7);
    board::expire_review(&board, &mut m, &clock, &mut cx(D, 3));
    claim_as(&mut board, &mut m2, B, &clock);
    claim_as(&mut board, &mut m3, B, &clock);
    abort 0
}

#[test]
fun overdue_delivery_can_still_be_accepted() {
    let (mut ctx, mut clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    clock.set_for_testing(T0 + D7 + 5);
    board::expire_review(&board, &mut m, &clock, &mut cx(D, 3));
    accept_as(&mut board, &mut m, A, &clock);
    assert!(m.status() == board::status_accepted() && board.active_claims(B) == 0);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun expire_review_twice_refused() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    clock.set_for_testing(T0 + D7);
    board::expire_review(&board, &mut m, &clock, &mut cx(D, 3));
    board::expire_review(&board, &mut m, &clock, &mut cx(D, 4));
    abort 0
}

#[test, expected_failure(abort_code = board::ENotExpired)]
fun expire_review_early_refused() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    clock.set_for_testing(T0 + D7 - 1);
    board::expire_review(&board, &mut m, &clock, &mut cx(D, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun expire_review_unsubmitted_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::expire_review(&board, &mut m, &clock, &mut cx(D, 3));
    abort 0
}

// === seal_approve (§6.2) ===

#[test]
fun seal_lead_reads_every_kind_and_past_epoch() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::release(&mut board, &mut m, &mut cx(B, 3));
    approve(&board, &m, 0, board::kind_brief(), A, &clock);
    approve(&board, &m, 0, board::kind_notes(), A, &clock);
    approve(&board, &m, 0, board::kind_output(), A, &clock);
    approve(&board, &m, 1, board::kind_brief(), A, &clock);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_future_epoch_refused_even_for_lead() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let m = open(&mut board, &clock, &mut ctx);
    approve(&board, &m, 1, board::kind_brief(), A, &clock);
    abort 0
}

#[test]
fun seal_active_claimer_until_deadline_minus_one() {
    let (mut ctx, mut clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    approve(&board, &m, 0, board::kind_brief(), B, &clock);
    approve(&board, &m, 0, board::kind_notes(), B, &clock);
    clock.set_for_testing(T0 + H72 - 1);
    approve(&board, &m, 0, board::kind_brief(), B, &clock);
    approve(&board, &m, 0, board::kind_notes(), B, &clock);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_claimer_brief_refused_at_deadline_before_expire() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    clock.set_for_testing(T0 + H72);
    approve(&board, &m, 0, board::kind_brief(), B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_claimer_notes_refused_at_deadline() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    clock.set_for_testing(T0 + H72);
    approve(&board, &m, 0, board::kind_notes(), B, &clock);
    abort 0
}

#[test]
fun seal_submitted_claimer_keeps_brief_and_own_output() {
    let (mut ctx, mut clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    // While in review, even past the claim deadline.
    clock.set_for_testing(T0 + H72 + 1);
    approve(&board, &m, 0, board::kind_brief(), B, &clock);
    approve(&board, &m, 0, board::kind_notes(), B, &clock);
    approve(&board, &m, 0, board::kind_output(), B, &clock);
    accept_as(&mut board, &mut m, A, &clock);
    approve(&board, &m, 0, board::kind_output(), B, &clock);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_brief_refused_after_acceptance() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let m = accepted(&mut board, B, &clock, &mut ctx);
    approve(&board, &m, 0, board::kind_brief(), B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_output_refused_before_submit() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    approve(&board, &m, 0, board::kind_output(), B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_output_refused_to_non_author() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let m = accepted(&mut board, B, &clock, &mut ctx);
    approve(&board, &m, 0, board::kind_output(), C, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_output_other_epoch_refused_to_author() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, C, &clock);
    board::release(&mut board, &mut m, &mut cx(C, 3));
    brief(&board, &mut m);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    approve(&board, &m, 1, board::kind_output(), B, &clock);
    approve(&board, &m, 0, board::kind_output(), B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_former_claimer_refused_old_epoch() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::release(&mut board, &mut m, &mut cx(B, 3));
    approve(&board, &m, 0, board::kind_brief(), B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_former_claimer_refused_new_epoch() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    clock.set_for_testing(T0 + H72);
    board::expire(&mut board, &mut m, &clock, &mut cx(D, 3));
    brief(&board, &mut m);
    approve(&board, &m, 1, board::kind_brief(), B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_next_claimer_never_reads_old_notes_q10() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::release(&mut board, &mut m, &mut cx(B, 3));
    brief(&board, &mut m);
    claim_as(&mut board, &mut m, C, &clock);
    approve(&board, &m, 1, board::kind_brief(), C, &clock);
    // Previous claimer's notes are under epoch 0: never visible to the next claimer.
    approve(&board, &m, 0, board::kind_notes(), C, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_third_party_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    approve(&board, &m, 0, board::kind_brief(), D, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_open_mission_refused_to_contributor() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let m = open(&mut board, &clock, &mut ctx);
    approve(&board, &m, 0, board::kind_brief(), B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_bad_kind_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let m = open(&mut board, &clock, &mut ctx);
    approve(&board, &m, 0, 3, A, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_other_mission_prefix_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    let other = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    let id = kid(&other, 0, board::kind_brief());
    board::seal_approve_for_testing(id, &board, &m, &clock, &mut cx(B, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_short_id_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let m = open(&mut board, &clock, &mut ctx);
    board::seal_approve_for_testing(object::id(&m).to_bytes(), &board, &m, &clock, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_mission_of_other_board_refused() {
    let (mut ctx, clock, board, _cap) = setup();
    let (mut board2, _cap2) = other_board(s(b"b2"));
    let m = open(&mut board2, &clock, &mut ctx);
    approve(&board, &m, 0, board::kind_brief(), A, &clock);
    abort 0
}

// === seal_approve_parent (§7) ===

/// Parent accepted (author B), child unlocked and claimed by C.
fun parent_child(board: &mut Board, clock: &Clock, ctx: &mut TxContext): (Mission, Mission) {
    let mut parent = open(board, clock, ctx);
    let mut child = open(board, clock, ctx);
    board::add_dependency(board, &mut child, &parent, &mut cx(board.lead(), 80));
    claim_as(board, &mut parent, B, clock);
    submit_as(board, &mut parent, B, clock);
    let lead = board.lead();
    accept_as(board, &mut parent, lead, clock);
    board::unlock(board, &mut child, &parent, &mut cx(D, 81));
    claim_as(board, &mut child, C, clock);
    (parent, child)
}

fun approve_parent(board: &Board, parent: &Mission, child: &Mission, who: address, clock: &Clock) {
    let id = kid(parent, 0, board::kind_output());
    board::seal_approve_parent_for_testing(id, board, parent, child, clock, &mut cx(who, 82));
}

#[test]
fun seal_parent_output_for_child_claimer_and_lead() {
    let (mut ctx, clock, mut board, cap) = setup();
    let (parent, mut child) = parent_child(&mut board, &clock, &mut ctx);
    approve_parent(&board, &parent, &child, C, &clock);
    approve_parent(&board, &parent, &child, A, &clock);
    submit_as(&board, &mut child, C, &clock);
    approve_parent(&board, &parent, &child, C, &clock);
    drop_m(parent);
    drop_m(child);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_parent_refused_to_third_party() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let (parent, child) = parent_child(&mut board, &clock, &mut ctx);
    approve_parent(&board, &parent, &child, D, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_parent_refused_when_child_claim_expired() {
    let (mut ctx, mut clock, mut board, _cap) = setup();
    let (parent, child) = parent_child(&mut board, &clock, &mut ctx);
    approve_parent(&board, &parent, &child, C, &clock);
    clock.set_for_testing(T0 + H72);
    approve_parent(&board, &parent, &child, C, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_parent_not_accepted_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let mut parent = open(&mut board, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut parent, B, &clock);
    submit_as(&board, &mut parent, B, &clock);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 3));
    approve_parent(&board, &parent, &child, A, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_parent_unlinked_child_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let parent = accepted(&mut board, B, &clock, &mut ctx);
    let mut child = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut child, C, &clock);
    approve_parent(&board, &parent, &child, C, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_grandparent_refused_q7() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let grand = accepted(&mut board, B, &clock, &mut ctx);
    let mut parent = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut parent, &grand, &mut cx(A, 3));
    claim_as(&mut board, &mut parent, B, &clock);
    submit_as(&board, &mut parent, B, &clock);
    accept_as(&mut board, &mut parent, A, &clock);
    let mut child = open(&mut board, &clock, &mut ctx);
    board::add_dependency(&board, &mut child, &parent, &mut cx(A, 4));
    claim_as(&mut board, &mut child, C, &clock);
    // The direct parent is granted; the grandparent is not.
    approve_parent(&board, &parent, &child, C, &clock);
    approve_parent(&board, &grand, &child, C, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_parent_wrong_key_id_refused() {
    let (mut ctx, clock, mut board, _cap) = setup();
    let (parent, child) = parent_child(&mut board, &clock, &mut ctx);
    let id = kid(&parent, 0, board::kind_brief());
    board::seal_approve_parent_for_testing(id, &board, &parent, &child, &clock, &mut cx(C, 3));
    abort 0
}

// === Project transfer (§4.5) ===

fun transfer_to(board: &mut Board, cap: &BoardAdminCap, to: address) {
    let lead = board.lead();
    board::propose_transfer(board, cap, to, &mut cx(lead, 90));
    let mut c = cx(to, tag(object::id(board)) + 1023);
    board::accept_transfer(board, &mut c);
}

#[test]
fun transfer_moves_role_and_invalidates_cap() {
    let (_ctx, clock, mut board, cap) = setup();
    let old_cap_id = board.admin_cap_id();
    transfer_to(&mut board, &cap, B);
    assert!(board.lead() == B && board.creator() == A && board.pending_lead().is_none());
    assert!(board.admin_cap_id() != old_cap_id);
    done(clock, board, cap);
}

#[test]
fun cancel_transfer_clears_pending() {
    let (_ctx, clock, mut board, cap) = setup();
    board::propose_transfer(&mut board, &cap, B, &mut cx(A, 3));
    board::cancel_transfer(&mut board, &cap, &mut cx(A, 4));
    assert!(board.pending_lead().is_none() && board.lead() == A);
    board::propose_transfer(&mut board, &cap, C, &mut cx(A, 5));
    assert!(board.pending_lead() == option::some(C));
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ETransferPending)]
fun second_proposal_refused() {
    let (_ctx, _clock, mut board, cap) = setup();
    board::propose_transfer(&mut board, &cap, B, &mut cx(A, 3));
    board::propose_transfer(&mut board, &cap, C, &mut cx(A, 4));
    abort 0
}

#[test, expected_failure(abort_code = board::ESelfTransfer)]
fun self_transfer_refused() {
    let (_ctx, _clock, mut board, cap) = setup();
    board::propose_transfer(&mut board, &cap, A, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun propose_by_other_refused() {
    let (_ctx, _clock, mut board, cap) = setup();
    board::propose_transfer(&mut board, &cap, C, &mut cx(B, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::ERecipientHasClaim)]
fun propose_to_active_claimer_refused() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::propose_transfer(&mut board, &cap, B, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::ERecipientHasClaim)]
fun propose_to_author_in_review_refused() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    submit_as(&board, &mut m, B, &clock);
    board::propose_transfer(&mut board, &cap, B, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::ERecipientHasClaim)]
fun accept_after_claiming_refused() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    board::propose_transfer(&mut board, &cap, B, &mut cx(A, 3));
    claim_as(&mut board, &mut m, B, &clock);
    let mut c = cx(B, 4);
    board::accept_transfer(&mut board, &mut c);
    abort 0
}

#[test]
fun accept_after_claim_finished_allowed() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, B, &clock);
    board::release(&mut board, &mut m, &mut cx(B, 3));
    transfer_to(&mut board, &cap, B);
    assert!(board.lead() == B);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENotPendingLead)]
fun accept_transfer_by_other_refused() {
    let (_ctx, _clock, mut board, cap) = setup();
    board::propose_transfer(&mut board, &cap, B, &mut cx(A, 3));
    let mut c = cx(C, 4);
    board::accept_transfer(&mut board, &mut c);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoPendingTransfer)]
fun accept_without_proposal_refused() {
    let (_ctx, _clock, mut board, _cap) = setup();
    let mut c = cx(B, 4);
    board::accept_transfer(&mut board, &mut c);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoPendingTransfer)]
fun accept_after_cancel_refused() {
    let (_ctx, _clock, mut board, cap) = setup();
    board::propose_transfer(&mut board, &cap, B, &mut cx(A, 3));
    board::cancel_transfer(&mut board, &cap, &mut cx(A, 4));
    let mut c = cx(B, 5);
    board::accept_transfer(&mut board, &mut c);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoPendingTransfer)]
fun cancel_without_proposal_refused() {
    let (_ctx, _clock, mut board, cap) = setup();
    board::cancel_transfer(&mut board, &cap, &mut cx(A, 3));
    abort 0
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun cancel_by_successor_refused() {
    let (_ctx, _clock, mut board, cap) = setup();
    board::propose_transfer(&mut board, &cap, B, &mut cx(A, 3));
    board::cancel_transfer(&mut board, &cap, &mut cx(B, 4));
    abort 0
}

#[test, expected_failure(abort_code = board::EStaleCap)]
fun old_cap_refused_after_transfer() {
    let (_ctx, _clock, mut board, cap) = setup();
    transfer_to(&mut board, &cap, B);
    // Even in the hands of the new lead, the invalidated cap is useless.
    board::freeze_board(&mut board, &cap, &mut cx(B, 4));
    abort 0
}

#[test, expected_failure(abort_code = board::EBadStatus)]
fun current_cap_cannot_be_destroyed() {
    let (_ctx, _clock, board, cap) = setup();
    board::destroy_stale_cap(&board, cap);
    abort 0
}

#[test, expected_failure(abort_code = board::EWrongBoard)]
fun stale_cap_of_other_board_refused() {
    let (_ctx, _clock, board, _cap) = setup();
    let (_board2, cap2) = other_board(s(b"b2"));
    board::destroy_stale_cap(&board, cap2);
    abort 0
}

#[test]
fun transfer_allowed_on_frozen_board() {
    let (_ctx, clock, mut board, cap) = setup();
    board::freeze_board(&mut board, &cap, &mut cx(A, 2));
    transfer_to(&mut board, &cap, B);
    assert!(board.lead() == B && board.is_frozen());
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun former_lead_cannot_post() {
    let (_ctx, clock, mut board, cap) = setup();
    transfer_to(&mut board, &cap, B);
    let mut a = cx(A, 5);
    let _m = board::new_mission_for_testing(&mut board, s(b"t"), vector[], 0, 0, &clock, &mut a);
    abort 0
}

#[test, expected_failure(abort_code = board::ENotLead)]
fun former_lead_cannot_review() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, C, &clock);
    submit_as(&board, &mut m, C, &clock);
    transfer_to(&mut board, &cap, B);
    accept_as(&mut board, &mut m, A, &clock);
    abort 0
}

#[test]
fun new_lead_runs_board_and_former_lead_contributes() {
    let (mut ctx, clock, mut board, cap) = setup();
    // B earns reputation as a contributor, then becomes lead.
    let done_m = accepted(&mut board, B, &clock, &mut ctx);
    // A mission written by A, still to do.
    let mut by_a = open(&mut board, &clock, &mut ctx);
    transfer_to(&mut board, &cap, B);
    // Reputation stays with addresses, not with the role.
    let (acc, _, _, _, active) = rep(&board, B);
    assert!(acc == 1 && active == 0);
    let (acc_a0, _, _, _, _) = rep(&board, A);
    assert!(acc_a0 == 0);
    // The new lead posts.
    let m2 = mk(&mut board, s(b"t"), vector[], 0, 0, &clock);
    assert!(m2.created_by() == B);
    // The former lead claims, even a mission he wrote; the new lead reviews it.
    claim_as(&mut board, &mut by_a, A, &clock);
    submit_as(&board, &mut by_a, A, &clock);
    accept_as(&mut board, &mut by_a, B, &clock);
    let (acc_a, _, _, _, active_a) = rep(&board, A);
    assert!(acc_a == 1 && active_a == 0 && by_a.accepted_by() == option::some(B));
    // A delivery in review at transfer time is reviewed by the new lead.
    drop_m(done_m);
    drop_m(by_a);
    drop_m(m2);
    done(clock, board, cap);
}

#[test]
fun delivery_in_review_moves_to_new_lead() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    claim_as(&mut board, &mut m, C, &clock);
    submit_as(&board, &mut m, C, &clock);
    transfer_to(&mut board, &cap, B);
    accept_as(&mut board, &mut m, B, &clock);
    assert!(m.accepted_by() == option::some(B));
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ELeadCannotClaim)]
fun new_lead_cannot_claim() {
    let (mut ctx, clock, mut board, cap) = setup();
    let mut m = open(&mut board, &clock, &mut ctx);
    transfer_to(&mut board, &cap, B);
    claim_as(&mut board, &mut m, B, &clock);
    abort 0
}

#[test]
fun seal_follows_current_lead() {
    let (mut ctx, clock, mut board, cap) = setup();
    let m = open(&mut board, &clock, &mut ctx);
    transfer_to(&mut board, &cap, B);
    approve(&board, &m, 0, board::kind_brief(), B, &clock);
    approve(&board, &m, 0, board::kind_notes(), B, &clock);
    approve(&board, &m, 0, board::kind_output(), B, &clock);
    drop_m(m);
    done(clock, board, cap);
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_former_lead_refused_after_transfer() {
    let (mut ctx, clock, mut board, cap) = setup();
    let m = open(&mut board, &clock, &mut ctx);
    approve(&board, &m, 0, board::kind_brief(), A, &clock);
    transfer_to(&mut board, &cap, B);
    approve(&board, &m, 0, board::kind_brief(), A, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_pending_successor_has_no_access() {
    let (mut ctx, clock, mut board, cap) = setup();
    let m = open(&mut board, &clock, &mut ctx);
    board::propose_transfer(&mut board, &cap, B, &mut cx(A, 3));
    approve(&board, &m, 0, board::kind_brief(), B, &clock);
    abort 0
}

#[test, expected_failure(abort_code = board::ENoAccess)]
fun seal_parent_former_lead_refused() {
    let (mut ctx, clock, mut board, cap) = setup();
    let (parent, child) = parent_child(&mut board, &clock, &mut ctx);
    approve_parent(&board, &parent, &child, A, &clock);
    transfer_to(&mut board, &cap, D);
    approve_parent(&board, &parent, &child, D, &clock);
    approve_parent(&board, &parent, &child, A, &clock);
    abort 0
}
