<<<<<<< HEAD
mod agent;
mod classification;
mod schedule;
mod settings;
mod store;

#[cfg(test)]
mod tests;

=======
mod schedule;
mod store;

>>>>>>> origin/agent/den-830-email-attention-agent
use std::{
    collections::{HashMap, HashSet},
    env,
    sync::Arc,
    time::Duration,
};

use anyhow::{anyhow, bail, Context, Result};
use chrono::{DateTime, Duration as ChronoDuration, Utc};
use reqwest::{
    header::{ACCEPT, AUTHORIZATION, CONTENT_LENGTH, CONTENT_TYPE, USER_AGENT},
    redirect::Policy,
    Client, Url,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::sync::Mutex as AsyncMutex;
use tracing::{error, info, warn};
use uuid::Uuid;

use self::{
    schedule::Schedule,
    store::{
        AttentionStore, DeliveryItem, ItemState, RunRecord, SeenItem,
        SourceState as StoredSourceState,
    },
};

const ENABLED_ENV: &str = "EMAIL_ATTENTION_ENABLED";
const SOURCES_ENV: &str = "EMAIL_ATTENTION_SOURCES_JSON";
const TIMEZONE_ENV: &str = "EMAIL_ATTENTION_TIMEZONE";
const WEEKDAYS_ENV: &str = "EMAIL_ATTENTION_WEEKDAYS";
const LOCAL_HOUR_ENV: &str = "EMAIL_ATTENTION_LOCAL_HOUR";
const LOCAL_MINUTE_ENV: &str = "EMAIL_ATTENTION_LOCAL_MINUTE";
const NOTIFICATION_URL_ENV: &str = "EMAIL_ATTENTION_NOTIFICATION_URL";
const NOTIFICATION_TOKEN_ENV_NAME_ENV: &str = "EMAIL_ATTENTION_NOTIFICATION_TOKEN_ENV";
const DEFAULT_NOTIFICATION_TOKEN_ENV: &str = "EMAIL_ATTENTION_NOTIFICATION_TOKEN";
const REQUEST_TIMEOUT_MS_ENV: &str = "EMAIL_ATTENTION_REQUEST_TIMEOUT_MS";
const MAX_RESPONSE_BYTES_ENV: &str = "EMAIL_ATTENTION_MAX_RESPONSE_BYTES";
const MAX_MESSAGES_PER_SOURCE_ENV: &str = "EMAIL_ATTENTION_MAX_MESSAGES_PER_SOURCE";
const MAX_NOTIFICATIONS_PER_RUN_ENV: &str = "EMAIL_ATTENTION_MAX_NOTIFICATIONS_PER_RUN";
const REMINDER_INTERVAL_HOURS_ENV: &str = "EMAIL_ATTENTION_REMINDER_INTERVAL_HOURS";
const LEASE_SECONDS_ENV: &str = "EMAIL_ATTENTION_LEASE_SECONDS";
const PENDING_RETRY_LIMIT_ENV: &str = "EMAIL_ATTENTION_PENDING_RETRY_LIMIT";
const USER_AGENT_ENV: &str = "EMAIL_ATTENTION_USER_AGENT";
const DEFAULT_USER_AGENT: &str = "ai-agent-coordinator-email-attention";
const SCHEDULER_LEASE_NAME: &str = "email-attention-scheduler";

#[derive(Clone)]
pub struct EmailAttentionAgent {
    client: Client,
    settings: Arc<Settings>,
<<<<<<< HEAD
    store: Option<AttentionStore>,
=======
    store: AttentionStore,
>>>>>>> origin/agent/den-830-email-attention-agent
    run_lock: Arc<AsyncMutex<()>>,
    lease_holder: Arc<String>,
}

#[derive(Debug)]
struct Settings {
    enabled: bool,
    sources: Vec<SourceConfig>,
    notification: Option<NotificationConfig>,
    schedule: Schedule,
    request_timeout: Duration,
    max_response_bytes: usize,
    max_messages_per_source: usize,
    max_notifications_per_run: usize,
    reminder_interval: ChronoDuration,
    lease_duration: ChronoDuration,
    pending_retry_limit: usize,
    user_agent: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceConfig {
    id: String,
    provider: SourceProvider,
    endpoint: String,
    #[serde(default)]
    token_env: Option<String>,
    #[serde(default = "default_true")]
    enabled: bool,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum SourceProvider {
    Gmail,
    Outlook,
}

impl SourceProvider {
    fn as_str(self) -> &'static str {
        match self {
            Self::Gmail => "gmail",
            Self::Outlook => "outlook",
        }
    }
}

#[derive(Debug)]
struct NotificationConfig {
    endpoint: String,
    token_env: Option<String>,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum RunMode {
    Scheduled,
    ManualTest,
}

impl RunMode {
    fn as_str(self) -> &'static str {
        match self {
            Self::Scheduled => "scheduled",
            Self::ManualTest => "manual_test",
        }
    }

    fn is_test(self) -> bool {
        matches!(self, Self::ManualTest)
    }
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum AttentionBucket {
    Urgent,
    NeedsReplySoon,
}

impl AttentionBucket {
    fn as_str(self) -> &'static str {
        match self {
            Self::Urgent => "urgent",
            Self::NeedsReplySoon => "needs_reply_soon",
        }
    }

    fn rank(self) -> u8 {
        match self {
            Self::Urgent => 0,
            Self::NeedsReplySoon => 1,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct AttentionNotificationItem {
    pub mailbox: String,
    pub provider: String,
    pub reference: String,
    pub sender: String,
    pub subject: String,
    pub received_at: DateTime<Utc>,
    pub reason: String,
    pub confidence: f32,
    pub deadline: Option<DeadlineEvidence>,
    pub recommended_next_action: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct DeadlineEvidence {
    pub at: DateTime<Utc>,
    pub source: &'static str,
}

#[derive(Debug, Clone)]
struct CandidateItem {
    source_id: String,
    stable_id: String,
    fingerprint: String,
    bucket: AttentionBucket,
    item: AttentionNotificationItem,
}

#[derive(Debug, Clone)]
struct SourceProgress {
    source_id: String,
    provider: SourceProvider,
    previous_cursor: Option<String>,
    next_cursor: Option<String>,
    observed_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct ConnectorMessage {
    stable_id: String,
    #[serde(default)]
    thread_id: Option<String>,
    sender: String,
    subject: String,
    received_at: DateTime<Utc>,
    #[serde(default)]
    snippet: Option<String>,
    #[serde(default)]
    direct_request: bool,
    #[serde(default)]
    user_is_next_responder: bool,
    #[serde(default)]
    automated: bool,
    #[serde(default)]
    importance: MessageImportance,
    #[serde(default)]
    categories: Vec<String>,
    #[serde(default)]
    explicit_deadline: Option<DateTime<Utc>>,
    #[serde(default)]
    material_version: Option<String>,
}

#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum MessageImportance {
    Low,
    #[default]
    Normal,
    High,
}

#[derive(Debug, Serialize)]
struct ConnectorScanRequest<'a> {
    source_id: &'a str,
    provider: SourceProvider,
    cursor: Option<&'a str>,
    max_messages: usize,
    manual_test: bool,
    read_only: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ConnectorScanResponse {
    #[serde(default)]
    next_cursor: Option<String>,
    #[serde(default)]
    messages: Vec<ConnectorMessage>,
}

#[derive(Debug, Serialize)]
struct NotificationPayload<'a> {
    schema_version: &'static str,
    run_id: &'a str,
    generated_at: DateTime<Utc>,
    test_run: bool,
    urgent: &'a [AttentionNotificationItem],
    needs_reply_soon: &'a [AttentionNotificationItem],
    source_failure_count: usize,
    truncated_item_count: usize,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManualEmailAttentionRunRequest {
    #[serde(default)]
    pub deliver: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct EmailAttentionRunReport {
    pub run_id: String,
    pub mode: String,
    pub test_run: bool,
    pub cursor_advanced: bool,
    pub started_at: DateTime<Utc>,
    pub finished_at: DateTime<Utc>,
    pub scan_status: String,
    pub notification_status: String,
    pub source_runs: Vec<SourceRunReport>,
    pub urgent: Vec<AttentionNotificationItem>,
    pub needs_reply_soon: Vec<AttentionNotificationItem>,
    pub truncated_item_count: usize,
    pub pending_retry_success_count: usize,
    pub pending_retry_failure_count: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct SourceRunReport {
    pub source_id: String,
    pub provider: String,
    pub status: String,
    pub messages_scanned: usize,
    pub attention_items: usize,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct EmailAttentionStatus {
    pub enabled: bool,
    pub timezone: String,
    pub weekdays: Vec<String>,
    pub local_hour: u32,
    pub local_minute: u32,
    pub next_scheduled_run: Option<DateTime<Utc>>,
    pub last_successful_scheduled_run: Option<DateTime<Utc>>,
    pub pending_delivery_count: usize,
    pub notification_endpoint_configured: bool,
    pub source_health: Vec<SourceHealthReport>,
    pub last_run: Option<LastRunReport>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SourceHealthReport {
    pub source_id: String,
    pub provider: String,
    pub status: String,
    pub last_success_at: Option<DateTime<Utc>>,
    pub last_error: Option<String>,
    pub last_error_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Serialize)]
pub struct LastRunReport {
    pub run_id: String,
    pub mode: String,
    pub started_at: DateTime<Utc>,
    pub finished_at: DateTime<Utc>,
    pub scan_status: String,
    pub notification_status: String,
    pub attention_item_count: usize,
    pub source_success_count: usize,
    pub source_failure_count: usize,
    pub error: Option<String>,
}

#[derive(Debug, Default)]
struct RetryReport {
    success_count: usize,
    failure_count: usize,
}
<<<<<<< HEAD
=======

include!("agent.inc.rs");
include!("settings.inc.rs");
include!("classification.inc.rs");
include!("tests.inc.rs");
>>>>>>> origin/agent/den-830-email-attention-agent
