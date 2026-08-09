use ai_agent_coordinator::{
    daily_portfolio_delivery::{
        DeliveryStateError, DeliveryStatus, DestinationReceipt, MutationOutcome, PlanOutcome,
        PlanSpec, RunMode,
    },
    daily_portfolio_delivery_store::DailyPortfolioDeliveryStore,
};
use chrono::{Duration, Utc};
use sea_orm::{ConnectionTrait, Database, DbBackend, Statement};

fn digest(seed: char) -> String {
    let nibble = seed.to_digit(36).expect("test digest seed") % 16;
    let character = char::from_digit(nibble, 16).expect("hex digest character");
    std::iter::repeat_n(character, 64).collect()
}

fn scheduled_key(date: &str) -> String {
    format!("daily-portfolio:scheduled:{date}")
}

fn plan(mode: RunMode, run_key: &str, date: &str, seed: char) -> PlanSpec {
    PlanSpec {
        run_key: run_key.to_owned(),
        scheduled_run_key: scheduled_key(date),
        mode,
        source_digest: digest(seed),
        plan_digest: digest(char::from_u32(seed as u32 + 1).expect("test seed")),
        delivery_digest: digest(char::from_u32(seed as u32 + 2).expect("test seed")),
        destination: "slack:C0PORTFOLIO".to_owned(),
        idempotency_key: run_key.to_owned(),
    }
}

fn receipt(spec: &PlanSpec, id: &str, at: chrono::DateTime<Utc>) -> DestinationReceipt {
    DestinationReceipt {
        receipt_id: id.to_owned(),
        destination: spec.destination.clone(),
        body_digest: spec.delivery_digest.clone(),
        delivered_at_ms: u64::try_from(at.timestamp_millis()).expect("positive fixture time"),
    }
}

fn has_state_error(error: &anyhow::Error, expected: DeliveryStateError) -> bool {
    error.downcast_ref::<DeliveryStateError>() == Some(&expected)
}

