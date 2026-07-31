mod delivery;
mod status;

#[cfg(test)]
mod tests;

use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use chrono::{DateTime, Utc};
use sea_orm::{
    sea_query::Value, ConnectOptions, ConnectionTrait, Database as SeaDatabase, DatabaseConnection,
    DbBackend, Statement,
};
use serde_json::Value as JsonValue;

#[derive(Clone)]
pub(super) struct AttentionStore {
    connection: DatabaseConnection,
}

#[derive(Debug, Clone)]
pub(super) struct ItemState {
    pub(super) last_emitted_fingerprint: Option<String>,
    pub(super) last_emitted_at: Option<DateTime<Utc>>,
    pub(super) pending_delivery_key: Option<String>,
}

#[derive(Debug, Clone)]
pub(super) struct SeenItem {
    pub(super) source_id: String,
    pub(super) stable_id: String,
    pub(super) fingerprint: String,
    pub(super) bucket: String,
    pub(super) deadline_at: Option<DateTime<Utc>>,
    pub(super) seen_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub(super) struct DeliveryItem {
    pub(super) source_id: String,
    pub(super) stable_id: String,
    pub(super) fingerprint: String,
}

#[derive(Debug, Clone)]
pub(super) struct PendingDelivery {
    pub(super) idempotency_key: String,
    pub(super) payload_json: JsonValue,
    pub(super) attempts: u32,
}

#[derive(Debug, Clone)]
pub(super) struct SourceState {
    pub(super) source_id: String,
    pub(super) provider: String,
    pub(super) last_success_at: Option<DateTime<Utc>>,
    pub(super) last_error: Option<String>,
    pub(super) last_error_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone)]
pub(super) struct RunState {
    pub(super) run_id: String,
    pub(super) mode: String,
    pub(super) started_at: DateTime<Utc>,
    pub(super) finished_at: DateTime<Utc>,
    pub(super) scan_status: String,
    pub(super) notification_status: String,
    pub(super) attention_item_count: usize,
    pub(super) source_success_count: usize,
    pub(super) source_failure_count: usize,
    pub(super) error: Option<String>,
}

#[derive(Debug, Clone)]
pub(super) struct RunRecord {
    pub(super) run_id: String,
    pub(super) mode: String,
    pub(super) started_at: DateTime<Utc>,
    pub(super) finished_at: DateTime<Utc>,
    pub(super) scan_status: String,
    pub(super) notification_status: String,
    pub(super) attention_item_count: usize,
    pub(super) source_success_count: usize,
    pub(super) source_failure_count: usize,
    pub(super) error: Option<String>,
}

impl AttentionStore {
    pub(super) async fn connect(database_url: &str) -> Result<Self> {
        if !matches!(
            database_url.split_once("://").map(|(scheme, _)| scheme),
            Some("postgres" | "postgresql")
        ) {
            bail!("email-attention database URL must use postgres:// or postgresql://");
        }

        let mut options = ConnectOptions::new(database_url.to_owned());
        options
            .max_connections(4)
            .min_connections(0)
            .connect_timeout(Duration::from_secs(10))
            .acquire_timeout(Duration::from_secs(10))
            .idle_timeout(Duration::from_secs(300))
            .sqlx_logging(false);
        let connection = SeaDatabase::connect(options)
            .await
            .context("failed to connect the email-attention PostgreSQL store")?;
        Ok(Self { connection })
    }

    pub(super) async fn verify_schema(&self) -> Result<()> {
        let row = self
            .connection
            .query_one(statement(
                r#"
                SELECT
                  to_regclass('ai_agent_coordinator.email_attention_sources') IS NOT NULL
                  AND to_regclass('ai_agent_coordinator.email_attention_items') IS NOT NULL
                  AND to_regclass('ai_agent_coordinator.email_attention_deliveries') IS NOT NULL
                  AND to_regclass('ai_agent_coordinator.email_attention_delivery_items') IS NOT NULL
                  AND to_regclass('ai_agent_coordinator.email_attention_runs') IS NOT NULL
                  AND to_regclass('ai_agent_coordinator.email_attention_leases') IS NOT NULL
                  AS ready
                "#,
                vec![],
            ))
            .await
            .context("failed to verify the email-attention schema")?
            .ok_or_else(|| anyhow!("email-attention schema verification returned no row"))?;
        let ready: bool = row.try_get("", "ready")?;
        if !ready {
            bail!(
                "email-attention is enabled but the canonical PostgreSQL schema has not been applied"
            );
        }
        Ok(())
    }

    pub(super) async fn ensure_source(
        &self,
        source_id: &str,
        provider: &str,
        at: DateTime<Utc>,
    ) -> Result<()> {
        self.connection
            .execute(statement(
                r#"
                INSERT INTO ai_agent_coordinator.email_attention_sources (
                    source_id, provider, updated_at
                ) VALUES ($1, $2, $3)
                ON CONFLICT (source_id) DO UPDATE SET
                    cursor = CASE
                        WHEN ai_agent_coordinator.email_attention_sources.provider = EXCLUDED.provider
                        THEN ai_agent_coordinator.email_attention_sources.cursor
                        ELSE NULL
                    END,
                    last_success_at = CASE
                        WHEN ai_agent_coordinator.email_attention_sources.provider = EXCLUDED.provider
                        THEN ai_agent_coordinator.email_attention_sources.last_success_at
                        ELSE NULL
                    END,
                    last_error = CASE
                        WHEN ai_agent_coordinator.email_attention_sources.provider = EXCLUDED.provider
                        THEN ai_agent_coordinator.email_attention_sources.last_error
                        ELSE NULL
                    END,
                    last_error_at = CASE
                        WHEN ai_agent_coordinator.email_attention_sources.provider = EXCLUDED.provider
                        THEN ai_agent_coordinator.email_attention_sources.last_error_at
                        ELSE NULL
                    END,
                    provider = EXCLUDED.provider,
                    updated_at = EXCLUDED.updated_at
                "#,
                vec![source_id.into(), provider.into(), at.into()],
            ))
            .await
            .context("failed to ensure email-attention source state")?;
        Ok(())
    }

