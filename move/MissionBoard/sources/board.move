// SPDX-License-Identifier: MIT

/// Mnemos mission board. BETA (testnet preview), testnet only, not audited.
///
/// A `Board` is a shared object run by a single project lead (the maintainer, at first
/// the creator) who defines missions. Each `Mission` is its own shared object carrying
/// the claim lock that `seal_approve` reads:
///
/// - a contributor claims an open mission; the claim is exclusive and time-bounded by
///   the Sui clock (`0x6`);
/// - the brief, notes and output are Seal ciphertexts on Walrus whose inner key id is
///   `[mission id: 32][key_epoch: u64 LE][kind: u8][nonce]`;
/// - every end of a claim without acceptance bumps `key_epoch`, so the next claimer
///   works under an epoch the previous one never obtained keys for;
/// - an accepted mission unlocks its direct children, whose active claimer may read the
///   accepted output (`seal_approve_parent`);
/// - the lead may hand the project over in two steps (`propose_transfer`,
///   `accept_transfer`); a fresh `BoardAdminCap` is minted for the successor and the
///   old one is invalidated through `admin_cap_id`.
///
/// Seal never takes back a key already served: expiry, revocation or a lead transfer
/// cut access to future keys only. Design: docs/fr/design-mission-board.md.
module mnemos_board::board;

use std::string::String;
use sui::clock::Clock;
use sui::event;
use sui::table::{Self, Table};
use sui::vec_set::{Self, VecSet};

// === Errors ===

const EWrongBoard: u64 = 1;
const ENotLead: u64 = 2;
const ENotClaimer: u64 = 3;
const EBadStatus: u64 = 4;
const EAlreadyClaimed: u64 = 5;
const EDepsUnmet: u64 = 6;
const EStaleBrief: u64 = 7;
const EExpired: u64 = 8;
const ENotExpired: u64 = 9;
const ETooManyClaims: u64 = 10;
const ECooldown: u64 = 11;
const ELowReputation: u64 = 12;
const EBadKeyId: u64 = 13;
const EBadDigest: u64 = 14;
const ECycle: u64 = 15;
const ETooManyDeps: u64 = 16;
const EFrozen: u64 = 17;
const EWrongVersion: u64 = 18;
const ETtlTooLong: u64 = 19;
const EStaleCap: u64 = 20;
const ETransferPending: u64 = 21;
const ENoPendingTransfer: u64 = 22;
const ENotPendingLead: u64 = 23;
const ESelfTransfer: u64 = 24;
const ERecipientHasClaim: u64 = 25;
const ENoAccess: u64 = 26;
const ELeadCannotClaim: u64 = 27;
const ETooLong: u64 = 28;
const ETooManyNotes: u64 = 29;
const EBadParams: u64 = 30;
const EBadDeadline: u64 = 31;
const ENotDependency: u64 = 32;
const EAlreadyMet: u64 = 33;
const EDuplicateDep: u64 = 34;

// === Constants ===

const VERSION: u64 = 1;

const MAX_NAME_LEN: u64 = 64;
const MAX_TITLE_LEN: u64 = 128;
const MAX_LABELS: u64 = 8;
const MAX_LABEL_LEN: u64 = 32;
const MAX_DEPS: u64 = 8;
const MAX_NOTES: u64 = 16;

/// Inner key id: mission id (32) + epoch (8) + kind (1) + at least one nonce byte.
const KEY_PREFIX_LEN: u64 = 32;
const KEY_MIN_LEN: u64 = 42;

const STATUS_BLOCKED: u8 = 0;
const STATUS_REKEY: u8 = 1;
const STATUS_OPEN: u8 = 2;
const STATUS_CLAIMED: u8 = 3;
const STATUS_SUBMITTED: u8 = 4;
const STATUS_ACCEPTED: u8 = 5;
const STATUS_CANCELLED: u8 = 6;

const KIND_BRIEF: u8 = 0;
const KIND_NOTES: u8 = 1;
const KIND_OUTPUT: u8 = 2;

// Defaults (design v1, decision Q9).
const DEFAULT_CLAIM_TTL_MS: u64 = 259_200_000; // 72 h
const DEFAULT_MAX_CLAIM_TTL_MS: u64 = 1_209_600_000; // 14 days
const DEFAULT_REVIEW_WINDOW_MS: u64 = 604_800_000; // 7 days
const DEFAULT_MAX_ACTIVE_CLAIMS: u64 = 2;
const DEFAULT_RECLAIM_COOLDOWN_MS: u64 = 86_400_000; // 24 h
const DEFAULT_MIN_REPUTATION: u64 = 0;

// === Objects ===

/// Shared. Parameters, project lead, per-board reputation.
public struct Board has key {
    id: UID,
    version: u64,
    name: String,
    creator: address,
    lead: address,
    admin_cap_id: ID,
    pending_lead: Option<address>,
    next_seq: u64,
    frozen: bool,
    params: BoardParams,
    reputation: Table<address, RepStats>,
}

public struct BoardParams has copy, drop, store {
    default_claim_ttl_ms: u64,
    max_claim_ttl_ms: u64,
    review_window_ms: u64,
    max_active_claims: u64,
    reclaim_cooldown_ms: u64,
    min_reputation_to_claim: u64,
}

/// Project lead capability. `key` without `store`: only this module moves it, so the
/// holder of the valid cap and `Board.lead` cannot diverge.
public struct BoardAdminCap has key {
    id: UID,
    board_id: ID,
}

/// Shared, one per mission. Holds the claim lock read by `seal_approve`.
public struct Mission has key {
    id: UID,
    board_id: ID,
    seq: u64,
    title: String,
    labels: vector<String>,
    status: u8,
    key_epoch: u64,
    brief: Option<BlobRef>,
    deps: vector<ID>,
    deps_met: VecSet<ID>,
    claim: Option<Claim>,
    delivery: Option<Delivery>,
    claim_ttl_ms: u64,
    min_reputation: u64,
    attempts: u64,
    last_expired: Option<LastExpired>,
    created_by: address,
    created_ms: u64,
}

