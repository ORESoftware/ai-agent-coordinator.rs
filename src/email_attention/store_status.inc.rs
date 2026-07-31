    pub(super) fn pending_delivery_count(&self) -> Result<usize> {
        let connection = self.connection.lock();
        let count: i64 = connection.query_row(
            "SELECT COUNT(*) FROM email_attention_deliveries WHERE status = 'pending'",
            [],
            |row| row.get(0),
        )?;
        Ok(count.max(0) as usize)
    }

    pub(super) fn source_states(&self) -> Result<Vec<SourceState>> {
        let connection = self.connection.lock();
        let mut statement = connection.prepare(
            r#"
            SELECT source_id, provider, last_success_at_ms, last_error, last_error_at_ms
              FROM email_attention_sources
             ORDER BY source_id ASC
            "#,
        )?;
        let rows = statement.query_map([], |row| {
            Ok(SourceState {
                source_id: row.get(0)?,
                provider: row.get(1)?,
                last_success_at: optional_timestamp(row.get(2)?)?,
                last_error: row.get(3)?,
                last_error_at: optional_timestamp(row.get(4)?)?,
            })
        })?;
        rows.collect::<rusqlite::Result<Vec<_>>>()
            .context("failed to list email-attention source states")
    }

    pub(super) fn last_run(&self) -> Result<Option<RunState>> {
        self.query_run(
            r#"
            SELECT run_id, mode, started_at_ms, finished_at_ms, scan_status,
                   notification_status, attention_item_count,
                   source_success_count, source_failure_count, error
              FROM email_attention_runs
             ORDER BY finished_at_ms DESC
             LIMIT 1
            "#,
        )
    }

    pub(super) fn last_successful_scheduled_run_at(&self) -> Result<Option<DateTime<Utc>>> {
        let connection = self.connection.lock();
        let timestamp: Option<i64> = connection
            .query_row(
                r#"
                SELECT finished_at_ms
                  FROM email_attention_runs
                 WHERE mode = 'scheduled'
                   AND scan_status IN ('success', 'partial')
                 ORDER BY finished_at_ms DESC
                 LIMIT 1
                "#,
                [],
                |row| row.get(0),
            )
            .optional()?;
        timestamp
            .map(timestamp_from_millis)
            .transpose()
            .context("failed to decode last successful email-attention run")
    }

    pub(super) fn record_run(&self, run: &RunRecord) -> Result<()> {
        let error = run.error.as_deref().map(|value| bounded_text(value, 512));
        let connection = self.connection.lock();
        connection
            .execute(
                r#"
                INSERT INTO email_attention_runs (
                    run_id, mode, started_at_ms, finished_at_ms, scan_status,
                    notification_status, attention_item_count,
                    source_success_count, source_failure_count, error
                ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
                "#,
                params![
                    run.run_id,
                    run.mode,
                    run.started_at.timestamp_millis(),
                    run.finished_at.timestamp_millis(),
                    run.scan_status,
                    run.notification_status,
                    run.attention_item_count as i64,
                    run.source_success_count as i64,
                    run.source_failure_count as i64,
                    error,
                ],
            )
            .context("failed to record email-attention run")?;
        Ok(())
    }

    pub(super) fn try_acquire_lease(
        &self,
        name: &str,
        holder: &str,
        now: DateTime<Utc>,
        expires_at: DateTime<Utc>,
    ) -> Result<bool> {
        let connection = self.connection.lock();
        let changed = connection.execute(
            r#"
            INSERT INTO email_attention_leases (
                name, holder, expires_at_ms, updated_at_ms
            ) VALUES (?1, ?2, ?3, ?4)
            ON CONFLICT(name) DO UPDATE SET
                holder = excluded.holder,
                expires_at_ms = excluded.expires_at_ms,
                updated_at_ms = excluded.updated_at_ms
            WHERE email_attention_leases.expires_at_ms <= ?4
               OR email_attention_leases.holder = excluded.holder
            "#,
            params![
                name,
                holder,
                expires_at.timestamp_millis(),
                now.timestamp_millis(),
            ],
        )?;
        Ok(changed == 1)
    }

    fn query_run(&self, sql: &str) -> Result<Option<RunState>> {
        let connection = self.connection.lock();
        connection
            .query_row(sql, [], |row| {
                let attention_item_count: i64 = row.get(6)?;
                let source_success_count: i64 = row.get(7)?;
                let source_failure_count: i64 = row.get(8)?;
                Ok(RunState {
                    run_id: row.get(0)?,
                    mode: row.get(1)?,
                    started_at: timestamp_from_millis(row.get(2)?)?,
                    finished_at: timestamp_from_millis(row.get(3)?)?,
                    scan_status: row.get(4)?,
                    notification_status: row.get(5)?,
                    attention_item_count: attention_item_count.max(0) as usize,
                    source_success_count: source_success_count.max(0) as usize,
                    source_failure_count: source_failure_count.max(0) as usize,
                    error: row.get(9)?,
                })
            })
            .optional()
            .context("failed to read email-attention run state")
    }
