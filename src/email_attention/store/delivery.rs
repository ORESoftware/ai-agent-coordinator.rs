use anyhow::{anyhow, Context, Result};
use chrono::{DateTime, Utc};
use sea_orm::{ConnectionTrait, TransactionTrait};
use serde_json::Value as JsonValue;

use super::{statement, AttentionStore, DeliveryItem, PendingDelivery};

impl AttentionStore {
    pub(in crate::email_attention) async fn create_pending_delivery(
        &self,
        idempotency_key: &str,
        payload_json: &JsonValue,
        items: &[DeliveryItem],
        at: DateTime<Utc>,
    ) -> Result<()> {
        if items.is_empty() {
            return Err(anyhow!("cannot create an empty email-attention delivery"));
        }

        let transaction = self
            .connection
            .begin()
            .await
            .context("failed to start the email-attention delivery transaction")?;
        let inserted = transaction
            .execute(statement(
                r#"
                INSERT INTO ai_agent_coordinator.email_attention_deliveries (
                    idempotency_key, payload_json, status, attempts,
                    created_at, updated_at
                ) VALUES ($1, $2, 'pending', 0, $3, $3)
                ON CONFLICT (idempotency_key) DO NOTHING
                "#,
                vec![
                    idempotency_key.into(),
                    payload_json.clone().into(),
                    at.into(),
                ],
            ))
            .await
            .context("failed to create the email-attention delivery")?
            .rows_affected();

        if inserted == 0 {
            let row = transaction
                .query_one(statement(
                    r#"
                    SELECT payload_json
                      FROM ai_agent_coordinator.email_attention_deliveries
                     WHERE idempotency_key = $1
                    "#,
                    vec![idempotency_key.into()],
                ))
                .await
                .context("failed to read the existing email-attention delivery payload")?
                .ok_or_else(|| anyhow!("email-attention delivery disappeared during creation"))?;
            let existing_payload: JsonValue = row.try_get("", "payload_json")?;
            if &existing_payload != payload_json {
                return Err(anyhow!(
                    "email-attention idempotency key collision with different payload"
                ));
            }
        }

        for item in items {
            transaction
                .execute(statement(
                    r#"
                    INSERT INTO ai_agent_coordinator.email_attention_delivery_items (
                        idempotency_key, source_id, stable_id, fingerprint
                    ) VALUES ($1, $2, $3, $4)
                    ON CONFLICT (idempotency_key, source_id, stable_id) DO NOTHING
                    "#,
                    vec![
                        idempotency_key.into(),
                        item.source_id.clone().into(),
                        item.stable_id.clone().into(),
                        item.fingerprint.clone().into(),
                    ],
                ))
                .await
                .context("failed to record an email-attention delivery item")?;
            let updated = transaction
                .execute(statement(
                    r#"
                    UPDATE ai_agent_coordinator.email_attention_items
                       SET pending_delivery_key = $1
                     WHERE source_id = $2
                       AND stable_id = $3
                       AND (pending_delivery_key IS NULL OR pending_delivery_key = $1)
                    "#,
                    vec![
                        idempotency_key.into(),
                        item.source_id.clone().into(),
                        item.stable_id.clone().into(),
                    ],
                ))
                .await
                .context("failed to attach an email-attention item to the delivery")?
                .rows_affected();
            if updated != 1 {
                return Err(anyhow!(
                    "email-attention item already belongs to another pending delivery"
                ));
            }
        }

        transaction
            .commit()
            .await
            .context("failed to commit the email-attention delivery")?;
        Ok(())
    }

    pub(in crate::email_attention) async fn pending_deliveries(
        &self,
        limit: usize,
    ) -> Result<Vec<PendingDelivery>> {
        let rows = self
            .connection
            .query_all(statement(
                r#"
                SELECT idempotency_key, payload_json, attempts::bigint AS attempts
                  FROM ai_agent_coordinator.email_attention_deliveries
                 WHERE status = 'pending'
                 ORDER BY created_at ASC
                 LIMIT $1
                "#,
                vec![(limit as i64).into()],
            ))
            .await
            .context("failed to list pending email-attention deliveries")?;
        rows.into_iter()
            .map(|row| {
                let attempts: i64 = row.try_get("", "attempts")?;
                Ok(PendingDelivery {
                    idempotency_key: row.try_get("", "idempotency_key")?,
                    payload_json: row.try_get("", "payload_json")?,
                    attempts: attempts.max(0) as u32,
                })
            })
            .collect::<Result<Vec<_>>>()
            .context("failed to decode pending email-attention deliveries")
    }