public struct BlobRef has copy, drop, store {
    blob_id: String,
    key_id: vector<u8>,
    blob_sha256: vector<u8>,
}

public struct Claim has copy, drop, store {
    claimer: address,
    operator: Option<address>,
    epoch: u64,
    started_ms: u64,
    deadline_ms: u64,
    notes: vector<BlobRef>,
}

public struct Delivery has copy, drop, store {
    author: address,
    output: BlobRef,
    summary_sha256: vector<u8>,
    submitted_ms: u64,
    review_deadline_ms: u64,
    accepted_by: Option<address>,
    accepted_ms: Option<u64>,
    rejections: u64,
    overdue: bool,
}

public struct LastExpired has copy, drop, store {
    claimer: address,
    at_ms: u64,
}

public struct RepStats has copy, drop, store {
    accepted: u64,
    rejected: u64,
    expired: u64,
    released: u64,
    active_claims: u64,
}

/// Non-transferable receipt: `key` without `store`, sent once to the author on
/// acceptance. The module offers no transfer and no delete function for it.
public struct DeliveryReceipt has key {
    id: UID,
    board_id: ID,
    mission_id: ID,
    mission_seq: u64,
    contributor: address,
    accepted_by: address,
    output_sha256: vector<u8>,
    accepted_ms: u64,
}

// === Events ===

public struct BoardCreated has copy, drop { board_id: ID, lead: address, name: String }

public struct BoardParamsChanged has copy, drop { board_id: ID, by: address }

public struct BoardFrozen has copy, drop { board_id: ID, by: address }

public struct TransferProposed has copy, drop { board_id: ID, from: address, to: address }

public struct TransferAccepted has copy, drop { board_id: ID, from: address, to: address }

public struct TransferCancelled has copy, drop { board_id: ID, from: address, to: address }

public struct MissionPosted has copy, drop { board_id: ID, mission_id: ID, seq: u64, by: address }

public struct MissionEdited has copy, drop { board_id: ID, mission_id: ID, seq: u64, by: address }

public struct DependencyAdded has copy, drop {
    board_id: ID,
    mission_id: ID,
    seq: u64,
    by: address,
    parent_id: ID,
}

public struct DependencyRemoved has copy, drop {
    board_id: ID,
    mission_id: ID,
    seq: u64,
    by: address,
    parent_id: ID,
}

public struct DependencyMet has copy, drop {
    board_id: ID,
    mission_id: ID,
    seq: u64,
    by: address,
    parent_id: ID,
}

public struct MissionUnblocked has copy, drop {
    board_id: ID,
    mission_id: ID,
    seq: u64,
    by: address,
}

public struct BriefPublished has copy, drop {
    board_id: ID,
    mission_id: ID,
    seq: u64,
    by: address,
    epoch: u64,
}

public struct MissionClaimed has copy, drop {
    board_id: ID,
    mission_id: ID,
    seq: u64,
    claimer: address,
    epoch: u64,
    deadline_ms: u64,
}

public struct ClaimExtended has copy, drop {
    board_id: ID,
    mission_id: ID,
    seq: u64,
    by: address,
    deadline_ms: u64,
}

public struct NoteAdded has copy, drop { board_id: ID, mission_id: ID, seq: u64, by: address }

public struct ClaimReleased has copy, drop { board_id: ID, mission_id: ID, seq: u64, by: address }

public struct ClaimExpired has copy, drop {
    board_id: ID,
    mission_id: ID,
    seq: u64,
    by: address,
    claimer: address,
}

public struct ClaimRevoked has copy, drop {
    board_id: ID,
    mission_id: ID,
    seq: u64,
    by: address,
    claimer: address,
}

public struct DeliverySubmitted has copy, drop {
    board_id: ID,
    mission_id: ID,
    seq: u64,
    by: address,
    output_sha256: vector<u8>,
}

public struct DeliveryAccepted has copy, drop {
    board_id: ID,
    mission_id: ID,
    seq: u64,
    by: address,
    author: address,
}

public struct DeliveryRejected has copy, drop {
    board_id: ID,
    mission_id: ID,
    seq: u64,
    by: address,
    back_to: u8,
}

public struct ReviewOverdue has copy, drop { board_id: ID, mission_id: ID, seq: u64, by: address }

public struct MissionCancelled has copy, drop { board_id: ID, mission_id: ID, seq: u64, by: address }

// === Parameters and references ===

/// Default parameters (design v1, decision Q9).
public fun default_params(): BoardParams {
    BoardParams {
        default_claim_ttl_ms: DEFAULT_CLAIM_TTL_MS,
        max_claim_ttl_ms: DEFAULT_MAX_CLAIM_TTL_MS,
        review_window_ms: DEFAULT_REVIEW_WINDOW_MS,
        max_active_claims: DEFAULT_MAX_ACTIVE_CLAIMS,
        reclaim_cooldown_ms: DEFAULT_RECLAIM_COOLDOWN_MS,
        min_reputation_to_claim: DEFAULT_MIN_REPUTATION,
    }
}

public fun new_params(
    default_claim_ttl_ms: u64,
    max_claim_ttl_ms: u64,
    review_window_ms: u64,
    max_active_claims: u64,
    reclaim_cooldown_ms: u64,
    min_reputation_to_claim: u64,
): BoardParams {
    assert!(default_claim_ttl_ms > 0, EBadParams);
    assert!(default_claim_ttl_ms <= max_claim_ttl_ms, EBadParams);
    assert!(review_window_ms > 0, EBadParams);
    assert!(max_active_claims > 0, EBadParams);
    BoardParams {
        default_claim_ttl_ms,
        max_claim_ttl_ms,
        review_window_ms,
        max_active_claims,
        reclaim_cooldown_ms,
        min_reputation_to_claim,
    }
}