    pub(super) async fn source_cursor(&self, source_id: &str) -> Result<Option<String>> {
        let row = self
            .connection
            .query_one(statement(
                r#"
                SELECT cursor
                  FROM ai_agent_coordinator.email_attention_sources
                 WHERE source_id = $1
                "#,
                vec![source_id.into()],
            ))
            .await
            .context("failed to read email-attention source cursor")?;
        row.map(|row| row.try_get("", "cursor"))
            .transpose()
            .map(|value: Option<Option<String>>| value.flatten())
            .context("failed to decode email-attention source cursor")
    }

    pub(super) async fn item_state(
        &self,
        source_id: &str,
        stable_id: &str,
    ) -> Result<Option<ItemState>> {
        let row = self
            .connection
            .query_one(statement(
                r#"
                SELECT last_emitted_fingerprint, last_emitted_at, pending_delivery_key
                  FROM ai_agent_coordinator.email_attention_items
                 WHERE source_id = $1 AND stable_id = $2
                "#,
                vec![source_id.into(), stable_id.into()],
            ))
            .await
            .context("failed to read email-attention item state")?;
        row.map(|row| -> Result<ItemState> {
            Ok(ItemState {
                last_emitted_fingerprint: row.try_get("", "last_emitted_fingerprint")?,
                last_emitted_at: row.try_get("", "last_emitted_at")?,
                pending_delivery_key: row.try_get("", "pending_delivery_key")?,
            })
        })
        .transpose()
        .context("failed to decode email-attention item state")
    }

    pub(super) async fn record_seen_item(&self, item: &SeenItem) -> Result<()> {
        self.connection
            .execute(statement(
                r#"
                INSERT INTO ai_agent_coordinator.email_attention_items (
                    source_id, stable_id, current_fingerprint, current_bucket,
                    deadline_at, last_seen_at
                ) VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (source_id, stable_id) DO UPDATE SET
                    current_fingerprint = EXCLUDED.current_fingerprint,
                    current_bucket = EXCLUDED.current_bucket,
                    deadline_at = EXCLUDED.deadline_at,
                    last_seen_at = EXCLUDED.last_seen_at
                "#,
                vec![
                    item.source_id.clone().into(),
                    item.stable_id.clone().into(),
                    item.fingerprint.clone().into(),
                    item.bucket.clone().into(),
                    item.deadline_at.into(),
                    item.seen_at.into(),
                ],
            ))
            .await
            .context("failed to record email-attention item")?;
        Ok(())
    }

    pub(super) async fn record_source_success(
        &self,
        source_id: &str,
        provider: &str,
        cursor: Option<&str>,
        at: DateTime<Utc>,
    ) -> Result<()> {
        self.connection
            .execute(statement(
                r#"
                INSERT INTO ai_agent_coordinator.email_attention_sources (
                    source_id, provider, cursor, last_success_at,
                    last_error, last_error_at, updated_at
                ) VALUES ($1, $2, $3, $4, NULL, NULL, $4)
                ON CONFLICT (source_id) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    cursor = COALESCE(
                        EXCLUDED.cursor,
                        ai_agent_coordinator.email_attention_sources.cursor
                    ),
                    last_success_at = EXCLUDED.last_success_at,
                    last_error = NULL,
                    last_error_at = NULL,
                    updated_at = EXCLUDED.updated_at
                "#,
                vec![
                    source_id.into(),
                    provider.into(),
                    cursor.map(str::to_owned).into(),
                    at.into(),
                ],
            ))
            .await
            .context("failed to record email-attention source success")?;
        Ok(())
    }

    pub(super) async fn record_source_failure(
        &self,
        source_id: &str,
        provider: &str,
        error: &str,
        at: DateTime<Utc>,
    ) -> Result<()> {
        let error = bounded_text(error, 512);
        self.connection
            .execute(statement(
                r#"
                INSERT INTO ai_agent_coordinator.email_attention_sources (
                    source_id, provider, last_error, last_error_at, updated_at
                ) VALUES ($1, $2, $3, $4, $4)
                ON CONFLICT (source_id) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    last_error = EXCLUDED.last_error,
                    last_error_at = EXCLUDED.last_error_at,
                    updated_at = EXCLUDED.updated_at
                "#,
                vec![source_id.into(), provider.into(), error.into(), at.into()],
            ))
            .await
            .context("failed to record email-attention source failure")?;
        Ok(())
    }
}

pub(super) fn statement(sql: &'static str, values: Vec<Value>) -> Statement {
    Statement::from_sql_and_values(DbBackend::Postgres, sql, values)
}

pub(super) fn bounded_text(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}
