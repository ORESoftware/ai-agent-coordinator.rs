use ai_agent_coordinator::daily_portfolio_delivery::{
    DeliveryState, DeliveryStateError, DeliveryStatus, DestinationReceipt, MutationOutcome,
    PlanOutcome, PlanSpec, RunMode,
};

fn digest(seed: char) -> String {
    let nibble = seed.to_digit(36).expect("test digest seed") % 16;
    let character = char::from_digit(nibble, 16).expect("hex digest character");
    std::iter::repeat_n(character, 64).collect()
}

fn scheduled_key(date: &str) -> String {
    format!("daily-portfolio:scheduled:{date}")
}

fn plan_spec(mode: RunMode, run_key: &str, scheduled_date: &str, seed: char) -> PlanSpec {
    PlanSpec {
        run_key: run_key.to_owned(),
        scheduled_run_key: scheduled_key(scheduled_date),
        mode,
        source_digest: digest(seed),
        plan_digest: digest(char::from_u32(seed as u32 + 1).expect("test seed")),
        delivery_digest: digest(char::from_u32(seed as u32 + 2).expect("test seed")),
        destination: "slack:C0PORTFOLIO".to_owned(),
        idempotency_key: run_key.to_owned(),
    }
}

fn receipt(spec: &PlanSpec, receipt_id: &str, delivered_at_ms: u64) -> DestinationReceipt {
    DestinationReceipt {
        receipt_id: receipt_id.to_owned(),
        destination: spec.destination.clone(),
        body_digest: spec.delivery_digest.clone(),
        delivered_at_ms,
    }
}

fn deliver(state: &mut DeliveryState, spec: PlanSpec, owner: &str, now_ms: u64, receipt_id: &str) {
    let run_key = spec.run_key.clone();
    let destination_receipt = receipt(&spec, receipt_id, now_ms + 2);
    assert_eq!(state.plan(spec), Ok(PlanOutcome::Planned));
    let lease = state
        .acquire(&run_key, owner, now_ms, 1_000)
        .expect("acquire delivery lease");
    assert_eq!(
        state.begin_delivery(&lease, now_ms + 1, 0),
        Ok(MutationOutcome::Applied)
    );
    assert_eq!(
        state.record_receipt(&lease, now_ms + 2, 1, destination_receipt),
        Ok(MutationOutcome::Applied)
    );
}

#[test]
fn immutable_plan_is_idempotent_but_drift_conflicts() {
    let mut state = DeliveryState::default();
    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    assert_eq!(state.plan(spec.clone()), Ok(PlanOutcome::Planned));
    assert_eq!(state.plan(spec.clone()), Ok(PlanOutcome::AlreadyPlanned));

    let mut drifted = spec;
    drifted.source_digest = digest('f');
    assert_eq!(state.plan(drifted), Err(DeliveryStateError::RunConflict));
}

#[test]
fn active_lease_blocks_concurrency_and_expiry_advances_the_fence() {
    let mut state = DeliveryState::default();
    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    state.plan(spec.clone()).expect("plan");
    let first = state
        .acquire(&spec.run_key, "worker-a", 100, 50)
        .expect("first lease");
    assert_eq!(
        state.acquire(&spec.run_key, "worker-b", 120, 50),
        Err(DeliveryStateError::LeaseHeld)
    );

    let second = state
        .acquire(&spec.run_key, "worker-b", 150, 50)
        .expect("expired lease can be replaced");
    assert!(second.fence > first.fence);
    assert_eq!(
        state.renew(&first, 151, 50),
        Err(DeliveryStateError::StaleFence)
    );
    assert_eq!(state.lease(&spec.run_key), Some(&second));
}

#[test]
fn generation_compare_and_set_prevents_replayed_transitions() {
    let mut state = DeliveryState::default();
    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    state.plan(spec.clone()).expect("plan");
    let lease = state
        .acquire(&spec.run_key, "worker-a", 100, 1_000)
        .expect("lease");

    assert_eq!(
        state.begin_delivery(&lease, 101, 1),
        Err(DeliveryStateError::GenerationConflict)
    );
    assert_eq!(
        state.begin_delivery(&lease, 101, 0),
        Ok(MutationOutcome::Applied)
    );
    assert_eq!(
        state.begin_delivery(&lease, 102, 1),
        Ok(MutationOutcome::AlreadyApplied)
    );
    assert_eq!(
        state.mark_failed(&lease, 103, 0, "destination unavailable"),
        Err(DeliveryStateError::GenerationConflict)
    );
    assert_eq!(
        state.mark_failed(&lease, 103, 1, "destination unavailable"),
        Ok(MutationOutcome::Applied)
    );

    let record = state.run(&spec.run_key).expect("record");
    assert_eq!(record.status(), DeliveryStatus::Failed);
    assert_eq!(record.generation(), 2);
    assert_eq!(record.attempts(), 1);
    assert_eq!(record.last_error(), Some("destination unavailable"));
}