/// Reference to a Seal ciphertext stored on Walrus. The digest must be 32 bytes.
public fun new_blob_ref(blob_id: String, key_id: vector<u8>, blob_sha256: vector<u8>): BlobRef {
    assert!(blob_sha256.length() == 32, EBadDigest);
    BlobRef { blob_id, key_id, blob_sha256 }
}

// === Board lifecycle ===

/// Creates and shares a board; the sender becomes its project lead and receives the
/// only valid `BoardAdminCap`. Returns the board id.
public fun create(name: String, params: BoardParams, ctx: &mut TxContext): ID {
    let (board, cap) = new_board(name, params, ctx);
    let board_id = object::id(&board);
    transfer::transfer(cap, ctx.sender());
    transfer::share_object(board);
    board_id
}

fun new_board(name: String, params: BoardParams, ctx: &mut TxContext): (Board, BoardAdminCap) {
    assert!(name.length() <= MAX_NAME_LEN, ETooLong);
    let id = object::new(ctx);
    let board_id = id.to_inner();
    let cap = BoardAdminCap { id: object::new(ctx), board_id };
    let board = Board {
        id,
        version: VERSION,
        name,
        creator: ctx.sender(),
        lead: ctx.sender(),
        admin_cap_id: object::id(&cap),
        pending_lead: option::none(),
        next_seq: 0,
        frozen: false,
        params,
        reputation: table::new(ctx),
    };
    event::emit(BoardCreated { board_id, lead: ctx.sender(), name });
    (board, cap)
}

public fun set_params(board: &mut Board, cap: &BoardAdminCap, params: BoardParams, ctx: &mut TxContext) {
    assert_admin(board, cap, ctx);
    board.params = params;
    event::emit(BoardParamsChanged { board_id: object::id(board), by: ctx.sender() });
}

/// One-way: no new missions and no new claims afterwards; everything else continues.
public fun freeze_board(board: &mut Board, cap: &BoardAdminCap, ctx: &mut TxContext) {
    assert_admin(board, cap, ctx);
    board.frozen = true;
    event::emit(BoardFrozen { board_id: object::id(board), by: ctx.sender() });
}

// === Project transfer (design §4.5) ===

public fun propose_transfer(board: &mut Board, cap: &BoardAdminCap, to: address, ctx: &mut TxContext) {
    assert_admin(board, cap, ctx);
    assert!(board.pending_lead.is_none(), ETransferPending);
    assert!(to != board.lead, ESelfTransfer);
    assert!(active_claims(board, to) == 0, ERecipientHasClaim);
    board.pending_lead = option::some(to);
    event::emit(TransferProposed { board_id: object::id(board), from: board.lead, to });
}

/// Called by the designated successor. Mints a fresh cap for them and invalidates the
/// previous one; the former lead becomes a normal participant.
public fun accept_transfer(board: &mut Board, ctx: &mut TxContext) {
    assert_version(board);
    assert!(board.pending_lead.is_some(), ENoPendingTransfer);
    let to = ctx.sender();
    assert!(*board.pending_lead.borrow() == to, ENotPendingLead);
    assert!(active_claims(board, to) == 0, ERecipientHasClaim);
    let from = board.lead;
    let board_id = object::id(board);
    let cap = BoardAdminCap { id: object::new(ctx), board_id };
    board.admin_cap_id = object::id(&cap);
    board.lead = to;
    board.pending_lead = option::none();
    transfer::transfer(cap, to);
    event::emit(TransferAccepted { board_id, from, to });
}

public fun cancel_transfer(board: &mut Board, cap: &BoardAdminCap, ctx: &mut TxContext) {
    assert_admin(board, cap, ctx);
    assert!(board.pending_lead.is_some(), ENoPendingTransfer);
    let to = board.pending_lead.extract();
    event::emit(TransferCancelled { board_id: object::id(board), from: board.lead, to });
}

/// Deletes a cap invalidated by a transfer. The current cap cannot be destroyed.
public fun destroy_stale_cap(board: &Board, cap: BoardAdminCap) {
    assert!(cap.board_id == object::id(board), EWrongBoard);
    assert!(object::id(&cap) != board.admin_cap_id, EBadStatus);
    let BoardAdminCap { id, board_id: _ } = cap;
    id.delete();
}

// === Missions: lead actions ===

/// Posts a mission in `Rekey`. Its brief is encrypted to the mission id, which only
/// exists once this transaction has run, so it is published by `publish_brief` in a
/// second transaction (together with any `add_dependency`). `ttl_ms == 0` uses the
/// board default.
public fun post(
    board: &mut Board,
    title: String,
    labels: vector<String>,
    ttl_ms: u64,
    min_reputation: u64,
    clock: &Clock,
    ctx: &mut TxContext,
): ID {
    let mission = new_mission(board, title, labels, ttl_ms, min_reputation, clock, ctx);
    let mission_id = object::id(&mission);
    transfer::share_object(mission);
    mission_id
}

fun new_mission(
    board: &mut Board,
    title: String,
    labels: vector<String>,
    ttl_ms: u64,
    min_reputation: u64,
    clock: &Clock,
    ctx: &mut TxContext,
): Mission {
    assert_lead(board, ctx);
    assert!(!board.frozen, EFrozen);
    check_public_text(&title, &labels);
    let claim_ttl_ms = resolve_ttl(board, ttl_ms);
    let seq = board.next_seq;
    board.next_seq = seq + 1;
    let mission = Mission {
        id: object::new(ctx),
        board_id: object::id(board),
        seq,
        title,
        labels,
        status: STATUS_REKEY,
        key_epoch: 0,
        brief: option::none(),
        deps: vector[],
        deps_met: vec_set::empty(),
        claim: option::none(),
        delivery: option::none(),
        claim_ttl_ms,
        min_reputation,
        attempts: 0,
        last_expired: option::none(),
        created_by: ctx.sender(),
        created_ms: clock.timestamp_ms(),
    };
    event::emit(MissionPosted {
        board_id: mission.board_id,
        mission_id: object::id(&mission),
        seq,
        by: ctx.sender(),
    });
    mission
}

