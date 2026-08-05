//! PostgreSQL persistence for daily portfolio briefing delivery state.
//!
//! The declarative schema remains authoritative in
//! `k8s-libs-and-shared-defs/pg-defs/schema/databases/ai_agent_coordinator/schema.sql`.
//! This module performs only schema-qualified runtime operations.

use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use chrono::{DateTime, Duration as ChronoDuration, NaiveDate, Utc};
use sea_orm::{
    sea_query::Value, AccessMode, ConnectOptions, ConnectionTrait, Database as SeaDatabase,
    DatabaseConnection, DatabaseTransaction, DbBackend, IsolationLevel, QueryResult, Statement,
    TransactionTrait,
};

use crate::daily_portfolio_delivery::{
    DeliveryState, DeliveryStateError, DeliveryStatus, DestinationReceipt, LeaseToken,
    MutationOutcome, PlanOutcome, PlanSpec, RunMode, ScheduledBaseline,
};

const MAX_LEASE_SECONDS: i64 = 3_600;
const MAX_IDENTIFIER_BYTES: usize = 256;
const MAX_ERROR_CHARS: usize = 512;
const EXPIRED_DELIVERY_ERROR: &str = "delivery lease expired before a receipt was committed";

#[derive(Clone)]
pub struct DailyPortfolioDeliveryStore {
    connection: DatabaseConnection,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoredDeliveryRun {
    pub spec: PlanSpec,
    pub status: DeliveryStatus,
    pub generation: u64,
    pub attempts: u64,
    pub receipt: Option<DestinationReceipt>,
    pub last_error: Option<String>,
    pub lease: Option<LeaseToken>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoredScheduledBaseline {
    pub source_run_key: String,
    pub baseline: ScheduledBaseline,
}

impl DailyPortfolioDeliveryStore {
    pub async fn connect(database_url: &str) -> Result<Self> {
        if !matches!(
            database_url.split_once("://").map(|(scheme, _)| scheme),
            Some("postgres" | "postgresql")
        ) {
            bail!("daily portfolio delivery database URL must use postgres:// or postgresql://");
        }

        let mut options = ConnectOptions::new(database_url.to_owned());
        options
            .max_connections(8)
            .min_connections(0)
            .connect_timeout(Duration::from_secs(10))
            .acquire_timeout(Duration::from_secs(10))
            .idle_timeout(Duration::from_secs(300))
            .sqlx_logging(false);
        let connection = SeaDatabase::connect(options)
            .await
            .context("failed to connect the daily portfolio delivery PostgreSQL store")?;
        Ok(Self { connection })
    }

    pub async fn verify_schema(&self) -> Result<()> {
        let row = self
            .connection
            .query_one(statement(
                r#"
                SELECT
                  to_regclass('ai_agent_coordinator.daily_portfolio_delivery_runs') IS NOT NULL
                  AND to_regclass('ai_agent_coordinator.daily_portfolio_delivery_baseline') IS NOT NULL
                  AND to_regclass('ai_agent_coordinator.daily_portfolio_delivery_fence_seq') IS NOT NULL
                  AS ready
                "#,
                vec![],
            ))
            .await
            .context("failed to verify the daily portfolio delivery schema")?
            .ok_or_else(|| anyhow!("daily portfolio delivery schema verification returned no row"))?;
        let ready: bool = row.try_get("", "ready")?;
        if !ready {
            bail!(
                "daily portfolio delivery is enabled but the canonical PostgreSQL schema has not been applied"
            );
        }
        Ok(())
    }

    pub async fn plan(&self, spec: &PlanSpec) -> Result<PlanOutcome> {
        validate_plan(spec)?;
        let result = self
            .connection
            .execute(statement(
                r#"
                INSERT INTO ai_agent_coordinator.daily_portfolio_delivery_runs (
                    run_key, scheduled_run_key, mode, source_digest, plan_digest,
                    delivery_digest, destination, idempotency_key
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (run_key) DO NOTHING
                "#,
                vec![
                    spec.run_key.clone().into(),
                    spec.scheduled_run_key.clone().into(),
                    mode_name(spec.mode).into(),
                    spec.source_digest.clone().into(),
                    spec.plan_digest.clone().into(),
                    spec.delivery_digest.clone().into(),
                    spec.destination.clone().into(),
                    spec.idempotency_key.clone().into(),
                ],
            ))
            .await
            .context("failed to plan a daily portfolio delivery run")?;

        if result.rows_affected() == 1 {
            return Ok(PlanOutcome::Planned);
        }

        let existing = self
            .get_run(&spec.run_key)
            .await?
            .ok_or_else(|| anyhow!("delivery run vanished after an insert conflict"))?;
        if existing.spec == *spec {
            Ok(PlanOutcome::AlreadyPlanned)
        } else {
            Err(domain(DeliveryStateError::RunConflict))
        }
    }

    pub async fn get_run(&self, run_key: &str) -> Result<Option<StoredDeliveryRun>> {
        load_run(&self.connection, run_key).await
    }

    pub async fn scheduled_baseline(&self) -> Result<Option<StoredScheduledBaseline>> {
        load_baseline(&self.connection, false).await
    }

    pub async fn claim(
        &self,
        run_key: &str,
        owner: &str,
        now: DateTime<Utc>,
        lease_seconds: i64,
    ) -> Result<LeaseToken> {
        validate_identifier(run_key)?;
        validate_identifier(owner)?;
        validate_lease_seconds(lease_seconds)?;

        let transaction = serializable(&self.connection).await?;
        let record = load_run_for_update(&transaction, run_key)
            .await?
            .ok_or_else(|| domain(DeliveryStateError::RunNotFound))?;
        if record.status == DeliveryStatus::Delivered {
            return Err(domain(DeliveryStateError::InvalidTransition));
        }
        if record
            .lease
            .as_ref()
            .is_some_and(|lease| lease.expires_at_ms > datetime_millis(now).unwrap_or(u64::MAX))
        {
            return Err(domain(DeliveryStateError::LeaseHeld));
        }

        let expires_at = now + ChronoDuration::seconds(lease_seconds);
        let row = transaction
            .query_one(statement(
                r#"
                UPDATE ai_agent_coordinator.daily_portfolio_delivery_runs
                   SET lease_owner = $2,
                       lease_fence = nextval('ai_agent_coordinator.daily_portfolio_delivery_fence_seq'),
                       lease_expires_at = $3,
                       updated_at = $4
                 WHERE run_key = $1
                 RETURNING lease_fence
                "#,
                vec![
                    run_key.into(),
                    owner.into(),
                    expires_at.into(),
                    now.into(),
                ],
            ))
            .await
            .context("failed to claim the daily portfolio delivery run")?
            .ok_or_else(|| anyhow!("claimed delivery run returned no fencing token"))?;
        let fence: i64 = row.try_get("", "lease_fence")?;
        transaction.commit().await?;
        Ok(LeaseToken {
            run_key: run_key.to_owned(),
            owner: owner.to_owned(),
            fence: positive_u64(fence, "lease fence")?,
            expires_at_ms: datetime_millis(expires_at)?,
        })
    }

    pub async fn renew(
        &self,
        token: &LeaseToken,
        now: DateTime<Utc>,
        lease_seconds: i64,
    ) -> Result<LeaseToken> {
        validate_token(token)?;
        validate_lease_seconds(lease_seconds)?;
        let expires_at = now + ChronoDuration::seconds(lease_seconds);
        let result = self
            .connection
            .execute(statement(
                r#"
                UPDATE ai_agent_coordinator.daily_portfolio_delivery_runs
                   SET lease_expires_at = $5,
                       updated_at = $4
                 WHERE run_key = $1
                   AND lease_owner = $2
                   AND lease_fence = $3
                   AND lease_expires_at > $4
                   AND status <> 'delivered'
                "#,
                vec![
                    token.run_key.clone().into(),
                    token.owner.clone().into(),
                    checked_i64(token.fence, "lease fence")?.into(),
                    now.into(),
                    expires_at.into(),
                ],
            ))
            .await
            .context("failed to renew the daily portfolio delivery lease")?;
        if result.rows_affected() != 1 {
            return Err(classify_token_failure(self, token, now).await);
        }
        Ok(LeaseToken {
            run_key: token.run_key.clone(),
            owner: token.owner.clone(),
            fence: token.fence,
            expires_at_ms: datetime_millis(expires_at)?,
        })
    }

    pub async fn release(&self, token: &LeaseToken, now: DateTime<Utc>) -> Result<()> {
        validate_token(token)?;
        let result = self
            .connection
            .execute(statement(
                r#"
                UPDATE ai_agent_coordinator.daily_portfolio_delivery_runs
                   SET lease_owner = NULL,
                       lease_fence = NULL,
                       lease_expires_at = NULL,
                       updated_at = $4
                 WHERE run_key = $1
                   AND lease_owner = $2
                   AND lease_fence = $3
                   AND lease_expires_at > $4
                   AND status <> 'delivering'
                   AND status <> 'delivered'
                "#,
                vec![
                    token.run_key.clone().into(),
                    token.owner.clone().into(),
                    checked_i64(token.fence, "lease fence")?.into(),
                    now.into(),
                ],
            ))
            .await
            .context("failed to release the daily portfolio delivery lease")?;
        if result.rows_affected() != 1 {
            let run = self
                .get_run(&token.run_key)
                .await?
                .ok_or_else(|| domain(DeliveryStateError::RunNotFound))?;
            require_active_token(&run, token, now)?;
            return Err(domain(DeliveryStateError::InvalidTransition));
        }
        Ok(())
    }

    pub async fn begin_delivery(
        &self,
        token: &LeaseToken,
        now: DateTime<Utc>,
        expected_generation: u64,
    ) -> Result<MutationOutcome> {
        validate_token(token)?;
        let result = self
            .connection
            .execute(statement(
                r#"
                UPDATE ai_agent_coordinator.daily_portfolio_delivery_runs
                   SET status = 'delivering',
                       generation = generation + 1,
                       attempts = attempts + 1,
                       last_error = NULL,
                       updated_at = $5
                 WHERE run_key = $1
                   AND lease_owner = $2
                   AND lease_fence = $3
                   AND lease_expires_at > $5
                   AND generation = $4
                   AND status IN ('planned', 'failed')
                "#,
                vec![
                    token.run_key.clone().into(),
                    token.owner.clone().into(),
                    checked_i64(token.fence, "lease fence")?.into(),
                    checked_i64(expected_generation, "expected generation")?.into(),
                    now.into(),
                ],
            ))
            .await
            .context("failed to begin daily portfolio delivery")?;
        if result.rows_affected() == 1 {
            return Ok(MutationOutcome::Applied);
        }
        let run = self
            .get_run(&token.run_key)
            .await?
            .ok_or_else(|| domain(DeliveryStateError::RunNotFound))?;
        if run.status == DeliveryStatus::Delivering && run.generation == expected_generation {
            return Ok(MutationOutcome::AlreadyApplied);
        }
        classify_mutation_failure(&run, token, now, expected_generation)
    }

    pub async fn mark_failed(
        &self,
        token: &LeaseToken,
        now: DateTime<Utc>,
        expected_generation: u64,
        error_summary: &str,
    ) -> Result<MutationOutcome> {
        self.mark_attempt_outcome(
            token,
            now,
            expected_generation,
            DeliveryStatus::Failed,
            error_summary,
        )
        .await
    }

    pub async fn mark_ambiguous(
        &self,
        token: &LeaseToken,
        now: DateTime<Utc>,
        expected_generation: u64,
        error_summary: &str,
    ) -> Result<MutationOutcome> {
        self.mark_attempt_outcome(
            token,
            now,
            expected_generation,
            DeliveryStatus::Ambiguous,
            error_summary,
        )
        .await
    }

    async fn mark_attempt_outcome(
        &self,
        token: &LeaseToken,
        now: DateTime<Utc>,
        expected_generation: u64,
        status: DeliveryStatus,
        error_summary: &str,
    ) -> Result<MutationOutcome> {
        validate_token(token)?;
        validate_error(error_summary)?;
        let status_name = status_name(status);
        let result = self
            .connection
            .execute(statement(
                r#"
                UPDATE ai_agent_coordinator.daily_portfolio_delivery_runs
                   SET status = $5,
                       generation = generation + 1,
                       last_error = $6,
                       lease_owner = NULL,
                       lease_fence = NULL,
                       lease_expires_at = NULL,
                       updated_at = $7
                 WHERE run_key = $1
                   AND lease_owner = $2
                   AND lease_fence = $3
                   AND generation = $4
                   AND lease_expires_at > $7
                   AND status = 'delivering'
                "#,
                vec![
                    token.run_key.clone().into(),
                    token.owner.clone().into(),
                    checked_i64(token.fence, "lease fence")?.into(),
                    checked_i64(expected_generation, "expected generation")?.into(),
                    status_name.into(),
                    error_summary.into(),
                    now.into(),
                ],
            ))
            .await
            .with_context(|| format!("failed to mark daily portfolio delivery {status_name}"))?;
        if result.rows_affected() == 1 {
            return Ok(MutationOutcome::Applied);
        }
        let run = self
            .get_run(&token.run_key)
            .await?
            .ok_or_else(|| domain(DeliveryStateError::RunNotFound))?;
        if run.status == status
            && run.generation == expected_generation.saturating_add(1)
            && run.last_error.as_deref() == Some(error_summary)
        {
            return Ok(MutationOutcome::AlreadyApplied);
        }
        classify_mutation_failure(&run, token, now, expected_generation)
    }

    pub async fn recover_expired_delivery(
        &self,
        run_key: &str,
        now: DateTime<Utc>,
    ) -> Result<MutationOutcome> {
        validate_identifier(run_key)?;
        let result = self
            .connection
            .execute(statement(
                r#"
                UPDATE ai_agent_coordinator.daily_portfolio_delivery_runs
                   SET status = 'ambiguous',
                       generation = generation + 1,
                       last_error = $2,
                       lease_owner = NULL,
                       lease_fence = NULL,
                       lease_expires_at = NULL,
                       updated_at = $3
                 WHERE run_key = $1
                   AND status = 'delivering'
                   AND lease_expires_at <= $3
                "#,
                vec![run_key.into(), EXPIRED_DELIVERY_ERROR.into(), now.into()],
            ))
            .await
            .context("failed to recover an expired daily portfolio delivery")?;
        if result.rows_affected() == 1 {
            return Ok(MutationOutcome::Applied);
        }
        let run = self
            .get_run(run_key)
            .await?
            .ok_or_else(|| domain(DeliveryStateError::RunNotFound))?;
        if run.status != DeliveryStatus::Delivering {
            return Ok(MutationOutcome::AlreadyApplied);
        }
        Err(domain(DeliveryStateError::LeaseHeld))
    }

    pub async fn record_receipt(
        &self,
        token: &LeaseToken,
        now: DateTime<Utc>,
        expected_generation: u64,
        receipt: &DestinationReceipt,
    ) -> Result<MutationOutcome> {
        validate_token(token)?;
        validate_receipt(receipt)?;
        let transaction = serializable(&self.connection).await?;
        let run = load_run_for_update(&transaction, &token.run_key)
            .await?
            .ok_or_else(|| domain(DeliveryStateError::RunNotFound))?;

        if run.status == DeliveryStatus::Delivered {
            let committed_generation = expected_generation
                .checked_add(1)
                .ok_or_else(|| domain(DeliveryStateError::CounterOverflow))?;
            if run.receipt.as_ref() != Some(receipt) {
                return Err(domain(DeliveryStateError::ReceiptConflict));
            }
            if run.generation != committed_generation {
                return Err(domain(DeliveryStateError::GenerationConflict));
            }
            transaction.commit().await?;
            return Ok(MutationOutcome::AlreadyApplied);
        }

        require_active_token(&run, token, now)?;
        if run.generation != expected_generation {
            return Err(domain(DeliveryStateError::GenerationConflict));
        }
        if !matches!(
            run.status,
            DeliveryStatus::Delivering | DeliveryStatus::Ambiguous
        ) {
            return Err(domain(DeliveryStateError::InvalidTransition));
        }
        if receipt.destination != run.spec.destination
            || receipt.body_digest != run.spec.delivery_digest
        {
            return Err(domain(DeliveryStateError::ReceiptConflict));
        }

        reconcile_baseline(&transaction, &run, receipt, now).await?;
        let delivered_at = receipt_datetime(receipt.delivered_at_ms)?;
        let result = transaction
            .execute(statement(
                r#"
                UPDATE ai_agent_coordinator.daily_portfolio_delivery_runs
                   SET status = 'delivered',
                       generation = generation + 1,
                       last_error = NULL,
                       lease_owner = NULL,
                       lease_fence = NULL,
                       lease_expires_at = NULL,
                       receipt_id = $5,
                       receipt_destination = $6,
                       receipt_body_digest = $7,
                       delivered_at = $8,
                       updated_at = $9
                 WHERE run_key = $1
                   AND lease_owner = $2
                   AND lease_fence = $3
                   AND generation = $4
                   AND lease_expires_at > $9
                   AND status IN ('delivering', 'ambiguous')
                "#,
                vec![
                    token.run_key.clone().into(),
                    token.owner.clone().into(),
                    checked_i64(token.fence, "lease fence")?.into(),
                    checked_i64(expected_generation, "expected generation")?.into(),
                    receipt.receipt_id.clone().into(),
                    receipt.destination.clone().into(),
                    receipt.body_digest.clone().into(),
                    delivered_at.into(),
                    now.into(),
                ],
            ))
            .await
            .context("failed to commit the daily portfolio destination receipt")?;
        if result.rows_affected() != 1 {
            return Err(domain(DeliveryStateError::GenerationConflict));
        }
        transaction.commit().await?;
        Ok(MutationOutcome::Applied)
    }
}

async fn serializable(connection: &DatabaseConnection) -> Result<DatabaseTransaction> {
    connection
        .begin_with_config(
            Some(IsolationLevel::Serializable),
            Some(AccessMode::ReadWrite),
        )
        .await
        .context("failed to begin a serializable daily portfolio delivery transaction")
}

async fn load_run<C: ConnectionTrait>(
    connection: &C,
    run_key: &str,
) -> Result<Option<StoredDeliveryRun>> {
    let row = connection
        .query_one(statement(
            r#"
            SELECT run_key, scheduled_run_key, mode, source_digest, plan_digest,
                   delivery_digest, destination, idempotency_key, status,
                   generation, attempts, last_error, lease_owner, lease_fence,
                   lease_expires_at, receipt_id, receipt_destination,
                   receipt_body_digest, delivered_at
              FROM ai_agent_coordinator.daily_portfolio_delivery_runs
             WHERE run_key = $1
            "#,
            vec![run_key.into()],
        ))
        .await
        .context("failed to load a daily portfolio delivery run")?;
    row.map(decode_run).transpose()
}

async fn load_run_for_update(
    transaction: &DatabaseTransaction,
    run_key: &str,
) -> Result<Option<StoredDeliveryRun>> {
    let row = transaction
        .query_one(statement(
            r#"
            SELECT run_key, scheduled_run_key, mode, source_digest, plan_digest,
                   delivery_digest, destination, idempotency_key, status,
                   generation, attempts, last_error, lease_owner, lease_fence,
                   lease_expires_at, receipt_id, receipt_destination,
                   receipt_body_digest, delivered_at
              FROM ai_agent_coordinator.daily_portfolio_delivery_runs
             WHERE run_key = $1
             FOR UPDATE
            "#,
            vec![run_key.into()],
        ))
        .await
        .context("failed to lock a daily portfolio delivery run")?;
    row.map(decode_run).transpose()
}

async fn load_baseline<C: ConnectionTrait>(
    connection: &C,
    for_update: bool,
) -> Result<Option<StoredScheduledBaseline>> {
    let suffix = if for_update { " FOR UPDATE" } else { "" };
    let sql = format!(
        r#"
        SELECT source_run_key, scheduled_run_key, plan_digest, delivery_digest,
               receipt_id, delivered_at
          FROM ai_agent_coordinator.daily_portfolio_delivery_baseline
         WHERE singleton_key = 'scheduled'{suffix}
        "#
    );
    let row = connection
        .query_one(Statement::from_string(DbBackend::Postgres, sql))
        .await
        .context("failed to load the daily portfolio scheduled baseline")?;
    row.map(|row| {
        let delivered_at: DateTime<Utc> = row.try_get("", "delivered_at")?;
        Ok(StoredScheduledBaseline {
            source_run_key: row.try_get("", "source_run_key")?,
            baseline: ScheduledBaseline {
                scheduled_run_key: row.try_get("", "scheduled_run_key")?,
                plan_digest: row.try_get("", "plan_digest")?,
                delivery_digest: row.try_get("", "delivery_digest")?,
                receipt_id: row.try_get("", "receipt_id")?,
                delivered_at_ms: datetime_millis(delivered_at)?,
            },
        })
    })
    .transpose()
}

async fn reconcile_baseline(
    transaction: &DatabaseTransaction,
    run: &StoredDeliveryRun,
    receipt: &DestinationReceipt,
    now: DateTime<Utc>,
) -> Result<()> {
    if run.spec.mode == RunMode::Manual {
        return Ok(());
    }

    let candidate_date = scheduled_date(&run.spec.scheduled_run_key)?;
    let current = load_baseline(transaction, true).await?;
    let replace = match current.as_ref() {
        None => true,
        Some(current) => {
            let current_date = scheduled_date(&current.baseline.scheduled_run_key)?;
            if candidate_date > current_date {
                true
            } else if candidate_date < current_date {
                false
            } else if current.baseline.plan_digest == run.spec.plan_digest
                && current.baseline.delivery_digest == run.spec.delivery_digest
                && current.baseline.receipt_id == receipt.receipt_id
            {
                false
            } else {
                return Err(domain(DeliveryStateError::BaselineConflict));
            }
        }
    };

    if !replace {
        return Ok(());
    }
    let delivered_at = receipt_datetime(receipt.delivered_at_ms)?;
    transaction
        .execute(statement(
            r#"
            INSERT INTO ai_agent_coordinator.daily_portfolio_delivery_baseline (
                singleton_key, source_run_key, scheduled_run_key, plan_digest,
                delivery_digest, receipt_id, delivered_at, updated_at
            ) VALUES ('scheduled', $1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (singleton_key) DO UPDATE SET
                source_run_key = EXCLUDED.source_run_key,
                scheduled_run_key = EXCLUDED.scheduled_run_key,
                plan_digest = EXCLUDED.plan_digest,
                delivery_digest = EXCLUDED.delivery_digest,
                receipt_id = EXCLUDED.receipt_id,
                delivered_at = EXCLUDED.delivered_at,
                updated_at = EXCLUDED.updated_at
            "#,
            vec![
                run.spec.run_key.clone().into(),
                run.spec.scheduled_run_key.clone().into(),
                run.spec.plan_digest.clone().into(),
                run.spec.delivery_digest.clone().into(),
                receipt.receipt_id.clone().into(),
                delivered_at.into(),
                now.into(),
            ],
        ))
        .await
        .context("failed to reconcile the daily portfolio scheduled baseline")?;
    Ok(())
}

fn decode_run(row: QueryResult) -> Result<StoredDeliveryRun> {
    let mode: String = row.try_get("", "mode")?;
    let status: String = row.try_get("", "status")?;
    let generation: i64 = row.try_get("", "generation")?;
    let attempts: i64 = row.try_get("", "attempts")?;

    let lease_owner: Option<String> = row.try_get("", "lease_owner")?;
    let lease_fence: Option<i64> = row.try_get("", "lease_fence")?;
    let lease_expires_at: Option<DateTime<Utc>> = row.try_get("", "lease_expires_at")?;
    let lease = match (lease_owner, lease_fence, lease_expires_at) {
        (None, None, None) => None,
        (Some(owner), Some(fence), Some(expires_at)) => Some(LeaseToken {
            run_key: row.try_get("", "run_key")?,
            owner,
            fence: positive_u64(fence, "lease fence")?,
            expires_at_ms: datetime_millis(expires_at)?,
        }),
        _ => bail!("database returned a partial daily portfolio delivery lease"),
    };

    let receipt_id: Option<String> = row.try_get("", "receipt_id")?;
    let receipt_destination: Option<String> = row.try_get("", "receipt_destination")?;
    let receipt_body_digest: Option<String> = row.try_get("", "receipt_body_digest")?;
    let delivered_at: Option<DateTime<Utc>> = row.try_get("", "delivered_at")?;
    let receipt = match (
        receipt_id,
        receipt_destination,
        receipt_body_digest,
        delivered_at,
    ) {
        (None, None, None, None) => None,
        (Some(receipt_id), Some(destination), Some(body_digest), Some(delivered_at)) => {
            Some(DestinationReceipt {
                receipt_id,
                destination,
                body_digest,
                delivered_at_ms: datetime_millis(delivered_at)?,
            })
        }
        _ => bail!("database returned a partial daily portfolio destination receipt"),
    };

    Ok(StoredDeliveryRun {
        spec: PlanSpec {
            run_key: row.try_get("", "run_key")?,
            scheduled_run_key: row.try_get("", "scheduled_run_key")?,
            mode: parse_mode(&mode)?,
            source_digest: row.try_get("", "source_digest")?,
            plan_digest: row.try_get("", "plan_digest")?,
            delivery_digest: row.try_get("", "delivery_digest")?,
            destination: row.try_get("", "destination")?,
            idempotency_key: row.try_get("", "idempotency_key")?,
        },
        status: parse_status(&status)?,
        generation: positive_or_zero_u64(generation, "generation")?,
        attempts: positive_or_zero_u64(attempts, "attempts")?,
        receipt,
        last_error: row.try_get("", "last_error")?,
        lease,
    })
}

async fn classify_token_failure(
    store: &DailyPortfolioDeliveryStore,
    token: &LeaseToken,
    now: DateTime<Utc>,
) -> anyhow::Error {
    match store.get_run(&token.run_key).await {
        Ok(Some(run)) => require_active_token(&run, token, now)
            .err()
            .unwrap_or_else(|| domain(DeliveryStateError::InvalidTransition)),
        Ok(None) => domain(DeliveryStateError::RunNotFound),
        Err(error) => error,
    }
}

fn classify_mutation_failure(
    run: &StoredDeliveryRun,
    token: &LeaseToken,
    now: DateTime<Utc>,
    expected_generation: u64,
) -> Result<MutationOutcome> {
    require_active_token(run, token, now)?;
    if run.generation != expected_generation {
        return Err(domain(DeliveryStateError::GenerationConflict));
    }
    Err(domain(DeliveryStateError::InvalidTransition))
}

fn require_active_token(
    run: &StoredDeliveryRun,
    token: &LeaseToken,
    now: DateTime<Utc>,
) -> Result<()> {
    let lease = run
        .lease
        .as_ref()
        .ok_or_else(|| domain(DeliveryStateError::LeaseUnavailable))?;
    if lease.owner != token.owner || lease.fence != token.fence {
        return Err(domain(DeliveryStateError::StaleFence));
    }
    let now_ms = datetime_millis(now)?;
    if lease.expires_at_ms <= now_ms || token.expires_at_ms <= now_ms {
        return Err(domain(DeliveryStateError::LeaseExpired));
    }
    Ok(())
}

fn validate_plan(spec: &PlanSpec) -> Result<()> {
    let mut state = DeliveryState::default();
    state.plan(spec.clone()).map(|_| ()).map_err(domain)
}

fn validate_token(token: &LeaseToken) -> Result<()> {
    validate_identifier(&token.run_key)?;
    validate_identifier(&token.owner)?;
    if token.fence == 0 || token.expires_at_ms == 0 {
        return Err(domain(DeliveryStateError::InvalidIdentifier));
    }
    Ok(())
}

fn validate_receipt(receipt: &DestinationReceipt) -> Result<()> {
    validate_identifier(&receipt.receipt_id)?;
    validate_identifier(&receipt.destination)?;
    validate_digest(&receipt.body_digest)?;
    if receipt.delivered_at_ms == 0 {
        return Err(domain(DeliveryStateError::ReceiptConflict));
    }
    Ok(())
}

fn validate_identifier(value: &str) -> Result<()> {
    let lower = value.to_ascii_lowercase();
    let credential_shaped = lower.starts_with("ghp_")
        || lower.starts_with("github_pat_")
        || lower.starts_with("sk-")
        || lower.contains("token=")
        || lower.contains("password=")
        || lower.contains("secret=");
    let valid = !value.is_empty()
        && value.len() <= MAX_IDENTIFIER_BYTES
        && !credential_shaped
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':' | b'/')
        });
    if valid {
        Ok(())
    } else {
        Err(domain(DeliveryStateError::InvalidIdentifier))
    }
}

fn validate_digest(value: &str) -> Result<()> {
    if value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
    {
        Ok(())
    } else {
        Err(domain(DeliveryStateError::InvalidDigest))
    }
}

fn validate_error(value: &str) -> Result<()> {
    if !value.is_empty()
        && value.chars().count() <= MAX_ERROR_CHARS
        && !value.chars().any(char::is_control)
    {
        Ok(())
    } else {
        Err(domain(DeliveryStateError::InvalidErrorSummary))
    }
}

fn validate_lease_seconds(value: i64) -> Result<()> {
    if (1..=MAX_LEASE_SECONDS).contains(&value) {
        Ok(())
    } else {
        Err(domain(DeliveryStateError::InvalidLeaseTtl))
    }
}

fn mode_name(mode: RunMode) -> &'static str {
    match mode {
        RunMode::Scheduled => "scheduled",
        RunMode::Recovery => "recovery",
        RunMode::Manual => "manual",
    }
}

