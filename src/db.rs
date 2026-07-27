use std::{path::Path, sync::Arc, time::Duration};

use anyhow::{anyhow, Context, Result};
use chrono::{DateTime, Duration as ChronoDuration, Utc};
use parking_lot::Mutex;
use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::Value;
use uuid::Uuid;

use crate::{
    config::WorkerConfig,
    jobs::{
        ClaimJobRequest, CompleteJobRequest, CompletionOutcome, CreateJobRequest, Job, JobStatus,
    },
};

#[derive(Clone)]
pub struct Database {
    connection: Arc<Mutex<Connection>>,
}

#[derive(Debug, Clone)]
pub struct UsageRecord {
    pub request_id: String,
    pub org: String,
    pub repo: String,
    pub provider: String,
    pub model: String,
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub cost_usd: f64,
}

impl Database {
    pub fn open(path: &str) -> Result<Self> {
        if path != ":memory:" {
            if let Some(parent) = Path::new(path).parent() {
                std::fs::create_dir_all(parent).with_context(|| {
                    format!("failed to create database directory {}", parent.display())
                })?;
            }
        }

        let connection = Connection::open(path)
            .with_context(|| format!("failed to open SQLite database at {path}"))?;
        connection
            .busy_timeout(Duration::from_secs(5))
            .context("failed to configure SQLite busy timeout")?;
        connection
            .execute_batch(
                r#"
                PRAGMA foreign_keys = ON;
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = NORMAL;

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    org TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    idempotency_key TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    available_at_ms INTEGER NOT NULL,
                    claimed_by TEXT,
                    lease_expires_at_ms INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    result_json TEXT,
                    last_error TEXT,
                    budget_usd REAL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency_key_unique
                    ON jobs(idempotency_key)
                    WHERE idempotency_key IS NOT NULL;

                CREATE INDEX IF NOT EXISTS jobs_claim_idx
                    ON jobs(status, available_at_ms, priority DESC, created_at_ms ASC);

                CREATE INDEX IF NOT EXISTS jobs_repo_idx
                    ON jobs(org, repo, status, created_at_ms DESC);

                CREATE TABLE IF NOT EXISTS model_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    org TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    cost_usd REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS model_usage_org_time_idx
                    ON model_usage(org, created_at_ms);

                CREATE INDEX IF NOT EXISTS model_usage_repo_time_idx
                    ON model_usage(org, repo, created_at_ms);
                "#,
            )
            .context("failed to initialize database schema")?;

        Ok(Self {
            connection: Arc::new(Mutex::new(connection)),
        })
    }

    pub fn ready(&self) -> Result<()> {
        let connection = self.connection.lock();
        let value: i64 = connection.query_row("SELECT 1", [], |row| row.get(0))?;
        if value != 1 {
            return Err(anyhow!("database readiness query returned an unexpected value"));
        }
        Ok(())
    }

    pub fn create_job(
        &self,
        request: &CreateJobRequest,
        idempotency_key: Option<&str>,
    ) -> Result<Job> {
        request.validate().map_err(anyhow::Error::msg)?;
        let mut connection = self.connection.lock();
        let transaction = connection.transaction()?;

        if let Some(key) = idempotency_key {
            let existing_id: Option<String> = transaction
                .query_row(
                    "SELECT id FROM jobs WHERE idempotency_key = ?1",
                    params![key],
                    |row| row.get(0),
                )
                .optional()?;
            if let Some(existing_id) = existing_id {
                let existing = query_job(&transaction, &existing_id)?
                    .ok_or_else(|| anyhow!("idempotent job disappeared during transaction"))?;
                transaction.commit()?;
                return Ok(existing);
            }
        }

        let now = Utc::now();
        let available_at = request.available_at.unwrap_or(now);
        let id = Uuid::new_v4().to_string();
        let payload_json = serde_json::to_string(&request.payload)?;

        transaction.execute(
            r#"
            INSERT INTO jobs (
                id, org, repo, task_type, payload_json, priority, status,
                idempotency_key, created_at_ms, updated_at_ms, available_at_ms,
                attempts, max_attempts, budget_usd
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'queued', ?7, ?8, ?8, ?9, 0, ?10, ?11)
            "#,
            params![
                id,
                request.org,
                request.repo,
                request.task_type,
                payload_json,
                request.priority,
                idempotency_key,
                now.timestamp_millis(),
                available_at.timestamp_millis(),
                request.max_attempts,
                request.budget_usd,
            ],
        )?;

        let job = query_job(&transaction, &id)?
            .ok_or_else(|| anyhow!("newly inserted job could not be read"))?;
        transaction.commit()?;
        Ok(job)
    }

