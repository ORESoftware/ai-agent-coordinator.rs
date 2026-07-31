    pub(super) fn create_pending_delivery(
        &self,
        idempotency_key: &str,
        payload_json: &str,
        items: &[DeliveryItem],
        at: DateTime<Utc>,
    ) -> Result<()> {
        if items.is_empty() {
            return Err(anyhow!("cannot create an empty email-attention delivery"));
        }

        let mut connection = self.connection.lock();
        let transaction = connection.transaction()?;
        let inserted = transaction.execute(
            r#"
            INSERT OR IGNORE INTO email_attention_deliveries (
                idempotency_key, payload_json, status, attempts,
                created_at_ms, updated_at_ms
            ) VALUES (?1, ?2, 'pending', 0, ?3, ?3)
            "#,
            params![idempotency_key, payload_json, at.timestamp_millis()],
        )?;

        if inserted == 0 {
            let existing_payload: String = transaction.query_row(
                "SELECT payload_json FROM email_attention_deliveries WHERE idempotency_key = ?1",
                params![idempotency_key],
                |row| row.get(0),
            )?;
            if existing_payload != payload_json {
                return Err(anyhow!(
                    "email-attention idempotency key collision with different payload"
                ));
            }
        }

        for item in items {
            transaction.execute(
                r#"
                INSERT OR IGNORE INTO email_attention_delivery_items (
                    idempotency_key, source_id, stable_id, fingerprint
                ) VALUES (?1, ?2, ?3, ?4)
                "#,
                params![
                    idempotency_key,
                    item.source_id,
                    item.stable_id,
                    item.fingerprint,
                ],
            )?;
            let updated = transaction.execute(
                r#"
                UPDATE email_attention_items
                   SET pending_delivery_key = ?1
                 WHERE source_id = ?2
                   AND stable_id = ?3
                   AND (pending_delivery_key IS NULL OR pending_delivery_key = ?1)
                "#,
                params![idempotency_key, item.source_id, item.stable_id],
            )?;
            if updated != 1 {
                return Err(anyhow!(
                    "email-attention item already belongs to another pending delivery"
                ));
            }
        }

        transaction.commit()?;
        Ok(())
    }

    pub(super) fn pending_deliveries(&self, limit: usize) -> Result<Vec<PendingDelivery>> {
        let connection = self.connection.lock();
        let mut statement = connection.prepare(
            r#"
            SELECT idempotency_key, payload_json, attempts
              FROM email_attention_deliveries
             WHERE status = 'pending'
             ORDER BY created_at_ms ASC
             LIMIT ?1
            "#,
        )?;
        let rows = statement.query_map(params![limit as i64], |row| {
            let attempts: i64 = row.get(2)?;
            Ok(PendingDelivery {
                idempotency_key: row.get(0)?,
                payload_json: row.get(1)?,
                attempts: attempts.max(0) as u32,
            })
        })?;
        rows.collect::<rusqlite::Result<Vec<_>>>()
            .context("failed to list pending email-attention deliveries")
    }

    pub(super) fn mark_delivery_failure(
        &self,
        idempotency_key: &str,
        error: &str,
        at: DateTime<Utc>,
    ) -> Result<()> {
        let error = bounded_text(error, 512);
        let connection = self.connection.lock();
        let updated = connection.execute(
            r#"
            UPDATE email_attention_deliveries
               SET attempts = attempts + 1,
                   updated_at_ms = ?1,
                   last_error = ?2
             WHERE idempotency_key = ?3
               AND status = 'pending'
            "#,
            params![at.timestamp_millis(), error, idempotency_key],
        )?;
        if updated != 1 {
            return Err(anyhow!(
                "pending email-attention delivery does not exist"
            ));
        }
        Ok(())
    }

    pub(super) fn mark_delivery_success(
        &self,
        idempotency_key: &str,
        at: DateTime<Utc>,
    ) -> Result<()> {
        let mut connection = self.connection.lock();
        let transaction = connection.transaction()?;
        let status: Option<String> = transaction
            .query_row(
                "SELECT status FROM email_attention_deliveries WHERE idempotency_key = ?1",
                params![idempotency_key],
                |row| row.get(0),
            )
            .optional()?;
        match status.as_deref() {
            Some("delivered") => {
                transaction.commit()?;
                return Ok(());
            }
            Some("pending") => {}
            Some(other) => {
                return Err(anyhow!(
                    "email-attention delivery has unsupported status {other:?}"
                ));
            }
            None => {
                return Err(anyhow!("email-attention delivery does not exist"));
            }
        }

        let delivery_items = {
            let mut statement = transaction.prepare(
                r#"
                SELECT source_id, stable_id, fingerprint
                  FROM email_attention_delivery_items
                 WHERE idempotency_key = ?1
                "#,
            )?;
            let rows = statement.query_map(params![idempotency_key], |row| {
                Ok(DeliveryItem {
                    source_id: row.get(0)?,
                    stable_id: row.get(1)?,
                    fingerprint: row.get(2)?,
                })
            })?;
            rows.collect::<rusqlite::Result<Vec<_>>>()?
        };

        transaction.execute(
            r#"
            UPDATE email_attention_deliveries
               SET status = 'delivered',
                   attempts = attempts + 1,
                   payload_json = '{"redacted":true}',
                   updated_at_ms = ?1,
                   delivered_at_ms = ?1,
                   last_error = NULL
             WHERE idempotency_key = ?2
            "#,
            params![at.timestamp_millis(), idempotency_key],
        )?;

        for item in delivery_items {
            transaction.execute(
                r#"
                UPDATE email_attention_items
                   SET last_emitted_fingerprint = ?1,
                       last_emitted_at_ms = ?2,
                       pending_delivery_key = CASE
                           WHEN pending_delivery_key = ?3 THEN NULL
                           ELSE pending_delivery_key
                       END
                 WHERE source_id = ?4 AND stable_id = ?5
                "#,
                params![
                    item.fingerprint,
                    at.timestamp_millis(),
                    idempotency_key,
                    item.source_id,
                    item.stable_id,
                ],
            )?;
        }

        transaction.commit()?;
        Ok(())
    }

    #[cfg(test)]
    fn delivery_payload(&self, idempotency_key: &str) -> Result<Option<String>> {
        let connection = self.connection.lock();
        connection
            .query_row(
                "SELECT payload_json FROM email_attention_deliveries WHERE idempotency_key = ?1",
                params![idempotency_key],
                |row| row.get(0),
            )
            .optional()
            .context("failed to read email-attention delivery payload")
    }