async fn reset(database_url: &str) {
    let connection = Database::connect(database_url)
        .await
        .expect("connect for test cleanup");
    connection
        .execute(Statement::from_string(
            DbBackend::Postgres,
            "TRUNCATE ai_agent_coordinator.daily_portfolio_delivery_baseline, ai_agent_coordinator.daily_portfolio_delivery_runs RESTART IDENTITY CASCADE",
        ))
        .await
        .expect("reset delivery tables");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn postgres_repository_preserves_fences_receipts_and_restart_state() {
    let Ok(database_url) = std::env::var("TEST_DATABASE_URL") else {
        eprintln!("TEST_DATABASE_URL is absent; skipping PostgreSQL delivery-store contract");
        return;
    };

    reset(&database_url).await;
    let first = DailyPortfolioDeliveryStore::connect(&database_url)
        .await
        .expect("connect first repository");
    let second = DailyPortfolioDeliveryStore::connect(&database_url)
        .await
        .expect("connect second repository");
    first.verify_schema().await.expect("schema ready");

    let scheduled = plan(
        RunMode::Scheduled,
        "daily-portfolio:scheduled:2026-08-05",
        "2026-08-05",
        'a',
    );
    assert_eq!(first.plan(&scheduled).await.unwrap(), PlanOutcome::Planned);
    assert_eq!(
        first.plan(&scheduled).await.unwrap(),
        PlanOutcome::AlreadyPlanned
    );
    let mut drifted = scheduled.clone();
    drifted.plan_digest = digest('f');
    let error = first.plan(&drifted).await.unwrap_err();
    assert!(has_state_error(&error, DeliveryStateError::RunConflict));

    let now = Utc::now();
    let (claim_a, claim_b) = tokio::join!(
        first.claim(&scheduled.run_key, "worker-a", now, 60),
        second.claim(&scheduled.run_key, "worker-b", now, 60)
    );
    let winning = match (claim_a, claim_b) {
        (Ok(token), Err(error)) | (Err(error), Ok(token)) => {
            assert!(
                has_state_error(&error, DeliveryStateError::LeaseHeld)
                    || error.to_string().contains("serialize"),
                "unexpected losing claim error: {error:#}"
            );
            token
        }
        outcome => panic!("exactly one concurrent claim must win: {outcome:?}"),
    };
    let claimed = first
        .get_run(&scheduled.run_key)
        .await
        .unwrap()
        .expect("claimed run");
    assert_eq!(claimed.lease.as_ref(), Some(&winning));
    assert_eq!(claimed.status, DeliveryStatus::Planned);
    assert_eq!(claimed.attempts, 0);

    assert_eq!(
        first
            .begin_delivery(&winning, now + Duration::seconds(1), 0)
            .await
            .unwrap(),
        MutationOutcome::Applied
    );
    let replay = first
        .begin_delivery(&winning, now + Duration::seconds(2), 1)
        .await
        .unwrap();
    assert_eq!(replay, MutationOutcome::AlreadyApplied);

    drop(first);
    drop(second);
    let restarted = DailyPortfolioDeliveryStore::connect(&database_url)
        .await
        .expect("reconnect after simulated process restart");
    let reloaded = restarted
        .get_run(&scheduled.run_key)
        .await
        .unwrap()
        .expect("reload durable run");
    assert_eq!(reloaded.status, DeliveryStatus::Delivering);
    assert_eq!(reloaded.generation, 1);
    assert_eq!(reloaded.attempts, 1);
    assert_eq!(reloaded.lease.as_ref(), Some(&winning));

    assert_eq!(
        restarted
            .mark_failed(
                &winning,
                now + Duration::seconds(3),
                1,
                "bounded destination timeout",
            )
            .await
            .unwrap(),
        MutationOutcome::Applied
    );
    let retry = restarted
        .claim(
            &scheduled.run_key,
            "worker-retry",
            now + Duration::seconds(4),
            60,
        )
        .await
        .expect("claim retry");
    assert!(retry.fence > winning.fence);
    restarted
        .begin_delivery(&retry, now + Duration::seconds(5), 2)
        .await
        .expect("begin retry");
    let confirmed = receipt(&scheduled, "scheduled-receipt", now + Duration::seconds(6));
    assert_eq!(
        restarted
            .record_receipt(&retry, now + Duration::seconds(6), 3, &confirmed)
            .await
            .unwrap(),
        MutationOutcome::Applied
    );
    assert_eq!(
        restarted
            .record_receipt(&retry, now + Duration::seconds(7), 3, &confirmed)
            .await
            .unwrap(),
        MutationOutcome::AlreadyApplied
    );

    let delivered = restarted
        .get_run(&scheduled.run_key)
        .await
        .unwrap()
        .expect("delivered run");
    assert_eq!(delivered.status, DeliveryStatus::Delivered);
    assert_eq!(delivered.generation, 4);
    assert_eq!(delivered.attempts, 2);
    assert_eq!(delivered.receipt.as_ref(), Some(&confirmed));
    assert!(delivered.lease.is_none());
    assert!(delivered.last_error.is_none());
    let baseline = restarted
        .scheduled_baseline()
        .await
        .unwrap()
        .expect("scheduled baseline");
    assert_eq!(baseline.source_run_key, scheduled.run_key);
    assert_eq!(baseline.baseline.receipt_id, "scheduled-receipt");

    let manual = plan(
        RunMode::Manual,
        "daily-portfolio:manual:operator-check",
        "2026-08-06",
        'd',
    );
    restarted.plan(&manual).await.expect("plan manual");
    let manual_lease = restarted
        .claim(
            &manual.run_key,
            "manual-worker",
            now + Duration::seconds(8),
            60,
        )
        .await
        .expect("claim manual");
    restarted
        .begin_delivery(&manual_lease, now + Duration::seconds(9), 0)
        .await
        .expect("begin manual");
    restarted
        .record_receipt(
            &manual_lease,
            now + Duration::seconds(10),
            1,
            &receipt(&manual, "manual-receipt", now + Duration::seconds(10)),
        )
        .await
        .expect("deliver manual");
    let baseline_after_manual = restarted
        .scheduled_baseline()
        .await
        .unwrap()
        .expect("baseline remains");
    assert_eq!(baseline_after_manual.source_run_key, scheduled.run_key);

    let recovery = plan(
        RunMode::Recovery,
        "daily-portfolio:recovery:2026-08-06:attempt-1",
        "2026-08-06",
        '7',
    );
    restarted.plan(&recovery).await.expect("plan recovery");
    let crashed = restarted
        .claim(
            &recovery.run_key,
            "crashed-worker",
            now + Duration::seconds(11),
            1,
        )
        .await
        .expect("claim recovery");
    restarted
        .begin_delivery(&crashed, now + Duration::seconds(11), 0)
        .await
        .expect("begin recovery send");
    assert_eq!(
        restarted
            .recover_expired_delivery(&recovery.run_key, now + Duration::seconds(13))
            .await
            .unwrap(),
        MutationOutcome::Applied
    );
    let ambiguous = restarted
        .get_run(&recovery.run_key)
        .await
        .unwrap()
        .expect("ambiguous recovery");
    assert_eq!(ambiguous.status, DeliveryStatus::Ambiguous);
    assert_eq!(ambiguous.generation, 2);

    let reconciler = restarted
        .claim(
            &recovery.run_key,
            "receipt-reconciler",
            now + Duration::seconds(14),
            60,
        )
        .await
        .expect("claim receipt reconciliation");
    let recovery_receipt = receipt(&recovery, "recovery-receipt", now + Duration::seconds(12));
    restarted
        .record_receipt(
            &reconciler,
            now + Duration::seconds(15),
            2,
            &recovery_receipt,
        )
        .await
        .expect("reconcile external receipt");
    let advanced = restarted
        .scheduled_baseline()
        .await
        .unwrap()
        .expect("advanced baseline");
    assert_eq!(advanced.source_run_key, recovery.run_key);
    assert_eq!(
        advanced.baseline.scheduled_run_key,
        scheduled_key("2026-08-06")
    );

    let stale_case = plan(
        RunMode::Manual,
        "daily-portfolio:manual:stale-fence",
        "2026-08-06",
        '9',
    );
    restarted.plan(&stale_case).await.expect("plan stale case");
    let stale = restarted
        .claim(
            &stale_case.run_key,
            "stale-worker",
            now + Duration::seconds(16),
            1,
        )
        .await
        .expect("claim stale token");
    let current = restarted
        .claim(
            &stale_case.run_key,
            "current-worker",
            now + Duration::seconds(18),
            60,
        )
        .await
        .expect("replace expired token");
    assert!(current.fence > stale.fence);
    let stale_error = restarted
        .begin_delivery(&stale, now + Duration::seconds(19), 0)
        .await
        .unwrap_err();
    assert!(has_state_error(
        &stale_error,
        DeliveryStateError::StaleFence
    ));
    restarted
        .begin_delivery(&current, now + Duration::seconds(19), 0)
        .await
        .expect("current fence mutates run");

    drop(restarted);
    let final_restart = DailyPortfolioDeliveryStore::connect(&database_url)
        .await
        .expect("final restart");
    final_restart
        .verify_schema()
        .await
        .expect("schema after restart");
    assert_eq!(
        final_restart
            .get_run(&recovery.run_key)
            .await
            .unwrap()
            .expect("recovery persisted")
            .status,
        DeliveryStatus::Delivered
    );
    reset(&database_url).await;
}