    pub fn get_job(&self, id: &str) -> Result<Option<Job>> {
        let connection = self.connection.lock();
        query_job(&connection, id)
    }

    pub fn claim_job(
        &self,
        request: &ClaimJobRequest,
        worker_config: &WorkerConfig,
    ) -> Result<Option<Job>> {
        request.validate().map_err(anyhow::Error::msg)?;
        let mut connection = self.connection.lock();
        let transaction = connection.transaction()?;
        let now = Utc::now();
        let now_ms = now.timestamp_millis();

        transaction.execute(
            r#"
            UPDATE jobs
               SET status = 'queued',
                   claimed_by = NULL,
                   lease_expires_at_ms = NULL,
                   updated_at_ms = ?1
             WHERE status = 'running'
               AND lease_expires_at_ms IS NOT NULL
               AND lease_expires_at_ms < ?1
               AND attempts < max_attempts
            "#,
            params![now_ms],
        )?;

        transaction.execute(
            r#"
            UPDATE jobs
               SET status = 'failed',
                   last_error = COALESCE(last_error, 'worker lease expired after final attempt'),
                   claimed_by = NULL,
                   lease_expires_at_ms = NULL,
                   updated_at_ms = ?1
             WHERE status = 'running'
               AND lease_expires_at_ms IS NOT NULL
               AND lease_expires_at_ms < ?1
               AND attempts >= max_attempts
            "#,
            params![now_ms],
        )?;

        let candidate_ids = {
            let mut statement = transaction.prepare(
                r#"
                SELECT id
                  FROM jobs
                 WHERE status = 'queued'
                   AND available_at_ms <= ?1
                   AND attempts < max_attempts
                 ORDER BY priority DESC, created_at_ms ASC
                 LIMIT 200
                "#,
            )?;
            let rows = statement.query_map(params![now_ms], |row| row.get::<_, String>(0))?;
            rows.collect::<rusqlite::Result<Vec<_>>>()?
        };

        for candidate_id in candidate_ids {
            let Some(candidate) = query_job(&transaction, &candidate_id)? else {
                continue;
            };
            if !request.accepts(&candidate) {
                continue;
            }

            let org_running: i64 = transaction.query_row(
                "SELECT COUNT(*) FROM jobs WHERE status = 'running' AND org = ?1",
                params![candidate.org],
                |row| row.get(0),
            )?;
            if org_running >= worker_config.org_limit(&candidate.org) as i64 {
                continue;
            }

            let repo_running: i64 = transaction.query_row(
                "SELECT COUNT(*) FROM jobs WHERE status = 'running' AND org = ?1 AND repo = ?2",
                params![candidate.org, candidate.repo],
                |row| row.get(0),
            )?;
            if repo_running >= worker_config.repo_limit(&candidate.org, &candidate.repo) as i64 {
                continue;
            }

            let lease_expires_at = now + ChronoDuration::seconds(request.lease_seconds);
            let updated = transaction.execute(
                r#"
                UPDATE jobs
                   SET status = 'running',
                       claimed_by = ?1,
                       lease_expires_at_ms = ?2,
                       attempts = attempts + 1,
                       updated_at_ms = ?3
                 WHERE id = ?4
                   AND status = 'queued'
                "#,
                params![
                    request.worker_id,
                    lease_expires_at.timestamp_millis(),
                    now_ms,
                    candidate_id,
                ],
            )?;
            if updated == 1 {
                let claimed = query_job(&transaction, &candidate_id)?
                    .ok_or_else(|| anyhow!("claimed job could not be read"))?;
                transaction.commit()?;
                return Ok(Some(claimed));
            }
        }

        transaction.commit()?;
        Ok(None)
    }