public fun edit(
    board: &Board,
    mission: &mut Mission,
    title: Option<String>,
    labels: Option<vector<String>>,
    ttl_ms: Option<u64>,
    ctx: &mut TxContext,
) {
    assert_lead_of(board, mission, ctx);
    let s = mission.status;
    assert!(s == STATUS_BLOCKED || s == STATUS_REKEY || s == STATUS_OPEN, EBadStatus);
    if (title.is_some()) mission.title = title.destroy_some() else title.destroy_none();
    if (labels.is_some()) mission.labels = labels.destroy_some() else labels.destroy_none();
    check_public_text(&mission.title, &mission.labels);
    if (ttl_ms.is_some()) {
        let ttl = ttl_ms.destroy_some();
        assert!(ttl > 0, EBadParams);
        assert!(ttl <= board.params.max_claim_ttl_ms, ETtlTooLong);
        mission.claim_ttl_ms = ttl;
    } else ttl_ms.destroy_none();
    event::emit(MissionEdited {
        board_id: mission.board_id,
        mission_id: object::id(mission),
        seq: mission.seq,
        by: ctx.sender(),
    });
}

/// Publishes (or replaces) the encrypted brief at the current `key_epoch`.
public fun publish_brief(board: &Board, mission: &mut Mission, brief: BlobRef, ctx: &mut TxContext) {
    assert_lead_of(board, mission, ctx);
    let s = mission.status;
    assert!(s == STATUS_BLOCKED || s == STATUS_REKEY || s == STATUS_OPEN, EBadStatus);
    check_key_id(mission, &brief.key_id, mission.key_epoch, KIND_BRIEF);
    mission.brief = option::some(brief);
    if (s == STATUS_REKEY) settle(mission);
    event::emit(BriefPublished {
        board_id: mission.board_id,
        mission_id: object::id(mission),
        seq: mission.seq,
        by: ctx.sender(),
        epoch: mission.key_epoch,
    });
}

/// Declares `parent` as a dependency of `child`. Parents must have a lower sequence
/// number, which makes cycles impossible.
public fun add_dependency(board: &Board, child: &mut Mission, parent: &Mission, ctx: &mut TxContext) {
    assert_lead_of(board, child, ctx);
    assert!(parent.board_id == child.board_id, EWrongBoard);
    let s = child.status;
    assert!(s == STATUS_BLOCKED || s == STATUS_REKEY || s == STATUS_OPEN, EBadStatus);
    assert!(parent.seq < child.seq, ECycle);
    let parent_id = object::id(parent);
    assert!(!child.deps.contains(&parent_id), EDuplicateDep);
    assert!(child.deps.length() < MAX_DEPS, ETooManyDeps);
    child.deps.push_back(parent_id);
    let mission_id = object::id(child);
    event::emit(DependencyAdded {
        board_id: child.board_id,
        mission_id,
        seq: child.seq,
        by: ctx.sender(),
        parent_id,
    });
    if (parent.status == STATUS_ACCEPTED) {
        child.deps_met.insert(parent_id);
        event::emit(DependencyMet {
            board_id: child.board_id,
            mission_id,
            seq: child.seq,
            by: ctx.sender(),
            parent_id,
        });
    } else {
        child.status = STATUS_BLOCKED;
    }
}

public fun remove_dependency(board: &Board, child: &mut Mission, parent_id: ID, ctx: &mut TxContext) {
    assert_lead_of(board, child, ctx);
    let s = child.status;
    assert!(s == STATUS_BLOCKED || s == STATUS_REKEY || s == STATUS_OPEN, EBadStatus);
    let (found, i) = child.deps.index_of(&parent_id);
    assert!(found, ENotDependency);
    child.deps.remove(i);
    if (child.deps_met.contains(&parent_id)) child.deps_met.remove(&parent_id);
    event::emit(DependencyRemoved {
        board_id: child.board_id,
        mission_id: object::id(child),
        seq: child.seq,
        by: ctx.sender(),
        parent_id,
    });
    if (s == STATUS_BLOCKED) settle_and_announce(child, ctx.sender());
}

public fun extend(
    board: &Board,
    mission: &mut Mission,
    new_deadline_ms: u64,
    clock: &Clock,
    ctx: &mut TxContext,
) {
    assert_lead_of(board, mission, ctx);
    assert!(mission.status == STATUS_CLAIMED, EBadStatus);
    let claim = mission.claim.borrow_mut();
    assert!(clock.timestamp_ms() < claim.deadline_ms, EExpired);
    assert!(new_deadline_ms > claim.deadline_ms, EBadDeadline);
    assert!(new_deadline_ms - claim.started_ms <= board.params.max_claim_ttl_ms, ETtlTooLong);
    claim.deadline_ms = new_deadline_ms;
    event::emit(ClaimExtended {
        board_id: mission.board_id,
        mission_id: object::id(mission),
        seq: mission.seq,
        by: ctx.sender(),
        deadline_ms: new_deadline_ms,
    });
}

public fun revoke(board: &mut Board, mission: &mut Mission, ctx: &mut TxContext) {
    assert_lead_of(board, mission, ctx);
    assert!(mission.status == STATUS_CLAIMED, EBadStatus);
    let claimer = end_claim(board, mission);
    event::emit(ClaimRevoked {
        board_id: mission.board_id,
        mission_id: object::id(mission),
        seq: mission.seq,
        by: ctx.sender(),
        claimer,
    });
}

