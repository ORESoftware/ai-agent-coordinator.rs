    pub fn from_env(database_path: &str) -> Result<Self> {
        let settings = Settings::from_env()?;
        let client = Client::builder()
            .timeout(settings.request_timeout)
            .redirect(Policy::none())
            .build()
            .context("failed to construct email-attention HTTP client")?;
        Ok(Self {
            client,
            settings: Arc::new(settings),
            store: AttentionStore::open(database_path)?,
            run_lock: Arc::new(AsyncMutex::new(())),
            lease_holder: Arc::new(Uuid::new_v4().to_string()),
        })
    }

    pub fn enabled(&self) -> bool {
        self.settings.enabled
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

    pub fn status(&self) -> Result<EmailAttentionStatus> {
        let stored_states = self
            .store
            .source_states()?
            .into_iter()
            .map(|state| (state.source_id.clone(), state))
            .collect::<HashMap<_, _>>();
        let source_health = self
            .settings
            .sources
            .iter()
            .map(|source| source_health(source, stored_states.get(&source.id)))
            .collect();
        let last_run = self.store.last_run()?.map(|run| LastRunReport {
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
            last_successful_scheduled_run: self.store.last_successful_scheduled_run_at()?,
            pending_delivery_count: self.store.pending_delivery_count()?,
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

    async fn run_scheduled(&self) -> Result<Option<EmailAttentionRunReport>> {
        let _guard = self.run_lock.lock().await;
        let now = Utc::now();
        let acquired = self.store.try_acquire_lease(
            SCHEDULER_LEASE_NAME,
            self.lease_holder.as_str(),
            now,
            now + self.settings.lease_duration,
        )?;
        if !acquired {
            return Ok(None);
        }
        self.execute_run(RunMode::Scheduled, true).await.map(Some)
    }

    fn renew_scheduler_lease(&self) -> Result<()> {
        let now = Utc::now();
        if !self.store.try_acquire_lease(
            SCHEDULER_LEASE_NAME,
            self.lease_holder.as_str(),
            now,
            now + self.settings.lease_duration,
        )? {
            bail!("email-attention scheduler lease was lost during the run");
        }
        Ok(())
    }

