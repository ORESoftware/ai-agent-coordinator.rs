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
                self.renew_scheduler_lease()?;
            }
            let cursor = self.store.source_cursor(&source.id)?;
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
                                .store
                                .item_state(&candidate.source_id, &candidate.stable_id)?;
                            let should_emit = should_emit(
                                &candidate,
                                state.as_ref(),
                                observed_at,
                                self.settings.reminder_interval,
                            );
                            self.store.record_seen_item(&SeenItem {
                                source_id: candidate.source_id.clone(),
                                stable_id: candidate.stable_id.clone(),
                                fingerprint: candidate.fingerprint.clone(),
                                bucket: candidate.bucket.as_str().to_owned(),
                                deadline_at: candidate.item.deadline.as_ref().map(|value| value.at),
                                seen_at: observed_at,
                            })?;
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
                        self.store.record_source_failure(
                            &source.id,
                            source.provider.as_str(),
                            &public_error,
                            Utc::now(),
                        )?;
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
            let payload_json = serde_json::to_string(&payload)
                .context("failed to serialize email-attention notification")?;

            if matches!(mode, RunMode::Scheduled) {
                self.renew_scheduler_lease()?;
                let idempotency_key = delivery_idempotency_key(&run_id, &candidates);
                let delivery_items = candidates
                    .iter()
                    .map(|candidate| DeliveryItem {
                        source_id: candidate.source_id.clone(),
                        stable_id: candidate.stable_id.clone(),
                        fingerprint: candidate.fingerprint.clone(),
                    })
                    .collect::<Vec<_>>();
                self.store.create_pending_delivery(
                    &idempotency_key,
                    &payload_json,
                    &delivery_items,
                    Utc::now(),
                )?;
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
            self.record_source_progress(&source_progress, truncated_item_count == 0)?
        } else {
            false
        };

        if let Some((idempotency_key, payload_json)) = scheduled_delivery {
            match self
                .deliver_serialized(&idempotency_key, &payload_json)
                .await
            {
                Ok(()) => {
                    self.store
                        .mark_delivery_success(&idempotency_key, Utc::now())?;
                    notification_status = if retry_report.failure_count > 0 {
                        "delivered_with_pending_retry_failures".to_owned()
                    } else {
                        "delivered".to_owned()
                    };
                }
                Err(error) => {
                    let public_error = bounded_text(&error.to_string(), 256);
                    self.store.mark_delivery_failure(
                        &idempotency_key,
                        &public_error,
                        Utc::now(),
                    )?;
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
        self.store.record_run(&RunRecord {
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
        })?;

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

    fn record_source_progress(
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
            self.store.record_source_success(
                &source.source_id,
                source.provider.as_str(),
                committed_cursor,
                source.observed_at,
            )?;
        }
        Ok(cursor_advanced)
    }