/// Accepts the delivery: terminal. Mints the author's non-transferable receipt.
public fun accept(board: &mut Board, mission: &mut Mission, clock: &Clock, ctx: &mut TxContext) {
    assert_lead_of(board, mission, ctx);
    assert!(mission.status == STATUS_SUBMITTED, EBadStatus);
    let now = clock.timestamp_ms();
    let lead = ctx.sender();
    let delivery = mission.delivery.borrow_mut();
    // Invariant: the lead never holds a claim on its own board (claim refuses the lead,
    // a transfer refuses a recipient with an active claim), so author != lead.
    delivery.accepted_by = option::some(lead);
    delivery.accepted_ms = option::some(now);
    let author = delivery.author;
    let output_sha256 = delivery.output.blob_sha256;
    mission.status = STATUS_ACCEPTED;
    let stats = stats_mut(board, author);
    stats.accepted = stats.accepted + 1;
    stats.active_claims = stats.active_claims - 1;
    let mission_id = object::id(mission);
    transfer::transfer(
        DeliveryReceipt {
            id: object::new(ctx),
            board_id: mission.board_id,
            mission_id,
            mission_seq: mission.seq,
            contributor: author,
            accepted_by: lead,
            output_sha256,
            accepted_ms: now,
        },
        author,
    );
    event::emit(DeliveryAccepted {
        board_id: mission.board_id,
        mission_id,
        seq: mission.seq,
        by: lead,
        author,
    });
}

/// `rework`: back to `Claimed` under the same epoch with a fresh deadline.
/// Otherwise the mission reopens: `key_epoch` + 1, `Rekey`.
public fun reject(
    board: &mut Board,
    mission: &mut Mission,
    rework: bool,
    clock: &Clock,
    ctx: &mut TxContext,
) {
    assert_lead_of(board, mission, ctx);
    assert!(mission.status == STATUS_SUBMITTED, EBadStatus);
    if (rework) {
        let now = clock.timestamp_ms();
        let ttl = mission.claim_ttl_ms;
        let claim = mission.claim.borrow_mut();
        claim.started_ms = now;
        claim.deadline_ms = now + ttl;
        let delivery = mission.delivery.borrow_mut();
        delivery.rejections = delivery.rejections + 1;
        delivery.overdue = false;
        mission.status = STATUS_CLAIMED;
    } else {
        let claimer = end_claim(board, mission);
        let stats = stats_mut(board, claimer);
        stats.rejected = stats.rejected + 1;
    };
    event::emit(DeliveryRejected {
        board_id: mission.board_id,
        mission_id: object::id(mission),
        seq: mission.seq,
        by: ctx.sender(),
        back_to: mission.status,
    });
}

public fun cancel(board: &mut Board, mission: &mut Mission, ctx: &mut TxContext) {
    assert_lead_of(board, mission, ctx);
    let s = mission.status;
    assert!(s != STATUS_ACCEPTED && s != STATUS_CANCELLED && s != STATUS_SUBMITTED, EBadStatus);
    if (s == STATUS_CLAIMED) {
        end_claim(board, mission);
    };
    mission.status = STATUS_CANCELLED;
    event::emit(MissionCancelled {
        board_id: mission.board_id,
        mission_id: object::id(mission),
        seq: mission.seq,
        by: ctx.sender(),
    });
}

// === Missions: contributor actions ===

public fun claim(
    board: &mut Board,
    mission: &mut Mission,
    operator: Option<address>,
    clock: &Clock,
    ctx: &mut TxContext,
) {
    assert_mission(board, mission);
    assert!(!board.frozen, EFrozen);
    let s = mission.status;
    assert!(s != STATUS_CLAIMED && s != STATUS_SUBMITTED, EAlreadyClaimed);
    assert!(s == STATUS_OPEN, EBadStatus);
    let sender = ctx.sender();
    assert!(sender != board.lead, ELeadCannotClaim);
    assert!(brief_fresh(mission), EStaleBrief);
    assert!(mission.deps_met.length() == mission.deps.length(), EDepsUnmet);
    let now = clock.timestamp_ms();
    if (mission.last_expired.is_some()) {
        let last = mission.last_expired.borrow();
        assert!(
            last.claimer != sender || now >= last.at_ms + board.params.reclaim_cooldown_ms,
            ECooldown,
        );
    };
    let min_rep = max(mission.min_reputation, board.params.min_reputation_to_claim);
    let max_claims = board.params.max_active_claims;
    let stats = stats_mut(board, sender);
    assert!(stats.active_claims < max_claims, ETooManyClaims);
    assert!(stats.accepted >= min_rep, ELowReputation);
    stats.active_claims = stats.active_claims + 1;
    let deadline_ms = now + mission.claim_ttl_ms;
    mission.claim =
        option::some(Claim {
            claimer: sender,
            operator,
            epoch: mission.key_epoch,
            started_ms: now,
            deadline_ms,
            notes: vector[],
        });
    mission.attempts = mission.attempts + 1;
    mission.status = STATUS_CLAIMED;
    event::emit(MissionClaimed {
        board_id: mission.board_id,
        mission_id: object::id(mission),
        seq: mission.seq,
        claimer: sender,
        epoch: mission.key_epoch,
        deadline_ms,
    });
}

public fun add_note(
    board: &Board,
    mission: &mut Mission,
    note: BlobRef,
    clock: &Clock,
    ctx: &mut TxContext,
) {
    assert_mission(board, mission);
    assert!(mission.status == STATUS_CLAIMED, EBadStatus);
    let epoch = assert_live_claimer(mission, clock, ctx);
    check_key_id(mission, &note.key_id, epoch, KIND_NOTES);
    let claim = mission.claim.borrow_mut();
    assert!(claim.notes.length() < MAX_NOTES, ETooManyNotes);
    claim.notes.push_back(note);
    event::emit(NoteAdded {
        board_id: mission.board_id,
        mission_id: object::id(mission),
        seq: mission.seq,
        by: ctx.sender(),
    });
}

