use std::{path::Path, sync::Arc, time::Duration};

use anyhow::{anyhow, Context, Result};
use chrono::{DateTime, Utc};
use parking_lot::Mutex;
use rusqlite::{params, Connection, OptionalExtension};

#[derive(Clone)]
pub(super) struct AttentionStore {
    connection: Arc<Mutex<Connection>>,
}

#[derive(Debug, Clone)]
pub(super) struct ItemState {
    pub(super) last_emitted_fingerprint: Option<String>,
    pub(super) last_emitted_at: Option<DateTime<Utc>>,
    pub(super) pending_delivery_key: Option<String>,
}

#[derive(Debug, Clone)]
pub(super) struct SeenItem {
    pub(super) source_id: String,
    pub(super) stable_id: String,
    pub(super) fingerprint: String,
    pub(super) bucket: String,
    pub(super) deadline_at: Option<DateTime<Utc>>,
    pub(super) seen_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub(super) struct DeliveryItem {
    pub(super) source_id: String,
    pub(super) stable_id: String,
    pub(super) fingerprint: String,
}

#[derive(Debug, Clone)]
pub(super) struct PendingDelivery {
    pub(super) idempotency_key: String,
    pub(super) payload_json: String,
    pub(super) attempts: u32,
}

#[derive(Debug, Clone)]
pub(super) struct SourceState {
    pub(super) source_id: String,
    pub(super) provider: String,
    pub(super) last_success_at: Option<DateTime<Utc>>,
    pub(super) last_error: Option<String>,
    pub(super) last_error_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone)]
pub(super) struct RunState {
    pub(super) run_id: String,
    pub(super) mode: String,
    pub(super) started_at: DateTime<Utc>,
    pub(super) finished_at: DateTime<Utc>,
    pub(super) scan_status: String,
    pub(super) notification_status: String,
    pub(super) attention_item_count: usize,
    pub(super) source_success_count: usize,
    pub(super) source_failure_count: usize,
    pub(super) error: Option<String>,
}

#[derive(Debug, Clone)]
pub(super) struct RunRecord {
    pub(super) run_id: String,
    pub(super) mode: String,
    pub(super) started_at: DateTime<Utc>,
    pub(super) finished_at: DateTime<Utc>,
    pub(super) scan_status: String,
    pub(super) notification_status: String,
    pub(super) attention_item_count: usize,
    pub(super) source_success_count: usize,
    pub(super) source_failure_count: usize,
    pub(super) error: Option<String>,
}

impl AttentionStore {
    include!("store_init.inc.rs");
    include!("store_delivery.inc.rs");
    include!("store_status.inc.rs");
}

fn optional_timestamp(value: Option<i64>) -> rusqlite::Result<Option<DateTime<Utc>>> {
    value.map(timestamp_from_millis).transpose()
}

fn timestamp_from_millis(value: i64) -> rusqlite::Result<DateTime<Utc>> {
    DateTime::<Utc>::from_timestamp_millis(value).ok_or_else(|| {
        rusqlite::Error::FromSqlConversionFailure(
            0,
            rusqlite::types::Type::Integer,
            Box::new(anyhow!("timestamp {value} is outside chrono range")),
        )
    })
}

fn bounded_text(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

include!("store_tests.inc.rs");