#[test]
fn retry_reuses_logical_identity_and_increments_attempts_once() {
    let mut state = DeliveryState::default();
    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    state.plan(spec.clone()).expect("plan");
    let first = state
        .acquire(&spec.run_key, "worker-a", 100, 100)
        .expect("lease");
    state
        .begin_delivery(&first, 101, 0)
        .expect("begin first attempt");
    state
        .mark_failed(&first, 102, 1, "temporary timeout")
        .expect("mark retryable failure");

    let second = state
        .acquire(&spec.run_key, "worker-b", 200, 100)
        .expect("retry lease");
    state
        .begin_delivery(&second, 201, 2)
        .expect("begin second attempt");
    let destination_receipt = receipt(&spec, "receipt-2", 202);
    state
        .record_receipt(&second, 202, 3, destination_receipt)
        .expect("commit receipt");

    let record = state.run(&spec.run_key).expect("record");
    assert_eq!(record.status(), DeliveryStatus::Delivered);
    assert_eq!(record.attempts(), 2);
    assert_eq!(record.spec().idempotency_key, spec.run_key);
}

#[test]
fn crash_before_send_can_reacquire_without_counting_an_attempt() {
    let mut durable = DeliveryState::default();
    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    durable.plan(spec.clone()).expect("plan");
    durable
        .acquire(&spec.run_key, "worker-a", 100, 20)
        .expect("lease before crash");

    let mut restarted = durable.clone();
    let replacement = restarted
        .acquire(&spec.run_key, "worker-b", 120, 20)
        .expect("replacement lease");
    restarted
        .begin_delivery(&replacement, 121, 0)
        .expect("first actual send attempt");
    assert_eq!(restarted.run(&spec.run_key).expect("record").attempts(), 1);
}

#[test]
fn expired_in_flight_delivery_requires_recovery_before_reacquisition() {
    let mut state = DeliveryState::default();
    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    state.plan(spec.clone()).expect("plan");
    let first = state
        .acquire(&spec.run_key, "worker-a", 100, 20)
        .expect("first lease");
    state.begin_delivery(&first, 101, 0).expect("send began");

    assert_eq!(
        state.acquire(&spec.run_key, "worker-b", 120, 20),
        Err(DeliveryStateError::RecoveryRequired)
    );
    assert_eq!(
        state.recover_expired_delivery(&spec.run_key, 120),
        Ok(MutationOutcome::Applied)
    );
    let reconciliation = state
        .acquire(&spec.run_key, "worker-b", 121, 20)
        .expect("reconciliation lease");
    assert_eq!(
        state.begin_delivery(&reconciliation, 122, 2),
        Err(DeliveryStateError::InvalidTransition)
    );
    state
        .record_receipt(
            &reconciliation,
            122,
            2,
            receipt(&spec, "destination-confirmed", 119),
        )
        .expect("verified receipt closes ambiguity");
}

#[test]
fn crash_after_send_becomes_ambiguous_and_requires_receipt_reconciliation() {
    let mut durable = DeliveryState::default();
    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    durable.plan(spec.clone()).expect("plan");
    let original = durable
        .acquire(&spec.run_key, "worker-a", 100, 20)
        .expect("lease");
    durable
        .begin_delivery(&original, 101, 0)
        .expect("send began");

    let mut restarted = durable.clone();
    assert_eq!(
        restarted.recover_expired_delivery(&spec.run_key, 120),
        Ok(MutationOutcome::Applied)
    );
    let ambiguous = restarted.run(&spec.run_key).expect("record");
    assert_eq!(ambiguous.status(), DeliveryStatus::Ambiguous);
    assert_eq!(ambiguous.generation(), 2);

    let reconciliation = restarted
        .acquire(&spec.run_key, "worker-b", 121, 20)
        .expect("reconciliation lease");
    assert_eq!(
        restarted.begin_delivery(&reconciliation, 122, 2),
        Err(DeliveryStateError::InvalidTransition)
    );
    restarted
        .record_receipt(
            &reconciliation,
            122,
            2,
            receipt(&spec, "destination-confirmed", 119),
        )
        .expect("verified external receipt closes ambiguity");
    assert_eq!(
        restarted.run(&spec.run_key).expect("record").status(),
        DeliveryStatus::Delivered
    );
}