/// `summary_sha256` is optional: empty, or 32 bytes.
public fun submit(
    board: &Board,
    mission: &mut Mission,
    output: BlobRef,
    summary_sha256: vector<u8>,
    clock: &Clock,
    ctx: &mut TxContext,
) {
    assert_mission(board, mission);
    assert!(mission.status == STATUS_CLAIMED, EBadStatus);
    let epoch = assert_live_claimer(mission, clock, ctx);
    check_key_id(mission, &output.key_id, epoch, KIND_OUTPUT);
    let len = summary_sha256.length();
    assert!(len == 0 || len == 32, EBadDigest);
    let now = clock.timestamp_ms();
    let output_sha256 = output.blob_sha256;
    let rejections = if (mission.delivery.is_some()) mission.delivery.borrow().rejections else 0;
    mission.delivery =
        option::some(Delivery {
            author: ctx.sender(),
            output,
            summary_sha256,
            submitted_ms: now,
            review_deadline_ms: now + board.params.review_window_ms,
            accepted_by: option::none(),
            accepted_ms: option::none(),
            rejections,
            overdue: false,
        });
    mission.status = STATUS_SUBMITTED;
    event::emit(DeliverySubmitted {
        board_id: mission.board_id,
        mission_id: object::id(mission),
        seq: mission.seq,
        by: ctx.sender(),
        output_sha256,
    });
}

public fun release(board: &mut Board, mission: &mut Mission, ctx: &mut TxContext) {
    assert_mission(board, mission);
    assert!(mission.status == STATUS_CLAIMED, EBadStatus);
    assert!(mission.claim.borrow().claimer == ctx.sender(), ENotClaimer);
    let claimer = end_claim(board, mission);
    let stats = stats_mut(board, claimer);
    stats.released = stats.released + 1;
    event::emit(ClaimReleased {
        board_id: mission.board_id,
        mission_id: object::id(mission),
        seq: mission.seq,
        by: claimer,
    });
}

// === Missions: permissionless ===

/// Records a passed deadline. Seal access already stopped at the deadline.
public fun expire(board: &mut Board, mission: &mut Mission, clock: &Clock, ctx: &mut TxContext) {
    assert_mission(board, mission);
    assert!(mission.status == STATUS_CLAIMED, EBadStatus);
    let now = clock.timestamp_ms();
    assert!(now >= mission.claim.borrow().deadline_ms, ENotExpired);
    let claimer = end_claim(board, mission);
    let stats = stats_mut(board, claimer);
    stats.expired = stats.expired + 1;
    mission.last_expired = option::some(LastExpired { claimer, at_ms: now });
    event::emit(ClaimExpired {
        board_id: mission.board_id,
        mission_id: object::id(mission),
        seq: mission.seq,
        by: ctx.sender(),
        claimer,
    });
}

/// Late review (decision Q3): flag and event only. The mission stays `Submitted` and
/// the contributor's claim slot stays held until the lead decides.
public fun expire_review(board: &Board, mission: &mut Mission, clock: &Clock, ctx: &mut TxContext) {
    assert_mission(board, mission);
    assert!(mission.status == STATUS_SUBMITTED, EBadStatus);
    let delivery = mission.delivery.borrow_mut();
    assert!(clock.timestamp_ms() >= delivery.review_deadline_ms, ENotExpired);
    assert!(!delivery.overdue, EBadStatus);
    delivery.overdue = true;
    event::emit(ReviewOverdue {
        board_id: mission.board_id,
        mission_id: object::id(mission),
        seq: mission.seq,
        by: ctx.sender(),
    });
}

/// Records that an accepted parent satisfies one dependency of `child`.
public fun unlock(board: &Board, child: &mut Mission, parent: &Mission, ctx: &mut TxContext) {
    assert_mission(board, child);
    assert!(parent.board_id == child.board_id, EWrongBoard);
    let parent_id = object::id(parent);
    assert!(child.deps.contains(&parent_id), ENotDependency);
    assert!(parent.status == STATUS_ACCEPTED, EDepsUnmet);
    assert!(!child.deps_met.contains(&parent_id), EAlreadyMet);
    assert!(child.status == STATUS_BLOCKED, EBadStatus);
    child.deps_met.insert(parent_id);
    event::emit(DependencyMet {
        board_id: child.board_id,
        mission_id: object::id(child),
        seq: child.seq,
        by: ctx.sender(),
        parent_id,
    });
    settle_and_announce(child, ctx.sender());
}

// === Seal policies ===

/// Seal access to a mission's brief, notes and output. Evaluated by the key servers in
/// a dry run; reads shared objects and the clock only.
entry fun seal_approve(id: vector<u8>, board: &Board, mission: &Mission, clock: &Clock, ctx: &TxContext) {
    assert!(can_access(&id, board, mission, clock, ctx.sender()), ENoAccess);
}

/// Context passing (§7): the active claimer of `child` (or the lead) may read the
/// accepted output of a direct parent.
entry fun seal_approve_parent(
    id: vector<u8>,
    board: &Board,
    parent: &Mission,
    child: &Mission,
    clock: &Clock,
    ctx: &TxContext,
) {
    assert!(can_access_parent(&id, board, parent, child, clock, ctx.sender()), ENoAccess);
}

fun can_access(id: &vector<u8>, board: &Board, mission: &Mission, clock: &Clock, s: address): bool {
    if (board.version != VERSION) return false;
    if (mission.board_id != object::id(board)) return false;
    if (!key_prefix_matches(id, object::id(mission))) return false;
    let epoch = key_epoch_of(id);
    let kind = key_kind_of(id);
    if (epoch > mission.key_epoch || kind > KIND_OUTPUT) return false;
    if (s == board.lead) return true;
    if (kind == KIND_OUTPUT) {
        if (mission.delivery.is_none()) return false;
        let delivery = mission.delivery.borrow();
        return delivery.author == s && epoch == key_epoch_of(&delivery.output.key_id)
    };
    if (mission.claim.is_none()) return false;
    let claim = mission.claim.borrow();
    if (claim.claimer != s || epoch != claim.epoch) return false;
    let live = mission.status == STATUS_CLAIMED && clock.timestamp_ms() < claim.deadline_ms;
    live || mission.status == STATUS_SUBMITTED
}

