use super::*;

impl Settings {
    fn from_env() -> Result<Self> {
        let enabled = read_bool_env(ENABLED_ENV, false)?;
        let source_json = env::var(SOURCES_ENV).unwrap_or_else(|_| "[]".to_owned());
        let mut sources: Vec<SourceConfig> = serde_json::from_str(&source_json)
            .with_context(|| format!("failed to parse {SOURCES_ENV}"))?;
        sources.retain(|source| source.enabled);
        validate_sources(&sources)?;

        let timezone = env::var(TIMEZONE_ENV).unwrap_or_else(|_| "America/New_York".to_owned());
        let weekdays = env::var(WEEKDAYS_ENV).unwrap_or_else(|_| "mon,tue,wed,thu,fri".to_owned());
        let local_hour = read_u32_env(LOCAL_HOUR_ENV, 9, 0, 23)?;
        let local_minute = read_u32_env(LOCAL_MINUTE_ENV, 0, 0, 59)?;
        let schedule = Schedule::parse(&timezone, &weekdays, local_hour, local_minute)?;

        let notification_endpoint = env::var(NOTIFICATION_URL_ENV)
            .ok()
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty());
        if let Some(endpoint) = notification_endpoint.as_deref() {
            validate_safe_endpoint(endpoint, NOTIFICATION_URL_ENV)?;
        }
        let notification_token_env = env::var(NOTIFICATION_TOKEN_ENV_NAME_ENV)
            .unwrap_or_else(|_| DEFAULT_NOTIFICATION_TOKEN_ENV.to_owned());
        let notification_token_env = optional_env_name(&notification_token_env)?;
        let notification = notification_endpoint.map(|endpoint| NotificationConfig {
            endpoint,
            token_env: notification_token_env,
        });

        let request_timeout_ms = read_u64_env(REQUEST_TIMEOUT_MS_ENV, 15_000, 100, 120_000)?;
        let max_response_bytes =
            read_usize_env(MAX_RESPONSE_BYTES_ENV, 512 * 1024, 1024, 8 * 1024 * 1024)?;
        let max_messages_per_source =
            read_usize_env(MAX_MESSAGES_PER_SOURCE_ENV, 200, 1, 2_000)?;
        let max_notifications_per_run =
            read_usize_env(MAX_NOTIFICATIONS_PER_RUN_ENV, 50, 1, 500)?;
        let reminder_hours = read_i64_env(REMINDER_INTERVAL_HOURS_ENV, 24, 1, 24 * 30)?;
        let lease_seconds = read_i64_env(LEASE_SECONDS_ENV, 3_600, 60, 6 * 60 * 60)?;
        let pending_retry_limit = read_usize_env(PENDING_RETRY_LIMIT_ENV, 20, 1, 200)?;
        let user_agent = env::var(USER_AGENT_ENV)
            .unwrap_or_else(|_| DEFAULT_USER_AGENT.to_owned())
            .trim()
            .to_owned();
        if user_agent.is_empty() || user_agent.len() > 128 || user_agent.chars().any(char::is_control)
        {
            bail!("{USER_AGENT_ENV} must be 1..=128 visible characters");
        }

        if enabled {
            if sources.is_empty() {
                bail!("{ENABLED_ENV} is true but {SOURCES_ENV} contains no enabled sources");
            }
            if notification.is_none() {
                bail!("{ENABLED_ENV} is true but {NOTIFICATION_URL_ENV} is not configured");
            }
            for source in &sources {
                if let Some(token_env) = source.token_env.as_deref() {
                    read_secret_env(token_env).with_context(|| {
                        format!("source {} cannot read its configured token", source.id)
                    })?;
                }
            }
            if let Some(token_env) = notification
                .as_ref()
                .and_then(|value| value.token_env.as_deref())
            {
                read_secret_env(token_env)
                    .context("email-attention notification token is unavailable")?;
            }
        }

        Ok(Self {
            enabled,
            sources,
            notification,
            schedule,
            request_timeout: Duration::from_millis(request_timeout_ms),
            max_response_bytes,
            max_messages_per_source,
            max_notifications_per_run,
            reminder_interval: ChronoDuration::hours(reminder_hours),
            lease_duration: ChronoDuration::seconds(lease_seconds),
            pending_retry_limit,
            user_agent,
        })
    }
}

pub(super) fn source_health(
    source: &SourceConfig,
    stored: Option<&StoredSourceState>,
) -> SourceHealthReport {
    match stored {
        Some(state) => SourceHealthReport {
            source_id: source.id.clone(),
            provider: source.provider.as_str().to_owned(),
            status: if state.provider != source.provider.as_str() {
                "configuration_changed".to_owned()
            } else if state.last_error.is_some() {
                "degraded".to_owned()
            } else {
                "healthy".to_owned()
            },
            last_success_at: state.last_success_at,
            last_error: state.last_error.clone(),
            last_error_at: state.last_error_at,
        },
        None => SourceHealthReport {
            source_id: source.id.clone(),
            provider: source.provider.as_str().to_owned(),
            status: "never_run".to_owned(),
            last_success_at: None,
            last_error: None,
            last_error_at: None,
        },
    }
}