#[test]
fn stale_owner_cannot_commit_a_receipt_after_reacquisition() {
    let mut state = DeliveryState::default();
    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    state.plan(spec.clone()).expect("plan");
    let stale = state
        .acquire(&spec.run_key, "worker-a", 100, 20)
        .expect("first lease");
    let current = state
        .acquire(&spec.run_key, "worker-b", 120, 20)
        .expect("replacement lease");
    state
        .begin_delivery(&current, 121, 0)
        .expect("current owner begins");

    assert_eq!(
        state.record_receipt(&stale, 121, 1, receipt(&spec, "stale", 121)),
        Err(DeliveryStateError::StaleFence)
    );
    assert_eq!(
        state.run(&spec.run_key).expect("record").status(),
        DeliveryStatus::Delivering
    );
}

#[test]
fn exact_receipt_replay_is_idempotent_but_receipt_or_generation_drift_conflicts() {
    let mut state = DeliveryState::default();
    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    state.plan(spec.clone()).expect("plan");
    let lease = state
        .acquire(&spec.run_key, "worker-a", 100, 100)
        .expect("lease");
    state.begin_delivery(&lease, 101, 0).expect("begin");
    let confirmed = receipt(&spec, "receipt", 102);
    assert_eq!(
        state.record_receipt(&lease, 102, 1, confirmed.clone()),
        Ok(MutationOutcome::Applied)
    );
    assert_eq!(
        state.record_receipt(&lease, 103, 1, confirmed.clone()),
        Ok(MutationOutcome::AlreadyApplied)
    );
    assert_eq!(
        state.record_receipt(&lease, 103, 2, confirmed.clone()),
        Err(DeliveryStateError::GenerationConflict)
    );

    let mut drifted = confirmed;
    drifted.receipt_id = "different-receipt".to_owned();
    assert_eq!(
        state.record_receipt(&lease, 103, 1, drifted),
        Err(DeliveryStateError::ReceiptConflict)
    );
}

#[test]
fn mismatched_destination_or_body_receipt_does_not_commit() {
    let mut state = DeliveryState::default();
    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    state.plan(spec.clone()).expect("plan");
    let lease = state
        .acquire(&spec.run_key, "worker-a", 100, 100)
        .expect("lease");
    state.begin_delivery(&lease, 101, 0).expect("begin");

    let mut wrong_destination = receipt(&spec, "receipt", 102);
    wrong_destination.destination = "slack:OTHER".to_owned();
    assert_eq!(
        state.record_receipt(&lease, 102, 1, wrong_destination),
        Err(DeliveryStateError::ReceiptConflict)
    );

    let mut wrong_body = receipt(&spec, "receipt", 102);
    wrong_body.body_digest = digest('f');
    assert_eq!(
        state.record_receipt(&lease, 102, 1, wrong_body),
        Err(DeliveryStateError::ReceiptConflict)
    );
    assert_eq!(
        state.run(&spec.run_key).expect("record").status(),
        DeliveryStatus::Delivering
    );
}

#[test]
fn scheduled_and_recovery_deliveries_advance_baseline_but_manual_does_not() {
    let mut state = DeliveryState::default();
    let scheduled = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    deliver(&mut state, scheduled, "worker-a", 100, "receipt-05");
    assert_eq!(
        state
            .scheduled_baseline()
            .expect("scheduled baseline")
            .scheduled_run_key,
        scheduled_key("2026-08-05")
    );

    let manual = plan_spec(
        RunMode::Manual,
        "daily-portfolio:manual:operator-check",
        "2026-08-06",
        'd',
    );
    deliver(&mut state, manual, "worker-b", 200, "manual-receipt");
    assert_eq!(
        state
            .scheduled_baseline()
            .expect("manual must not replace baseline")
            .scheduled_run_key,
        scheduled_key("2026-08-05")
    );

    let recovery = plan_spec(
        RunMode::Recovery,
        "daily-portfolio:recovery:2026-08-06:attempt-1",
        "2026-08-06",
        'b',
    );
    deliver(&mut state, recovery, "worker-c", 300, "receipt-06");
    assert_eq!(
        state
            .scheduled_baseline()
            .expect("recovery advances baseline")
            .scheduled_run_key,
        scheduled_key("2026-08-06")
    );

    let older = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-04",
        "2026-08-04",
        'c',
    );
    deliver(&mut state, older, "worker-d", 400, "receipt-04");
    assert_eq!(
        state
            .scheduled_baseline()
            .expect("older delivery cannot regress baseline")
            .scheduled_run_key,
        scheduled_key("2026-08-06")
    );
}