fun can_access_parent(
    id: &vector<u8>,
    board: &Board,
    parent: &Mission,
    child: &Mission,
    clock: &Clock,
    s: address,
): bool {
    if (board.version != VERSION) return false;
    let board_id = object::id(board);
    if (parent.board_id != board_id || child.board_id != board_id) return false;
    if (parent.status != STATUS_ACCEPTED) return false;
    if (!child.deps.contains(&object::id(parent))) return false;
    if (id.length() < KEY_MIN_LEN || key_kind_of(id) != KIND_OUTPUT) return false;
    if (*id != parent.delivery.borrow().output.key_id) return false;
    if (s == board.lead) return true;
    if (child.claim.is_none() || child.claim.borrow().claimer != s) return false;
    let live =
        child.status == STATUS_CLAIMED && clock.timestamp_ms() < child.claim.borrow().deadline_ms;
    live || child.status == STATUS_SUBMITTED
}

// === Internal helpers ===

fun assert_version(board: &Board) {
    assert!(board.version == VERSION, EWrongVersion);
}

fun assert_lead(board: &Board, ctx: &TxContext) {
    assert_version(board);
    assert!(ctx.sender() == board.lead, ENotLead);
}

fun assert_admin(board: &Board, cap: &BoardAdminCap, ctx: &TxContext) {
    assert_lead(board, ctx);
    assert!(cap.board_id == object::id(board), EWrongBoard);
    assert!(object::id(cap) == board.admin_cap_id, EStaleCap);
}

fun assert_mission(board: &Board, mission: &Mission) {
    assert_version(board);
    assert!(mission.board_id == object::id(board), EWrongBoard);
}

fun assert_lead_of(board: &Board, mission: &Mission, ctx: &TxContext) {
    assert_mission(board, mission);
    assert!(ctx.sender() == board.lead, ENotLead);
}

/// Sender is the claimer and the deadline has not passed; returns the claim epoch.
fun assert_live_claimer(mission: &Mission, clock: &Clock, ctx: &TxContext): u64 {
    let claim = mission.claim.borrow();
    assert!(claim.claimer == ctx.sender(), ENotClaimer);
    assert!(clock.timestamp_ms() < claim.deadline_ms, EExpired);
    claim.epoch
}

fun check_public_text(title: &String, labels: &vector<String>) {
    assert!(title.length() <= MAX_TITLE_LEN, ETooLong);
    assert!(labels.length() <= MAX_LABELS, ETooLong);
    labels.do_ref!(|l| assert!(l.length() <= MAX_LABEL_LEN, ETooLong));
}

fun resolve_ttl(board: &Board, ttl_ms: u64): u64 {
    if (ttl_ms == 0) return board.params.default_claim_ttl_ms;
    assert!(ttl_ms <= board.params.max_claim_ttl_ms, ETtlTooLong);
    ttl_ms
}

fun check_key_id(mission: &Mission, key_id: &vector<u8>, epoch: u64, kind: u8) {
    assert!(key_prefix_matches(key_id, object::id(mission)), EBadKeyId);
    assert!(key_epoch_of(key_id) == epoch, EStaleBrief);
    assert!(key_kind_of(key_id) == kind, EBadKeyId);
}

fun key_prefix_matches(id: &vector<u8>, mission_id: ID): bool {
    if (id.length() < KEY_MIN_LEN) return false;
    let bytes = mission_id.to_bytes();
    let mut i = 0;
    while (i < KEY_PREFIX_LEN) {
        if (id[i] != bytes[i]) return false;
        i = i + 1;
    };
    true
}

/// Little-endian u64 at bytes 32..40. Callers check the length first.
fun key_epoch_of(id: &vector<u8>): u64 {
    let mut epoch = 0u64;
    let mut i = 0;
    while (i < 8) {
        epoch = epoch | ((id[KEY_PREFIX_LEN + i] as u64) << ((8 * i) as u8));
        i = i + 1;
    };
    epoch
}

fun key_kind_of(id: &vector<u8>): u8 {
    id[KEY_PREFIX_LEN + 8]
}

fun brief_fresh(mission: &Mission): bool {
    mission.brief.is_some() && key_epoch_of(&mission.brief.borrow().key_id) == mission.key_epoch
}

/// Status of a mission without claim, from its dependencies and brief.
fun settle(mission: &mut Mission) {
    mission.status =
        if (mission.deps_met.length() != mission.deps.length()) STATUS_BLOCKED
        else if (brief_fresh(mission)) STATUS_OPEN
        else STATUS_REKEY;
}

fun settle_and_announce(mission: &mut Mission, by: address) {
    settle(mission);
    if (mission.status != STATUS_BLOCKED) {
        event::emit(MissionUnblocked {
            board_id: mission.board_id,
            mission_id: object::id(mission),
            seq: mission.seq,
            by,
        });
    }
}

/// Ends a claim without acceptance: frees the slot, rotates the epoch, drops any
/// delivery under the old epoch, back to `Rekey`. Returns the former claimer.
fun end_claim(board: &mut Board, mission: &mut Mission): address {
    let claim = mission.claim.extract();
    let stats = stats_mut(board, claim.claimer);
    stats.active_claims = stats.active_claims - 1;
    mission.key_epoch = mission.key_epoch + 1;
    mission.delivery = option::none();
    mission.status = STATUS_REKEY;
    claim.claimer
}

fun stats_mut(board: &mut Board, who: address): &mut RepStats {
    if (!board.reputation.contains(who)) {
        board
            .reputation
            .add(who, RepStats { accepted: 0, rejected: 0, expired: 0, released: 0, active_claims: 0 });
    };
    board.reputation.borrow_mut(who)
}

fun max(a: u64, b: u64): u64 { if (a > b) a else b }

// === Read accessors ===

public fun version(board: &Board): u64 { board.version }

public fun name(board: &Board): String { board.name }

public fun creator(board: &Board): address { board.creator }

public fun lead(board: &Board): address { board.lead }

public fun admin_cap_id(board: &Board): ID { board.admin_cap_id }

public fun pending_lead(board: &Board): Option<address> { board.pending_lead }

public fun next_seq(board: &Board): u64 { board.next_seq }

public fun is_frozen(board: &Board): bool { board.frozen }