    pub fn heartbeat_job(&self, id: &str, worker_id: &str, lease_seconds: i64) -> Result<Job> {
        if !(15..=3600).contains(&lease_seconds) {
            return Err(anyhow!("lease_seconds must be between 15 and 3600"));
        }
        let now = Utc::now();
        let lease_expires_at = now + ChronoDuration::seconds(lease_seconds);
        let connection = self.connection.lock();
        let updated = connection.execute(
            r#"
            UPDATE jobs
               SET lease_expires_at_ms = ?1,
                   updated_at_ms = ?2
             WHERE id = ?3
               AND status = 'running'
               AND claimed_by = ?4
            "#,
            params![
                lease_expires_at.timestamp_millis(),
                now.timestamp_millis(),
                id,
                worker_id,
            ],
        )?;
        if updated != 1 {
            return Err(anyhow!(
                "job is not running, does not exist, or is leased by another worker"
            ));
        }
        query_job(&connection, id)?.ok_or_else(|| anyhow!("updated job could not be read"))
    }

    pub fn complete_job(&self, id: &str, request: &CompleteJobRequest) -> Result<Job> {
        let mut connection = self.connection.lock();
        let transaction = connection.transaction()?;
        let job = query_job(&transaction, id)?.ok_or_else(|| anyhow!("job not found"))?;

        if job.status != JobStatus::Running {
            return Err(anyhow!("job is not running"));
        }
        if job.claimed_by.as_deref() != Some(request.worker_id.as_str()) {
            return Err(anyhow!("job is leased by another worker"));
        }

        let now = Utc::now();
        let result_json = request
            .result
            .as_ref()
            .map(serde_json::to_string)
            .transpose()?;

        match request.outcome {
            CompletionOutcome::Succeeded => {
                transaction.execute(
                    r#"
                    UPDATE jobs
                       SET status = 'succeeded',
                           result_json = ?1,
                           last_error = NULL,
                           claimed_by = NULL,
                           lease_expires_at_ms = NULL,
                           updated_at_ms = ?2
                     WHERE id = ?3
                    "#,
                    params![result_json, now.timestamp_millis(), id],
                )?;
            }
            CompletionOutcome::Failed
                if request.retryable && job.attempts < job.max_attempts =>
            {
                let delay = request.retry_delay_seconds.clamp(0, 86_400);
                let available_at = now + ChronoDuration::seconds(delay);
                transaction.execute(
                    r#"
                    UPDATE jobs
                       SET status = 'queued',
                           result_json = ?1,
                           last_error = ?2,
                           claimed_by = NULL,
                           lease_expires_at_ms = NULL,
                           available_at_ms = ?3,
                           updated_at_ms = ?4
                     WHERE id = ?5
                    "#,
                    params![
                        result_json,
                        request.error,
                        available_at.timestamp_millis(),
                        now.timestamp_millis(),
                        id,
                    ],
                )?;
            }
            CompletionOutcome::Failed => {
                transaction.execute(
                    r#"
                    UPDATE jobs
                       SET status = 'failed',
                           result_json = ?1,
                           last_error = ?2,
                           claimed_by = NULL,
                           lease_expires_at_ms = NULL,
                           updated_at_ms = ?3
                     WHERE id = ?4
                    "#,
                    params![result_json, request.error, now.timestamp_millis(), id],
                )?;
            }
        }

        let updated = query_job(&transaction, id)?
            .ok_or_else(|| anyhow!("completed job could not be read"))?;
        transaction.commit()?;
        Ok(updated)
    }

    pub fn cancel_job(&self, id: &str) -> Result<Job> {
        let connection = self.connection.lock();
        let now_ms = Utc::now().timestamp_millis();
        let updated = connection.execute(
            r#"
            UPDATE jobs
               SET status = 'cancelled',
                   claimed_by = NULL,
                   lease_expires_at_ms = NULL,
                   updated_at_ms = ?1
             WHERE id = ?2
               AND status IN ('queued', 'running')
            "#,
            params![now_ms, id],
        )?;
        if updated != 1 {
            return Err(anyhow!("job cannot be cancelled or does not exist"));
        }
        query_job(&connection, id)?.ok_or_else(|| anyhow!("cancelled job could not be read"))
    }