#[test]
fn same_scheduled_date_with_different_delivery_conflicts_before_commit() {
    let mut state = DeliveryState::default();
    let first = plan_spec(
        RunMode::Recovery,
        "daily-portfolio:recovery:2026-08-05:first",
        "2026-08-05",
        'a',
    );
    deliver(&mut state, first, "worker-a", 100, "receipt-first");

    let second = plan_spec(
        RunMode::Recovery,
        "daily-portfolio:recovery:2026-08-05:second",
        "2026-08-05",
        'd',
    );
    state.plan(second.clone()).expect("second plan");
    let lease = state
        .acquire(&second.run_key, "worker-b", 200, 100)
        .expect("lease");
    state.begin_delivery(&lease, 201, 0).expect("begin");
    assert_eq!(
        state.record_receipt(&lease, 202, 1, receipt(&second, "receipt-second", 202)),
        Err(DeliveryStateError::BaselineConflict)
    );
    assert_eq!(
        state.run(&second.run_key).expect("record").status(),
        DeliveryStatus::Delivering
    );
}

#[test]
fn releasing_a_sending_lease_is_forbidden_but_idle_release_and_renew_work() {
    let mut state = DeliveryState::default();
    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    state.plan(spec.clone()).expect("plan");
    let lease = state
        .acquire(&spec.run_key, "worker-a", 100, 100)
        .expect("lease");
    let renewed = state.renew(&lease, 110, 200).expect("renew");
    assert_eq!(renewed.fence, lease.fence);
    state.release(&renewed, 111).expect("idle release");

    let sending = state
        .acquire(&spec.run_key, "worker-b", 120, 100)
        .expect("second lease");
    state.begin_delivery(&sending, 121, 0).expect("begin");
    assert_eq!(
        state.release(&sending, 122),
        Err(DeliveryStateError::InvalidTransition)
    );
}

#[test]
fn invalid_identity_digest_receipt_and_error_summary_fail_closed() {
    let mut state = DeliveryState::default();
    let mut invalid = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    invalid.destination = "token=secret-value".to_owned();
    assert_eq!(
        state.plan(invalid),
        Err(DeliveryStateError::InvalidIdentifier)
    );

    let mut other_token_shape = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    other_token_shape.destination = "gho_abcdefghijklmnopqrstuvwxyz1234567890".to_owned();
    assert_eq!(
        state.plan(other_token_shape),
        Err(DeliveryStateError::InvalidIdentifier)
    );

    let empty_recovery_identity = plan_spec(
        RunMode::Recovery,
        "daily-portfolio:recovery:",
        "2026-08-05",
        'a',
    );
    assert_eq!(
        state.plan(empty_recovery_identity),
        Err(DeliveryStateError::InvalidRunIdentity)
    );

    let mut invalid_digest = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    invalid_digest.plan_digest = "ABC".to_owned();
    assert_eq!(
        state.plan(invalid_digest),
        Err(DeliveryStateError::InvalidDigest)
    );

    let spec = plan_spec(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    state.plan(spec.clone()).expect("plan");
    let lease = state
        .acquire(&spec.run_key, "worker-a", 100, 100)
        .expect("lease");
    state.begin_delivery(&lease, 101, 0).expect("begin");
    assert_eq!(
        state.mark_failed(&lease, 102, 1, "line one\nline two"),
        Err(DeliveryStateError::InvalidErrorSummary)
    );

    let mut zero_time = receipt(&spec, "receipt", 0);
    zero_time.delivered_at_ms = 0;
    assert_eq!(
        state.record_receipt(&lease, 102, 1, zero_time),
        Err(DeliveryStateError::ReceiptConflict)
    );
}
