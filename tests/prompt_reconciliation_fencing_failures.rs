#![allow(clippy::unwrap_used)]

#[allow(dead_code)]
#[path = "../src/bin/prompt-reconciliation-fenced-receipts.rs"]
mod fenced;

use fenced::{FencedReceiptState, Receipt, ReceiptOutcome, StateError};

fn receipt(operation: &str, mutation: &str, issue: &str) -> Receipt {
    Receipt {
        operation_id: operation.to_owned(),
        mutation_key: mutation.to_owned(),
        canonical_issue_id: issue.to_owned(),
    }
}

fn acquire(state: &mut FencedReceiptState, owner: &str, now_ms: u64) -> fenced::LeaseToken {
    state
        .acquire(owner, now_ms, 100)
        .unwrap_or_else(|error| panic!("lease acquisition failed: {error}"))
}

#[test]
fn stale_generation_loses_a_two_worker_compare_and_set_race() {
    let mut state = FencedReceiptState::default();
    let lease = acquire(&mut state, "worker-a", 100);

    let worker_a_generation = state.current_receipt_generation();
    let worker_b_generation = state.current_receipt_generation();

    assert_eq!(
        state.record(
            &lease,
            101,
            worker_a_generation,
            receipt("operation-a", "mutation-a", "DEN-100")
        ),
        Ok(ReceiptOutcome::Recorded)
    );
    assert_eq!(
        state.record(
            &lease,
            102,
            worker_b_generation,
            receipt("operation-b", "mutation-b", "DEN-200")
        ),
        Err(StateError::GenerationConflict)
    );

    let refreshed_generation = state.current_receipt_generation();
    assert_eq!(
        state.record(
            &lease,
            103,
            refreshed_generation,
            receipt("operation-b", "mutation-b", "DEN-200")
        ),
        Ok(ReceiptOutcome::Recorded)
    );
    assert_eq!(state.current_receipt_generation(), 2);
}

#[test]
fn restarted_worker_reuses_durable_receipt_after_lease_handoff() {
    let mut state = FencedReceiptState::default();
    let first_worker = acquire(&mut state, "worker-a", 100);
    let expected = state.current_receipt_generation();

    assert_eq!(
        state.record(
            &first_worker,
            101,
            expected,
            receipt("operation-a", "mutation-a", "DEN-100")
        ),
        Ok(ReceiptOutcome::Recorded)
    );

    // Simulate the process disappearing without releasing its lease. The durable
    // state remains, and a replacement worker may resume only after expiry.
    assert_eq!(
        state.acquire("worker-b", 150, 100),
        Err(StateError::LeaseHeld)
    );
    let replacement = acquire(&mut state, "worker-b", 200);

    assert_eq!(
        state.record(
            &first_worker,
            201,
            state.current_receipt_generation(),
            receipt("operation-b", "mutation-b", "DEN-200")
        ),
        Err(StateError::StaleFence)
    );
    assert_eq!(
        state.record(
            &replacement,
            201,
            state.current_receipt_generation(),
            receipt("operation-a", "mutation-a", "DEN-100")
        ),
        Ok(ReceiptOutcome::AlreadyRecorded)
    );
    assert_eq!(state.current_receipt_generation(), 1);
    assert_eq!(
        state.receipt("operation-a"),
        Some(&receipt("operation-a", "mutation-a", "DEN-100"))
    );
}

#[test]
fn duplicate_repair_survives_worker_crash_and_rejects_scope_drift() {
    let mut state = FencedReceiptState::default();
    let first_worker = acquire(&mut state, "worker-a", 100);

    let first = state.repair_duplicates(
        &first_worker,
        101,
        0,
        "repair-operation",
        "duplicate-race",
        [
            "DEN-30".to_owned(),
            "DEN-10".to_owned(),
            "DEN-20".to_owned(),
        ],
    );
    assert!(first.is_ok());
    assert_eq!(state.canonical_issue("DEN-30"), "DEN-10");

    let replacement = acquire(&mut state, "worker-b", 200);
    assert_eq!(
        state.repair_duplicates(
            &first_worker,
            201,
            1,
            "repair-operation",
            "duplicate-race",
            [
                "DEN-10".to_owned(),
                "DEN-20".to_owned(),
                "DEN-30".to_owned()
            ]
        ),
        Err(StateError::StaleFence)
    );

    let exact_rerun = state.repair_duplicates(
        &replacement,
        201,
        1,
        "repair-operation",
        "duplicate-race",
        [
            "DEN-20".to_owned(),
            "DEN-30".to_owned(),
            "DEN-10".to_owned(),
        ],
    );
    assert!(matches!(
        exact_rerun,
        Ok(fenced::DuplicateRepair {
            receipt_outcome: ReceiptOutcome::AlreadyRecorded,
            receipt_generation: 1,
            ..
        })
    ));

    assert_eq!(
        state.repair_duplicates(
            &replacement,
            202,
            1,
            "repair-operation",
            "duplicate-race",
            [
                "DEN-10".to_owned(),
                "DEN-20".to_owned(),
                "DEN-30".to_owned(),
                "DEN-40".to_owned()
            ]
        ),
        Err(StateError::ReceiptConflict)
    );
    assert_eq!(state.canonical_issue("DEN-40"), "DEN-40");
}

#[test]
fn renewed_lease_does_not_revive_an_expired_old_capability() {
    let mut state = FencedReceiptState::default();
    let original = acquire(&mut state, "worker-a", 100);
    let renewed = state
        .renew(&original, 150, 100)
        .unwrap_or_else(|error| panic!("lease renewal failed: {error}"));

    assert_eq!(
        state.record(
            &original,
            201,
            0,
            receipt("operation-a", "mutation-a", "DEN-100")
        ),
        Err(StateError::LeaseExpired)
    );
    assert_eq!(
        state.record(
            &renewed,
            201,
            0,
            receipt("operation-a", "mutation-a", "DEN-100")
        ),
        Ok(ReceiptOutcome::Recorded)
    );
}
