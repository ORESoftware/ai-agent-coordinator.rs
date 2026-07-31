use super::*;

use super::{
    classification::{
        bounded_text, classify_message, delivery_idempotency_key, read_bounded_response,
        read_secret_env, reject_oversized_content_length, should_emit, validate_connector_response,
    },
    settings::source_health,
};

impl EmailAttentionAgent {
    pub async fn from_env(database_url: Option<&str>) -> Result<Self> {
        let settings = Settings::from_env()?;
        let client = Client::builder()
            .timeout(settings.request_timeout)
            .redirect(Policy::none())
            .build()
            .context("failed to construct email-attention HTTP client")?;
        let store = if settings.enabled {
            let database_url = database_url.ok_or_else(|| {
                anyhow!("email-attention is enabled but no PostgreSQL database URL is configured")
            })?;
            let store = AttentionStore::connect(database_url).await?;
            store.verify_schema().await?;
            Some(store)
        } else {
            None
        };
        Ok(Self {
            client,
            settings: Arc::new(settings),
            store,
            run_lock: Arc::new(AsyncMutex::new(())),
            lease_holder: Arc::new(Uuid::new_v4().to_string()),
        })
    }

    pub fn enabled(&self) -> bool {
        self.settings.enabled
    }

    fn store(&self) -> Result<&AttentionStore> {
        self.store.as_ref().ok_or_else(|| {
            anyhow!("email-attention store is unavailable because the agent is disabled")
        })
    }

    pub fn spawn_scheduler(&self) -> Option<tokio::task::JoinHandle<()>> {
        if !self.enabled() {
            info!("email-attention scheduler is disabled");
            return None;
        }

        let agent = self.clone();
        Some(tokio::spawn(async move {
            loop {
                let now = Utc::now();
                let next_run = agent.settings.schedule.next_after(now);
                let delay = (next_run - now)
                    .to_std()
                    .unwrap_or_else(|_| Duration::from_secs(1));
                info!(next_run = %next_run, "email-attention scheduler waiting for next run");
                tokio::time::sleep(delay).await;

                match agent.run_scheduled().await {
                    Ok(Some(report)) => {
                        info!(
                            run_id = %report.run_id,
                            scan_status = %report.scan_status,
                            notification_status = %report.notification_status,
                            urgent = report.urgent.len(),
                            needs_reply_soon = report.needs_reply_soon.len(),
                            "email-attention scheduled run finished"
                        );
                    }
                    Ok(None) => {
                        info!("email-attention scheduled run skipped because another holder owns the lease");
                    }
                    Err(error) => {
                        error!(error = %error, "email-attention scheduled run failed");
                    }
                }
            }
        }))
    }

    pub async fn status(&self) -> Result<EmailAttentionStatus> {
        let (stored_states, last_successful_scheduled_run, pending_delivery_count, stored_last_run) =
            match self.store.as_ref() {
                Some(store) => (
                    store.source_states().await?,
                    store.last_successful_scheduled_run_at().await?,
                    store.pending_delivery_count().await?,
                    store.last_run().await?,
                ),
                None => (Vec::new(), None, 0, None),
            };
        let stored_states = stored_states
            .into_iter()
            .map(|state| (state.source_id.clone(), state))
            .collect::<HashMap<_, _>>();
        let source_health = self
            .settings
            .sources
            .iter()
            .map(|source| source_health(source, stored_states.get(&source.id)))
            .collect();
        let last_run = stored_last_run.map(|run| LastRunReport {
            run_id: run.run_id,
            mode: run.mode,
            started_at: run.started_at,
            finished_at: run.finished_at,
            scan_status: run.scan_status,
            notification_status: run.notification_status,
            attention_item_count: run.attention_item_count,
            source_success_count: run.source_success_count,
            source_failure_count: run.source_failure_count,
            error: run.error,
        });

        Ok(EmailAttentionStatus {
            enabled: self.enabled(),
            timezone: self.settings.schedule.timezone_name().to_owned(),
            weekdays: self
                .settings
                .schedule
                .weekday_names()
                .into_iter()
                .map(str::to_owned)
                .collect(),
            local_hour: self.settings.schedule.local_hour(),
            local_minute: self.settings.schedule.local_minute(),
            next_scheduled_run: self
                .enabled()
                .then(|| self.settings.schedule.next_after(Utc::now())),
            last_successful_scheduled_run,
            pending_delivery_count,
            notification_endpoint_configured: self.settings.notification.is_some(),
            source_health,
            last_run,
        })
    }