fn status_name(status: DeliveryStatus) -> &'static str {
    match status {
        DeliveryStatus::Planned => "planned",
        DeliveryStatus::Delivering => "delivering",
        DeliveryStatus::Ambiguous => "ambiguous",
        DeliveryStatus::Failed => "failed",
        DeliveryStatus::Delivered => "delivered",
    }
}

fn parse_mode(value: &str) -> Result<RunMode> {
    match value {
        "scheduled" => Ok(RunMode::Scheduled),
        "recovery" => Ok(RunMode::Recovery),
        "manual" => Ok(RunMode::Manual),
        _ => bail!("database returned an unknown daily portfolio run mode"),
    }
}

fn parse_status(value: &str) -> Result<DeliveryStatus> {
    match value {
        "planned" => Ok(DeliveryStatus::Planned),
        "delivering" => Ok(DeliveryStatus::Delivering),
        "ambiguous" => Ok(DeliveryStatus::Ambiguous),
        "failed" => Ok(DeliveryStatus::Failed),
        "delivered" => Ok(DeliveryStatus::Delivered),
        _ => bail!("database returned an unknown daily portfolio delivery status"),
    }
}

fn scheduled_date(value: &str) -> Result<NaiveDate> {
    let suffix = value
        .strip_prefix("daily-portfolio:scheduled:")
        .ok_or_else(|| domain(DeliveryStateError::InvalidRunIdentity))?;
    NaiveDate::parse_from_str(suffix, "%Y-%m-%d")
        .map_err(|_| domain(DeliveryStateError::InvalidRunIdentity))
}