public fun params(board: &Board): BoardParams { board.params }

public fun default_claim_ttl_ms(p: &BoardParams): u64 { p.default_claim_ttl_ms }

public fun max_claim_ttl_ms(p: &BoardParams): u64 { p.max_claim_ttl_ms }

public fun review_window_ms(p: &BoardParams): u64 { p.review_window_ms }

public fun max_active_claims(p: &BoardParams): u64 { p.max_active_claims }

public fun reclaim_cooldown_ms(p: &BoardParams): u64 { p.reclaim_cooldown_ms }

public fun min_reputation_to_claim(p: &BoardParams): u64 { p.min_reputation_to_claim }

/// (accepted, rejected, expired, released, active_claims); zeros for an unknown address.
public fun reputation(board: &Board, who: address): (u64, u64, u64, u64, u64) {
    if (!board.reputation.contains(who)) return (0, 0, 0, 0, 0);
    let s = board.reputation.borrow(who);
    (s.accepted, s.rejected, s.expired, s.released, s.active_claims)
}

public fun active_claims(board: &Board, who: address): u64 {
    if (!board.reputation.contains(who)) 0 else board.reputation.borrow(who).active_claims
}

public fun cap_board_id(cap: &BoardAdminCap): ID { cap.board_id }

public fun mission_board_id(m: &Mission): ID { m.board_id }

public fun seq(m: &Mission): u64 { m.seq }

public fun title(m: &Mission): String { m.title }

public fun labels(m: &Mission): vector<String> { m.labels }

public fun status(m: &Mission): u8 { m.status }

public fun key_epoch(m: &Mission): u64 { m.key_epoch }

public fun has_brief(m: &Mission): bool { m.brief.is_some() }

public fun deps(m: &Mission): vector<ID> { m.deps }

public fun deps_met_count(m: &Mission): u64 { m.deps_met.length() }

public fun claim_ttl_ms(m: &Mission): u64 { m.claim_ttl_ms }

public fun min_reputation(m: &Mission): u64 { m.min_reputation }

public fun attempts(m: &Mission): u64 { m.attempts }

public fun created_by(m: &Mission): address { m.created_by }

public fun has_claim(m: &Mission): bool { m.claim.is_some() }

public fun claimer(m: &Mission): address { m.claim.borrow().claimer }

public fun claim_operator(m: &Mission): Option<address> { m.claim.borrow().operator }

public fun claim_epoch(m: &Mission): u64 { m.claim.borrow().epoch }

public fun claim_deadline_ms(m: &Mission): u64 { m.claim.borrow().deadline_ms }

public fun note_count(m: &Mission): u64 { m.claim.borrow().notes.length() }

public fun has_delivery(m: &Mission): bool { m.delivery.is_some() }

public fun delivery_author(m: &Mission): address { m.delivery.borrow().author }

public fun review_deadline_ms(m: &Mission): u64 { m.delivery.borrow().review_deadline_ms }

public fun rejections(m: &Mission): u64 { m.delivery.borrow().rejections }

public fun is_overdue(m: &Mission): bool { m.delivery.borrow().overdue }

public fun accepted_by(m: &Mission): Option<address> { m.delivery.borrow().accepted_by }

public fun receipt_contributor(r: &DeliveryReceipt): address { r.contributor }

public fun receipt_mission_id(r: &DeliveryReceipt): ID { r.mission_id }

public fun receipt_accepted_by(r: &DeliveryReceipt): address { r.accepted_by }

public fun status_blocked(): u8 { STATUS_BLOCKED }

public fun status_rekey(): u8 { STATUS_REKEY }

public fun status_open(): u8 { STATUS_OPEN }

public fun status_claimed(): u8 { STATUS_CLAIMED }

public fun status_submitted(): u8 { STATUS_SUBMITTED }

public fun status_accepted(): u8 { STATUS_ACCEPTED }

public fun status_cancelled(): u8 { STATUS_CANCELLED }

public fun kind_brief(): u8 { KIND_BRIEF }

public fun kind_notes(): u8 { KIND_NOTES }

public fun kind_output(): u8 { KIND_OUTPUT }

// === Test-only hooks ===

#[test_only]
public fun new_board_for_testing(name: String, params: BoardParams, ctx: &mut TxContext): (Board, BoardAdminCap) {
    new_board(name, params, ctx)
}

#[test_only]
public fun new_mission_for_testing(
    board: &mut Board,
    title: String,
    labels: vector<String>,
    ttl_ms: u64,
    min_reputation: u64,
    clock: &Clock,
    ctx: &mut TxContext,
): Mission {
    new_mission(board, title, labels, ttl_ms, min_reputation, clock, ctx)
}

#[test_only]
public fun seal_approve_for_testing(
    id: vector<u8>,
    board: &Board,
    mission: &Mission,
    clock: &Clock,
    ctx: &mut TxContext,
) {
    seal_approve(id, board, mission, clock, ctx)
}

#[test_only]
public fun seal_approve_parent_for_testing(
    id: vector<u8>,
    board: &Board,
    parent: &Mission,
    child: &Mission,
    clock: &Clock,
    ctx: &mut TxContext,
) {
    seal_approve_parent(id, board, parent, child, clock, ctx)
}

#[test_only]
public fun set_version_for_testing(board: &mut Board, version: u64) { board.version = version }

#[test_only]
public fun destroy_board_for_testing(board: Board) {
    let Board { id, reputation, .. } = board;
    reputation.drop();
    id.delete();
}

#[test_only]
public fun destroy_mission_for_testing(mission: Mission) {
    let Mission { id, .. } = mission;
    id.delete();
}

#[test_only]
public fun destroy_cap_for_testing(cap: BoardAdminCap) {
    let BoardAdminCap { id, .. } = cap;
    id.delete();
}

#[test_only]
public fun destroy_receipt_for_testing(receipt: DeliveryReceipt) {
    let DeliveryReceipt { id, .. } = receipt;
    id.delete();
}