    pub async fn run_manual_test(
        &self,
        request: ManualEmailAttentionRunRequest,
    ) -> Result<EmailAttentionRunReport> {
        if !self.enabled() {
            bail!("email-attention agent is disabled");
        }
        let _guard = self.run_lock.lock().await;
        self.execute_run(RunMode::ManualTest, request.deliver).await
    }

    pub(super) async fn run_scheduled(&self) -> Result<Option<EmailAttentionRunReport>> {
        let _guard = self.run_lock.lock().await;
        let now = Utc::now();
        let acquired = self
            .store()?
            .try_acquire_lease(
                SCHEDULER_LEASE_NAME,
                self.lease_holder.as_str(),
                now,
                now + self.settings.lease_duration,
            )
            .await?;
        if !acquired {
            return Ok(None);
        }
        self.execute_run(RunMode::Scheduled, true).await.map(Some)
    }

    async fn renew_scheduler_lease(&self) -> Result<()> {
        let now = Utc::now();
        if !self
            .store()?
            .try_acquire_lease(
                SCHEDULER_LEASE_NAME,
                self.lease_holder.as_str(),
                now,
                now + self.settings.lease_duration,
            )
            .await?
        {
            bail!("email-attention scheduler lease was lost during the run");
        }
        Ok(())
    }