    pub(in crate::email_attention) async fn mark_delivery_failure(
        &self,
        idempotency_key: &str,
        error: &str,
        at: DateTime<Utc>,
    ) -> Result<()> {
        let error = super::bounded_text(error, 512);
        let updated = self
            .connection
            .execute(statement(
                r#"
                UPDATE ai_agent_coordinator.email_attention_deliveries
                   SET attempts = attempts + 1,
                       updated_at = $1,
                       last_error = $2
                 WHERE idempotency_key = $3
                   AND status = 'pending'
                "#,
                vec![at.into(), error.into(), idempotency_key.into()],
            ))
            .await
            .context("failed to record the email-attention delivery failure")?
            .rows_affected();
        if updated != 1 {
            return Err(anyhow!("pending email-attention delivery does not exist"));
        }
        Ok(())
    }

    pub(in crate::email_attention) async fn mark_delivery_success(
        &self,
        idempotency_key: &str,
        at: DateTime<Utc>,
    ) -> Result<()> {
        let transaction = self
            .connection
            .begin()
            .await
            .context("failed to start the email-attention delivery-success transaction")?;
        let status: Option<String> = transaction
            .query_one(statement(
                r#"
                SELECT status
                  FROM ai_agent_coordinator.email_attention_deliveries
                 WHERE idempotency_key = $1
                "#,
                vec![idempotency_key.into()],
            ))
            .await
            .context("failed to read the email-attention delivery status")?
            .map(|row| row.try_get("", "status"))
            .transpose()?;
        match status.as_deref() {
            Some("delivered") => {
                transaction
                    .commit()
                    .await
                    .context("failed to commit the email-attention delivery-success check")?;
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

        let delivery_items = transaction
            .query_all(statement(
                r#"
                SELECT source_id, stable_id, fingerprint
                  FROM ai_agent_coordinator.email_attention_delivery_items
                 WHERE idempotency_key = $1
                "#,
                vec![idempotency_key.into()],
            ))
            .await
            .context("failed to list email-attention delivery items")?
            .into_iter()
            .map(|row| {
                Ok(DeliveryItem {
                    source_id: row.try_get("", "source_id")?,
                    stable_id: row.try_get("", "stable_id")?,
                    fingerprint: row.try_get("", "fingerprint")?,
                })
            })
            .collect::<Result<Vec<_>>>()?;

        transaction
            .execute(statement(
                r#"
                UPDATE ai_agent_coordinator.email_attention_deliveries
                   SET status = 'delivered',
                       attempts = attempts + 1,
                       payload_json = '{"redacted":true}'::jsonb,
                       updated_at = $1,
                       delivered_at = $1,
                       last_error = NULL
                 WHERE idempotency_key = $2
                "#,
                vec![at.into(), idempotency_key.into()],
            ))
            .await
            .context("failed to redact the delivered email-attention payload")?;

        for item in delivery_items {
            transaction
                .execute(statement(
                    r#"
                    UPDATE ai_agent_coordinator.email_attention_items
                       SET last_emitted_fingerprint = $1,
                           last_emitted_at = $2,
                           pending_delivery_key = CASE
                               WHEN pending_delivery_key = $3 THEN NULL
                               ELSE pending_delivery_key
                           END
                     WHERE source_id = $4 AND stable_id = $5
                    "#,
                    vec![
                        item.fingerprint.into(),
                        at.into(),
                        idempotency_key.into(),
                        item.source_id.into(),
                        item.stable_id.into(),
                    ],
                ))
                .await
                .context("failed to advance an email-attention item after delivery")?;
        }

        transaction
            .commit()
            .await
            .context("failed to commit the email-attention delivery success")?;
        Ok(())
    }

    #[cfg(test)]
    pub(super) async fn delivery_payload(&self, idempotency_key: &str) -> Result<Option<JsonValue>> {
        let row = self
            .connection
            .query_one(statement(
                r#"
                SELECT payload_json
                  FROM ai_agent_coordinator.email_attention_deliveries
                 WHERE idempotency_key = $1
                "#,
                vec![idempotency_key.into()],
            ))
            .await
            .context("failed to read the email-attention delivery payload")?;
        row.map(|row| row.try_get("", "payload_json"))
            .transpose()
            .context("failed to decode the email-attention delivery payload")
    }
}
