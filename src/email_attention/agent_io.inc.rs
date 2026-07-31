    async fn retry_pending_deliveries(&self) -> Result<RetryReport> {
        let pending = self
            .store
            .pending_deliveries(self.settings.pending_retry_limit)?;
        let mut report = RetryReport::default();
        for delivery in pending {
            self.renew_scheduler_lease()?;
            match self
                .deliver_serialized(&delivery.idempotency_key, &delivery.payload_json)
                .await
            {
                Ok(()) => {
                    self.store
                        .mark_delivery_success(&delivery.idempotency_key, Utc::now())?;
                    report.success_count += 1;
                }
                Err(error) => {
                    let public_error = bounded_text(&error.to_string(), 256);
                    self.store.mark_delivery_failure(
                        &delivery.idempotency_key,
                        &public_error,
                        Utc::now(),
                    )?;
                    report.failure_count += 1;
                    warn!(
                        attempts = delivery.attempts + 1,
                        "email-attention pending notification retry failed"
                    );
                }
            }
        }
        Ok(report)
    }

    async fn fetch_source(
        &self,
        source: &SourceConfig,
        cursor: Option<&str>,
        manual_test: bool,
    ) -> Result<ConnectorScanResponse> {
        let mut request = self
            .client
            .post(&source.endpoint)
            .header(ACCEPT, "application/json")
            .header(CONTENT_TYPE, "application/json")
            .header(USER_AGENT, &self.settings.user_agent)
            .json(&ConnectorScanRequest {
                source_id: &source.id,
                provider: source.provider,
                cursor,
                max_messages: self.settings.max_messages_per_source,
                manual_test,
                read_only: true,
            });
        if let Some(token_env) = source.token_env.as_deref() {
            let token = read_secret_env(token_env)?;
            request = request.header(AUTHORIZATION, format!("Bearer {token}"));
        }

        let response = request
            .send()
            .await
            .map_err(|_| anyhow!("connector request failed for source {}", source.id))?;
        if !response.status().is_success() {
            return Err(anyhow!(
                "connector returned HTTP {} for source {}",
                response.status().as_u16(),
                source.id
            ));
        }
        reject_oversized_content_length(&response, self.settings.max_response_bytes)?;
        let bytes = read_bounded_response(response, self.settings.max_response_bytes)
            .await
            .with_context(|| format!("connector response read failed for source {}", source.id))?;
        let response: ConnectorScanResponse = serde_json::from_slice(&bytes)
            .map_err(|_| anyhow!("connector returned invalid JSON for source {}", source.id))?;
        validate_connector_response(source, &response, self.settings.max_messages_per_source)?;
        Ok(response)
    }

    async fn deliver_serialized(&self, idempotency_key: &str, payload_json: &str) -> Result<()> {
        let notification = self
            .settings
            .notification
            .as_ref()
            .ok_or_else(|| anyhow!("notification endpoint is not configured"))?;
        let mut request = self
            .client
            .post(&notification.endpoint)
            .header(ACCEPT, "application/json")
            .header(CONTENT_TYPE, "application/json")
            .header(USER_AGENT, &self.settings.user_agent)
            .header("idempotency-key", idempotency_key)
            .body(payload_json.to_owned());
        if let Some(token_env) = notification.token_env.as_deref() {
            let token = read_secret_env(token_env)?;
            request = request.header(AUTHORIZATION, format!("Bearer {token}"));
        }

        let response = request
            .send()
            .await
            .map_err(|_| anyhow!("email-attention notification request failed"))?;
        if !response.status().is_success() {
            bail!(
                "email-attention notification endpoint returned HTTP {}",
                response.status().as_u16()
            );
        }
        reject_oversized_content_length(&response, self.settings.max_response_bytes)?;
        Ok(())
    }