    async fn execute_run(
        &self,
        mode: RunMode,
        deliver_manual_test: bool,
    ) -> Result<EmailAttentionRunReport> {
        let started_at = Utc::now();
        let run_id = Uuid::new_v4().to_string();
        let retry_report = if matches!(mode, RunMode::Scheduled) {
            self.retry_pending_deliveries().await?
        } else {
            RetryReport::default()
        };

        let mut source_runs = Vec::with_capacity(self.settings.sources.len());
        let mut candidates = Vec::new();
        let mut source_success_count = 0usize;
        let mut source_failure_count = 0usize;
        let mut source_progress = Vec::with_capacity(self.settings.sources.len());

        for source in &self.settings.sources {
            if matches!(mode, RunMode::Scheduled) {
                self.renew_scheduler_lease().await?;
            }
            let cursor = self.store()?.source_cursor(&source.id).await?;
            match self
                .fetch_source(source, cursor.as_deref(), mode.is_test())
                .await
            {
                Ok(response) => {
                    let observed_at = Utc::now();
                    let messages_scanned = response.messages.len();
                    let next_cursor = response.next_cursor.clone();
                    let mut attention_items = 0usize;
                    for message in response.messages {
                        let Some(candidate) = classify_message(source, &message, observed_at) else {
                            continue;
                        };
                        attention_items += 1;

                        if matches!(mode, RunMode::Scheduled) {
                            let state = self
                                .store()?
                                .item_state(&candidate.source_id, &candidate.stable_id)
                                .await?;
                            let should_emit = should_emit(
                                &candidate,
                                state.as_ref(),
                                observed_at,
                                self.settings.reminder_interval,
                            );
                            self.store()?
                                .record_seen_item(&SeenItem {
                                    source_id: candidate.source_id.clone(),
                                    stable_id: candidate.stable_id.clone(),
                                    fingerprint: candidate.fingerprint.clone(),
                                    bucket: candidate.bucket.as_str().to_owned(),
                                    deadline_at: candidate
                                        .item
                                        .deadline
                                        .as_ref()
                                        .map(|value| value.at),
                                    seen_at: observed_at,
                                })
                                .await?;
                            if should_emit {
                                candidates.push(candidate);
                            }
                        } else {
                            candidates.push(candidate);
                        }
                    }

                    if matches!(mode, RunMode::Scheduled) {
                        source_progress.push(SourceProgress {
                            source_id: source.id.clone(),
                            provider: source.provider,
                            previous_cursor: cursor,
                            next_cursor,
                            observed_at,
                        });
                    }
                    source_success_count += 1;
                    source_runs.push(SourceRunReport {
                        source_id: source.id.clone(),
                        provider: source.provider.as_str().to_owned(),
                        status: "success".to_owned(),
                        messages_scanned,
                        attention_items,
                        error: None,
                    });
                }
                Err(error) => {
                    let public_error = bounded_text(&error.to_string(), 256);
                    if matches!(mode, RunMode::Scheduled) {
                        self.store()?
                            .record_source_failure(
                                &source.id,
                                source.provider.as_str(),
                                &public_error,
                                Utc::now(),
                            )
                            .await?;
                    }
                    source_failure_count += 1;
                    source_runs.push(SourceRunReport {
                        source_id: source.id.clone(),
                        provider: source.provider.as_str().to_owned(),
                        status: "failed".to_owned(),
                        messages_scanned: 0,
                        attention_items: 0,
                        error: Some(public_error),
                    });
                }
            }
        }

        candidates.sort_by(|left, right| {
            left.bucket
                .rank()
                .cmp(&right.bucket.rank())
                .then_with(|| right.item.received_at.cmp(&left.item.received_at))
                .then_with(|| left.source_id.cmp(&right.source_id))
                .then_with(|| left.stable_id.cmp(&right.stable_id))
        });
        let truncated_item_count = candidates
            .len()
            .saturating_sub(self.settings.max_notifications_per_run);
        candidates.truncate(self.settings.max_notifications_per_run);

        let urgent = candidates
            .iter()
            .filter(|candidate| candidate.bucket == AttentionBucket::Urgent)
            .map(|candidate| candidate.item.clone())
            .collect::<Vec<_>>();
        let needs_reply_soon = candidates
            .iter()
            .filter(|candidate| candidate.bucket == AttentionBucket::NeedsReplySoon)
            .map(|candidate| candidate.item.clone())
            .collect::<Vec<_>>();

        let scan_status = if source_failure_count == 0 {
            "success"
        } else if source_success_count == 0 {
            "failed"
        } else {
            "partial"
        }
        .to_owned();

        let mut notification_status = if retry_report.failure_count > 0 {
            "pending_retry_failed".to_owned()
        } else if retry_report.success_count > 0 {
            "pending_retry_delivered".to_owned()
        } else {
            "not_attempted".to_owned()
        };
        let mut run_error = None;
        let mut scheduled_delivery = None;

        if !candidates.is_empty() {
            let payload = NotificationPayload {
                schema_version: "email-attention/v1",
                run_id: &run_id,
                generated_at: Utc::now(),
                test_run: mode.is_test(),
                urgent: &urgent,
                needs_reply_soon: &needs_reply_soon,
                source_failure_count,
                truncated_item_count,
            };
            let payload_value = serde_json::to_value(&payload)
                .context("failed to serialize email-attention notification")?;
            let payload_json = payload_value.to_string();

            if matches!(mode, RunMode::Scheduled) {
                self.renew_scheduler_lease().await?;
                let idempotency_key = delivery_idempotency_key(&run_id, &candidates);
                let delivery_items = candidates
                    .iter()
                    .map(|candidate| DeliveryItem {
                        source_id: candidate.source_id.clone(),
                        stable_id: candidate.stable_id.clone(),
                        fingerprint: candidate.fingerprint.clone(),
                    })
                    .collect::<Vec<_>>();
                self.store()?
                    .create_pending_delivery(
                        &idempotency_key,
                        &payload_value,
                        &delivery_items,
                        Utc::now(),
                    )
                    .await?;
                scheduled_delivery = Some((idempotency_key, payload_json));
            } else if deliver_manual_test {
                let idempotency_key = format!("email-attention:test:{run_id}");
                match self
                    .deliver_serialized(&idempotency_key, &payload_json)
                    .await
                {
                    Ok(()) => notification_status = "test_delivered".to_owned(),
                    Err(error) => {
                        notification_status = "test_delivery_failed".to_owned();
                        run_error = Some(bounded_text(&error.to_string(), 256));
                    }
                }
            } else {
                notification_status = "test_preview_only".to_owned();
            }
        }

        let cursor_advanced = if matches!(mode, RunMode::Scheduled) {
            // If the notification cap truncated items, keep every source cursor in place so
            // the next run can re-read the bounded window. Already delivered fingerprints
            // are suppressed, allowing the remaining items to drain without silent loss.
            self.record_source_progress(&source_progress, truncated_item_count == 0)
                .await?
        } else {
            false
        };

        if let Some((idempotency_key, payload_json)) = scheduled_delivery {
            match self
                .deliver_serialized(&idempotency_key, &payload_json)
                .await
            {
                Ok(()) => {
                    self.store()?
                        .mark_delivery_success(&idempotency_key, Utc::now())
                        .await?;
                    notification_status = if retry_report.failure_count > 0 {
                        "delivered_with_pending_retry_failures".to_owned()
                    } else {
                        "delivered".to_owned()
                    };
                }
                Err(error) => {
                    let public_error = bounded_text(&error.to_string(), 256);
                    self.store()?
                        .mark_delivery_failure(&idempotency_key, &public_error, Utc::now())
                        .await?;
                    notification_status = "delivery_failed".to_owned();
                    run_error = Some(public_error);
                }
            }
        } else if candidates.is_empty()
            && retry_report.success_count == 0
            && retry_report.failure_count == 0
        {
            notification_status = "silent".to_owned();
        }

        let finished_at = Utc::now();
        self.store()?
            .record_run(&RunRecord {
                run_id: run_id.clone(),
                mode: mode.as_str().to_owned(),
                started_at,
                finished_at,
                scan_status: scan_status.clone(),
                notification_status: notification_status.clone(),
                attention_item_count: urgent.len() + needs_reply_soon.len(),
                source_success_count,
                source_failure_count,
                error: run_error,
            })
            .await?;

        Ok(EmailAttentionRunReport {
            run_id,
            mode: mode.as_str().to_owned(),
            test_run: mode.is_test(),
            cursor_advanced,
            started_at,
            finished_at,
            scan_status,
            notification_status,
            source_runs,
            urgent,
            needs_reply_soon,
            truncated_item_count,
            pending_retry_success_count: retry_report.success_count,
            pending_retry_failure_count: retry_report.failure_count,
        })
    }

