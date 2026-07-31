use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use sea_orm::ConnectionTrait;

use super::{statement, AttentionStore, RunRecord, RunState, SourceState};

impl AttentionStore {
    pub(in crate::email_attention) async fn pending_delivery_count(&self) -> Result<usize> {
        let row = self
            .connection
            .query_one(statement(
                r#"
                SELECT COUNT(*) AS pending
                  FROM ai_agent_coordinator.email_attention_deliveries
                 WHERE status = 'pending'
                "#,
                vec![],
            ))
            .await
            .context("failed to count pending email-attention deliveries")?;
        let count: i64 = row
            .map(|row| row.try_get("", "pending"))
            .transpose()?
            .unwrap_or(0);
        Ok(count.max(0) as usize)
    }

    pub(in crate::email_attention) async fn source_states(&self) -> Result<Vec<SourceState>> {
        let rows = self
            .connection
            .query_all(statement(
                r#"
                SELECT source_id, provider, last_success_at, last_error, last_error_at
                  FROM ai_agent_coordinator.email_attention_sources
                 ORDER BY source_id ASC
                "#,
                vec![],
            ))
            .await
            .context("failed to list email-attention source states")?;
        rows.into_iter()
            .map(|row| {
                Ok(SourceState {
                    source_id: row.try_get("", "source_id")?,
                    provider: row.try_get("", "provider")?,
                    last_success_at: row.try_get("", "last_success_at")?,
                    last_error: row.try_get("", "last_error")?,
                    last_error_at: row.try_get("", "last_error_at")?,
                })
            })
            .collect::<Result<Vec<_>>>()
            .context("failed to decode email-attention source states")
    }

    pub(in crate::email_attention) async fn last_run(&self) -> Result<Option<RunState>> {
        self.query_run(
            r#"
            SELECT run_id, mode, started_at, finished_at, scan_status,
                   notification_status,
                   attention_item_count::bigint AS attention_item_count,
                   source_success_count::bigint AS source_success_count,
                   source_failure_count::bigint AS source_failure_count,
                   error
              FROM ai_agent_coordinator.email_attention_runs
             ORDER BY finished_at DESC
             LIMIT 1
            "#,
        )
        .await
    }

    pub(in crate::email_attention) async fn last_successful_scheduled_run_at(
        &self,
    ) -> Result<Option<DateTime<Utc>>> {
        let row = self
            .connection
            .query_one(statement(
                r#"
                SELECT finished_at
                  FROM ai_agent_coordinator.email_attention_runs
                 WHERE mode = 'scheduled'
                   AND scan_status IN ('success', 'partial')
                 ORDER BY finished_at DESC
                 LIMIT 1
                "#,
                vec![],
            ))
            .await
            .context("failed to read the last successful email-attention run")?;
        row.map(|row| row.try_get("", "finished_at"))
            .transpose()
            .context("failed to decode the last successful email-attention run")
    }

    pub(in crate::email_attention) async fn record_run(&self, run: &RunRecord) -> Result<()> {
        let error = run
            .error
            .as_deref()
            .map(|value| super::bounded_text(value, 512));
        self.connection
            .execute(statement(
                r#"
                INSERT INTO ai_agent_coordinator.email_attention_runs (
                    run_id, mode, started_at, finished_at, scan_status,
                    notification_status, attention_item_count,
                    source_success_count, source_failure_count, error
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                "#,
                vec![
                    run.run_id.clone().into(),
                    run.mode.clone().into(),
                    run.started_at.into(),
                    run.finished_at.into(),
                    run.scan_status.clone().into(),
                    run.notification_status.clone().into(),
                    (run.attention_item_count as i64).into(),
                    (run.source_success_count as i64).into(),
                    (run.source_failure_count as i64).into(),
                    error.into(),
                ],
            ))
            .await
            .context("failed to record email-attention run")?;
        Ok(())
    }

    pub(in crate::email_attention) async fn try_acquire_lease(
        &self,
        name: &str,
        holder: &str,
        now: DateTime<Utc>,
        expires_at: DateTime<Utc>,
    ) -> Result<bool> {
        let changed = self
            .connection
            .execute(statement(
                r#"
                INSERT INTO ai_agent_coordinator.email_attention_leases (
                    name, holder, expires_at, updated_at
                ) VALUES ($1, $2, $3, $4)
                ON CONFLICT (name) DO UPDATE SET
                    holder = EXCLUDED.holder,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = EXCLUDED.updated_at
                WHERE ai_agent_coordinator.email_attention_leases.expires_at <= $4
                   OR ai_agent_coordinator.email_attention_leases.holder = EXCLUDED.holder
                "#,
                vec![name.into(), holder.into(), expires_at.into(), now.into()],
            ))
            .await
            .context("failed to negotiate the email-attention scheduler lease")?
            .rows_affected();
        Ok(changed == 1)
    }

    #[cfg(test)]
    pub(in crate::email_attention) async fn clear_lease(&self, name: &str) -> Result<()> {
        self.connection
            .execute(statement(
                r#"
                DELETE FROM ai_agent_coordinator.email_attention_leases
                 WHERE name = $1
                "#,
                vec![name.into()],
            ))
            .await
            .context("failed to clear the email-attention scheduler lease")?;
        Ok(())
    }

    async fn query_run(&self, sql: &'static str) -> Result<Option<RunState>> {
        let row = self
            .connection
            .query_one(statement(sql, vec![]))
            .await
            .context("failed to read email-attention run state")?;
        row.map(|row| {
            let attention_item_count: i64 = row.try_get("", "attention_item_count")?;
            let source_success_count: i64 = row.try_get("", "source_success_count")?;
            let source_failure_count: i64 = row.try_get("", "source_failure_count")?;
            Ok(RunState {
                run_id: row.try_get("", "run_id")?,
                mode: row.try_get("", "mode")?,
                started_at: row.try_get("", "started_at")?,
                finished_at: row.try_get("", "finished_at")?,
                scan_status: row.try_get("", "scan_status")?,
                notification_status: row.try_get("", "notification_status")?,
                attention_item_count: attention_item_count.max(0) as usize,
                source_success_count: source_success_count.max(0) as usize,
                source_failure_count: source_failure_count.max(0) as usize,
                error: row.try_get("", "error")?,
            })
        })
        .transpose()
        .context("failed to decode email-attention run state")
    }
}