fn receipt_datetime(value: u64) -> Result<DateTime<Utc>> {
    let millis = checked_i64(value, "receipt timestamp")?;
    DateTime::from_timestamp_millis(millis)
        .ok_or_else(|| anyhow!("receipt timestamp is outside the supported range"))
}

fn datetime_millis(value: DateTime<Utc>) -> Result<u64> {
    positive_or_zero_u64(value.timestamp_millis(), "timestamp")
}

fn checked_i64(value: u64, label: &str) -> Result<i64> {
    i64::try_from(value).with_context(|| format!("{label} exceeds PostgreSQL bigint range"))
}

fn positive_u64(value: i64, label: &str) -> Result<u64> {
    if value <= 0 {
        bail!("{label} must be positive");
    }
    u64::try_from(value).with_context(|| format!("{label} is outside the supported range"))
}

fn positive_or_zero_u64(value: i64, label: &str) -> Result<u64> {
    if value < 0 {
        bail!("{label} must not be negative");
    }
    u64::try_from(value).with_context(|| format!("{label} is outside the supported range"))
}

fn domain(error: DeliveryStateError) -> anyhow::Error {
    anyhow::Error::new(error)
}

fn statement(sql: &'static str, values: Vec<Value>) -> Statement {
    Statement::from_sql_and_values(DbBackend::Postgres, sql, values)
}