    async fn record_source_progress(
        &self,
        progress: &[SourceProgress],
        advance_cursors: bool,
    ) -> Result<bool> {
        let mut cursor_advanced = false;
        for source in progress {
            let committed_cursor = if advance_cursors {
                source.next_cursor.as_deref()
            } else {
                None
            };
            cursor_advanced |= committed_cursor
                .is_some_and(|next| source.previous_cursor.as_deref() != Some(next));
            self.store()?
                .record_source_success(
                    &source.source_id,
                    source.provider.as_str(),
                    committed_cursor,
                    source.observed_at,
                )
                .await?;
        }
        Ok(cursor_advanced)
    }

    async fn retry_pending_deliveries(&self) -> Result<RetryReport> {
        let pending = self
            .store()?
            .pending_deliveries(self.settings.pending_retry_limit)
            .await?;
        let mut report = RetryReport::default();
        for delivery in pending {
            self.renew_scheduler_lease().await?;
            let payload_json = delivery.payload_json.to_string();
            match self
                .deliver_serialized(&delivery.idempotency_key, &payload_json)
                .await
            {
                Ok(()) => {
                    self.store()?
                        .mark_delivery_success(&delivery.idempotency_key, Utc::now())
                        .await?;
                    report.success_count += 1;
                }
                Err(error) => {
                    let public_error = bounded_text(&error.to_string(), 256);
                    self.store()?
                        .mark_delivery_failure(
                            &delivery.idempotency_key,
                            &public_error,
                            Utc::now(),
                        )
                        .await?;
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
}
