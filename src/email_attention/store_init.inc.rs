    pub(super) fn open(path: &str) -> Result<Self> {
        if path != ":memory:" {
            if let Some(parent) = Path::new(path)
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
            {
                std::fs::create_dir_all(parent).with_context(|| {
                    format!(
                        "failed to create email-attention database directory {}",
                        parent.display()
                    )
                })?;
            }
        }

        let connection = Connection::open(path)
            .with_context(|| format!("failed to open email-attention SQLite database at {path}"))?;
        connection
            .busy_timeout(Duration::from_secs(5))
            .context("failed to configure email-attention SQLite busy timeout")?;
        connection
            .execute_batch(
                r#"
                PRAGMA foreign_keys = ON;
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = NORMAL;

                CREATE TABLE IF NOT EXISTS email_attention_sources (
                    source_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    cursor TEXT,
                    last_success_at_ms INTEGER,
                    last_error TEXT,
                    last_error_at_ms INTEGER,
                    updated_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS email_attention_items (
                    source_id TEXT NOT NULL,
                    stable_id TEXT NOT NULL,
                    current_fingerprint TEXT NOT NULL,
                    current_bucket TEXT NOT NULL,
                    deadline_at_ms INTEGER,
                    last_seen_at_ms INTEGER NOT NULL,
                    last_emitted_fingerprint TEXT,
                    last_emitted_at_ms INTEGER,
                    pending_delivery_key TEXT,
                    PRIMARY KEY (source_id, stable_id)
                );

                CREATE INDEX IF NOT EXISTS email_attention_items_pending_idx
                    ON email_attention_items(pending_delivery_key)
                    WHERE pending_delivery_key IS NOT NULL;

                CREATE TABLE IF NOT EXISTS email_attention_deliveries (
                    idempotency_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    delivered_at_ms INTEGER,
                    last_error TEXT
                );

                CREATE INDEX IF NOT EXISTS email_attention_deliveries_pending_idx
                    ON email_attention_deliveries(status, created_at_ms);

                CREATE TABLE IF NOT EXISTS email_attention_delivery_items (
                    idempotency_key TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    stable_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    PRIMARY KEY (idempotency_key, source_id, stable_id),
                    FOREIGN KEY (idempotency_key)
                        REFERENCES email_attention_deliveries(idempotency_key)
                        ON DELETE CASCADE,
                    FOREIGN KEY (source_id, stable_id)
                        REFERENCES email_attention_items(source_id, stable_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS email_attention_runs (
                    run_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    started_at_ms INTEGER NOT NULL,
                    finished_at_ms INTEGER NOT NULL,
                    scan_status TEXT NOT NULL,
                    notification_status TEXT NOT NULL,
                    attention_item_count INTEGER NOT NULL,
                    source_success_count INTEGER NOT NULL,
                    source_failure_count INTEGER NOT NULL,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS email_attention_runs_finished_idx
                    ON email_attention_runs(finished_at_ms DESC);

                CREATE TABLE IF NOT EXISTS email_attention_leases (
                    name TEXT PRIMARY KEY,
                    holder TEXT NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                "#,
            )
            .context("failed to initialize email-attention database schema")?;

        Ok(Self {
            connection: Arc::new(Mutex::new(connection)),
        })
    }

    pub(super) fn source_cursor(&self, source_id: &str) -> Result<Option<String>> {
        let connection = self.connection.lock();
        connection
            .query_row(
                "SELECT cursor FROM email_attention_sources WHERE source_id = ?1",
                params![source_id],
                |row| row.get(0),
            )
            .optional()
            .map(|value| value.flatten())
            .context("failed to read email-attention source cursor")
    }

    pub(super) fn item_state(
        &self,
        source_id: &str,
        stable_id: &str,
    ) -> Result<Option<ItemState>> {
        let connection = self.connection.lock();
        connection
            .query_row(
                r#"
                SELECT last_emitted_fingerprint, last_emitted_at_ms, pending_delivery_key
                  FROM email_attention_items
                 WHERE source_id = ?1 AND stable_id = ?2
                "#,
                params![source_id, stable_id],
                |row| {
                    Ok(ItemState {
                        last_emitted_fingerprint: row.get(0)?,
                        last_emitted_at: optional_timestamp(row.get(1)?)?,
                        pending_delivery_key: row.get(2)?,
                    })
                },
            )
            .optional()
            .context("failed to read email-attention item state")
    }

    pub(super) fn record_seen_item(&self, item: &SeenItem) -> Result<()> {
        let connection = self.connection.lock();
        connection
            .execute(
                r#"
                INSERT INTO email_attention_items (
                    source_id, stable_id, current_fingerprint, current_bucket,
                    deadline_at_ms, last_seen_at_ms
                ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
                ON CONFLICT(source_id, stable_id) DO UPDATE SET
                    current_fingerprint = excluded.current_fingerprint,
                    current_bucket = excluded.current_bucket,
                    deadline_at_ms = excluded.deadline_at_ms,
                    last_seen_at_ms = excluded.last_seen_at_ms
                "#,
                params![
                    item.source_id,
                    item.stable_id,
                    item.fingerprint,
                    item.bucket,
                    item.deadline_at.map(|value| value.timestamp_millis()),
                    item.seen_at.timestamp_millis(),
                ],
            )
            .context("failed to record email-attention item")?;
        Ok(())
    }

    pub(super) fn record_source_success(
        &self,
        source_id: &str,
        provider: &str,
        cursor: Option<&str>,
        at: DateTime<Utc>,
    ) -> Result<()> {
        let connection = self.connection.lock();
        connection
            .execute(
                r#"
                INSERT INTO email_attention_sources (
                    source_id, provider, cursor, last_success_at_ms,
                    last_error, last_error_at_ms, updated_at_ms
                ) VALUES (?1, ?2, ?3, ?4, NULL, NULL, ?4)
                ON CONFLICT(source_id) DO UPDATE SET
                    provider = excluded.provider,
                    cursor = COALESCE(excluded.cursor, email_attention_sources.cursor),
                    last_success_at_ms = excluded.last_success_at_ms,
                    last_error = NULL,
                    last_error_at_ms = NULL,
                    updated_at_ms = excluded.updated_at_ms
                "#,
                params![source_id, provider, cursor, at.timestamp_millis()],
            )
            .context("failed to record email-attention source success")?;
        Ok(())
    }

    pub(super) fn record_source_failure(
        &self,
        source_id: &str,
        provider: &str,
        error: &str,
        at: DateTime<Utc>,
    ) -> Result<()> {
        let error = bounded_text(error, 512);
        let connection = self.connection.lock();
        connection
            .execute(
                r#"
                INSERT INTO email_attention_sources (
                    source_id, provider, cursor, last_success_at_ms,
                    last_error, last_error_at_ms, updated_at_ms
                ) VALUES (?1, ?2, NULL, NULL, ?3, ?4, ?4)
                ON CONFLICT(source_id) DO UPDATE SET
                    provider = excluded.provider,
                    last_error = excluded.last_error,
                    last_error_at_ms = excluded.last_error_at_ms,
                    updated_at_ms = excluded.updated_at_ms
                "#,
                params![source_id, provider, error, at.timestamp_millis()],
            )
            .context("failed to record email-attention source failure")?;
        Ok(())
    }