    pub fn record_usage(&self, record: &UsageRecord) -> Result<()> {
        let connection = self.connection.lock();
        connection.execute(
            r#"
            INSERT INTO model_usage (
                request_id, created_at_ms, org, repo, provider, model,
                prompt_tokens, completion_tokens, cost_usd
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
            "#,
            params![
                record.request_id,
                Utc::now().timestamp_millis(),
                record.org,
                record.repo,
                record.provider,
                record.model,
                record.prompt_tokens as i64,
                record.completion_tokens as i64,
                record.cost_usd,
            ],
        )?;
        Ok(())
    }

    pub fn org_usage_today_usd(&self, org: &str) -> Result<f64> {
        let start_ms = start_of_utc_day().timestamp_millis();
        let connection = self.connection.lock();
        let value: f64 = connection.query_row(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM model_usage WHERE org = ?1 AND created_at_ms >= ?2",
            params![org, start_ms],
            |row| row.get(0),
        )?;
        Ok(value)
    }

    pub fn repo_usage_today_usd(&self, org: &str, repo: &str) -> Result<f64> {
        let start_ms = start_of_utc_day().timestamp_millis();
        let connection = self.connection.lock();
        let value: f64 = connection.query_row(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM model_usage WHERE org = ?1 AND repo = ?2 AND created_at_ms >= ?3",
            params![org, repo, start_ms],
            |row| row.get(0),
        )?;
        Ok(value)
    }
}

fn start_of_utc_day() -> DateTime<Utc> {
    Utc::now()
        .date_naive()
        .and_hms_opt(0, 0, 0)
        .expect("midnight is always valid")
        .and_utc()
}

fn query_job(connection: &Connection, id: &str) -> Result<Option<Job>> {
    connection
        .query_row(
            r#"
            SELECT id, org, repo, task_type, payload_json, priority, status,
                   created_at_ms, updated_at_ms, available_at_ms, claimed_by,
                   lease_expires_at_ms, attempts, max_attempts, result_json,
                   last_error, budget_usd
              FROM jobs
             WHERE id = ?1
            "#,
            params![id],
            row_to_job,
        )
        .optional()
        .map_err(Into::into)
}

fn row_to_job(row: &Row<'_>) -> rusqlite::Result<Job> {
    let payload_json: String = row.get(4)?;
    let status_string: String = row.get(6)?;
    let created_at_ms: i64 = row.get(7)?;
    let updated_at_ms: i64 = row.get(8)?;
    let available_at_ms: i64 = row.get(9)?;
    let lease_expires_at_ms: Option<i64> = row.get(11)?;
    let result_json: Option<String> = row.get(14)?;

    Ok(Job {
        id: row.get(0)?,
        org: row.get(1)?,
        repo: row.get(2)?,
        task_type: row.get(3)?,
        payload: parse_json_column(4, &payload_json)?,
        priority: row.get(5)?,
        status: JobStatus::parse(&status_string).ok_or_else(|| {
            rusqlite::Error::FromSqlConversionFailure(
                6,
                rusqlite::types::Type::Text,
                std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    format!("invalid job status {status_string:?}"),
                )
                .into(),
            )
        })?,
        created_at: timestamp_from_millis(7, created_at_ms)?,
        updated_at: timestamp_from_millis(8, updated_at_ms)?,
        available_at: timestamp_from_millis(9, available_at_ms)?,
        claimed_by: row.get(10)?,
        lease_expires_at: lease_expires_at_ms
            .map(|value| timestamp_from_millis(11, value))
            .transpose()?,
        attempts: row.get(12)?,
        max_attempts: row.get(13)?,
        result: result_json
            .as_deref()
            .map(|value| parse_json_column(14, value))
            .transpose()?,
        last_error: row.get(15)?,
        budget_usd: row.get(16)?,
    })
}

fn parse_json_column(index: usize, value: &str) -> rusqlite::Result<Value> {
    serde_json::from_str(value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(
            index,
            rusqlite::types::Type::Text,
            Box::new(error),
        )
    })
}

fn timestamp_from_millis(index: usize, value: i64) -> rusqlite::Result<DateTime<Utc>> {
    DateTime::<Utc>::from_timestamp_millis(value).ok_or_else(|| {
        rusqlite::Error::FromSqlConversionFailure(
            index,
            rusqlite::types::Type::Integer,
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("timestamp {value} is out of range"),
            )
            .into(),
        )
    })
}
