use std::{
    sync::{Arc, Mutex},
    time::Duration,
};

use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    routing::post,
    Json, Router,
};
use chrono::{Duration as ChronoDuration, TimeZone, Utc};
use reqwest::{redirect::Policy, Client};
use serde_json::{json, Value};
use tokio::{net::TcpListener, sync::Mutex as AsyncMutex};
use uuid::Uuid;

use super::{
    classification::{classify_message, should_emit},
    store::AttentionStore,
    AttentionBucket, ConnectorMessage, EmailAttentionAgent, ItemState,
    ManualEmailAttentionRunRequest, MessageImportance, NotificationConfig, Schedule, Settings,
    SourceConfig, SourceProvider, SCHEDULER_LEASE_NAME,
};

fn source() -> SourceConfig {
    SourceConfig {
        id: "primary-gmail".to_owned(),
        provider: SourceProvider::Gmail,
        endpoint: "http://127.0.0.1:1234/scan".to_owned(),
        token_env: None,
        enabled: true,
    }
}

fn message() -> ConnectorMessage {
    ConnectorMessage {
        stable_id: "thread-1".to_owned(),
        thread_id: Some("thread-1".to_owned()),
        sender: "Hiring Team <team@example.test>".to_owned(),
        subject: "Please confirm your interview".to_owned(),
        received_at: Utc
            .with_ymd_and_hms(2026, 7, 30, 12, 0, 0)
            .single()
            .expect("timestamp"),
        snippet: Some("Could you confirm by tomorrow?".to_owned()),
        direct_request: true,
        user_is_next_responder: true,
        automated: false,
        importance: MessageImportance::Normal,
        categories: vec!["hiring".to_owned()],
        explicit_deadline: None,
        material_version: Some("v1".to_owned()),
    }
}

#[derive(Clone, Default)]
struct TestAdapterState {
    scan_requests: Arc<Mutex<Vec<Value>>>,
    notification_requests: Arc<Mutex<Vec<Value>>>,
    notification_keys: Arc<Mutex<Vec<String>>>,
}

async fn test_scan_adapter(
    State(state): State<TestAdapterState>,
    Json(request): Json<Value>,
) -> Json<Value> {
    state
        .scan_requests
        .lock()
        .expect("scan request lock")
        .push(request);
    Json(json!({
        "next_cursor": "cursor-1",
        "messages": [{
            "stable_id": "thread-1",
            "thread_id": "thread-1",
            "sender": "Hiring Team <team@example.test>",
            "subject": "Please confirm your interview",
            "received_at": "2026-07-30T12:00:00Z",
            "snippet": "Could you confirm by tomorrow?\nThank you.",
            "direct_request": true,
            "user_is_next_responder": true,
            "automated": false,
            "importance": "normal",
            "categories": ["hiring"],
            "material_version": "v1"
        }, {
            "stable_id": "thread-2",
            "thread_id": "thread-2",
            "sender": "Customer Team <customer@example.test>",
            "subject": "Can you review the proposal?",
            "received_at": "2026-07-30T11:00:00Z",
            "snippet": "Can you review the proposal and reply?",
            "direct_request": true,
            "user_is_next_responder": true,
            "automated": false,
            "importance": "normal",
            "categories": ["customer"],
            "material_version": "v1"
        }]
    }))
}

async fn test_notification_adapter(
    State(state): State<TestAdapterState>,
    headers: HeaderMap,
    Json(payload): Json<Value>,
) -> StatusCode {
    let key = headers
        .get("idempotency-key")
        .and_then(|value| value.to_str().ok())
        .expect("idempotency key")
        .to_owned();
    state
        .notification_keys
        .lock()
        .expect("notification key lock")
        .push(key);
    state
        .notification_requests
        .lock()
        .expect("notification request lock")
        .push(payload);
    StatusCode::NO_CONTENT
}

async fn test_agent() -> Option<(EmailAttentionAgent, TestAdapterState, String)> {
    let Ok(database_url) = std::env::var("TEST_DATABASE_URL") else {
        eprintln!("skipping PostgreSQL integration test: TEST_DATABASE_URL is not set");
        return None;
    };
    let store = AttentionStore::connect(&database_url)
        .await
        .expect("connect to the email-attention test database");
    store
        .clear_lease(SCHEDULER_LEASE_NAME)
        .await
        .expect("clear scheduler lease");

    let state = TestAdapterState::default();
    let router = Router::new()
        .route("/scan", post(test_scan_adapter))
        .route("/notify", post(test_notification_adapter))
        .with_state(state.clone());
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind test adapter");
    let address = listener.local_addr().expect("test adapter address");
    let _server = tokio::spawn(async move {
        axum::serve(listener, router)
            .await
            .expect("test adapter server");
    });

    let source_id = format!("primary-gmail-{}", Uuid::new_v4());
    let settings = Settings {
        enabled: true,
        sources: vec![SourceConfig {
            id: source_id.clone(),
            provider: SourceProvider::Gmail,
            endpoint: format!("http://{address}/scan"),
            token_env: None,
            enabled: true,
        }],
        notification: Some(NotificationConfig {
            endpoint: format!("http://{address}/notify"),
            token_env: None,
        }),
        schedule: Schedule::parse("America/New_York", "mon,tue,wed,thu,fri", 9, 0)
            .expect("schedule"),
        request_timeout: Duration::from_secs(5),
        max_response_bytes: 64 * 1024,
        max_messages_per_source: 20,
        max_notifications_per_run: 1,
        reminder_interval: ChronoDuration::hours(24),
        lease_duration: ChronoDuration::minutes(10),
        pending_retry_limit: 10,
        user_agent: "email-attention-test".to_owned(),
    };
    let client = Client::builder()
        .timeout(settings.request_timeout)
        .redirect(Policy::none())
        .build()
        .expect("test client");
    let agent = EmailAttentionAgent {
        client,
        settings: Arc::new(settings),
        store: Some(store),
        run_lock: Arc::new(AsyncMutex::new(())),
        lease_holder: Arc::new(Uuid::new_v4().to_string()),
    };
    Some((agent, state, source_id))
}

#[test]
fn direct_request_is_explainable_and_needs_reply() {
    let now = Utc
        .with_ymd_and_hms(2026, 7, 30, 13, 0, 0)
        .single()
        .expect("timestamp");
    let candidate = classify_message(&source(), &message(), now).expect("attention item");
    assert_eq!(candidate.bucket, AttentionBucket::NeedsReplySoon);
    assert!(candidate.item.reason.contains("next expected responder"));
    assert!(candidate.item.deadline.is_none());
    assert!(!candidate.item.reference.contains("thread-1"));
}

#[test]
fn explicit_near_deadline_is_urgent_and_labeled_explicit() {
    let now = Utc
        .with_ymd_and_hms(2026, 7, 30, 13, 0, 0)
        .single()
        .expect("timestamp");
    let mut message = message();
    message.explicit_deadline = Some(now + chrono::Duration::hours(2));
    let candidate = classify_message(&source(), &message, now).expect("attention item");
    assert_eq!(candidate.bucket, AttentionBucket::Urgent);
    assert_eq!(
        candidate.item.deadline.as_ref().map(|value| value.source),
        Some("explicit")
    );
}

#[test]
fn unchanged_item_is_suppressed_until_overdue_reminder_interval() {
    let now = Utc
        .with_ymd_and_hms(2026, 7, 30, 13, 0, 0)
        .single()
        .expect("timestamp");
    let mut message = message();
    message.explicit_deadline = Some(now - chrono::Duration::hours(1));
    let candidate = classify_message(&source(), &message, now).expect("attention item");
    let recent = ItemState {
        last_emitted_fingerprint: Some(candidate.fingerprint.clone()),
        last_emitted_at: Some(now - chrono::Duration::hours(2)),
        pending_delivery_key: None,
    };
    assert!(!should_emit(
        &candidate,
        Some(&recent),
        now,
        chrono::Duration::hours(24)
    ));
    let old = ItemState {
        last_emitted_at: Some(now - chrono::Duration::hours(25)),
        ..recent
    };
    assert!(should_emit(
        &candidate,
        Some(&old),
        now,
        chrono::Duration::hours(24)
    ));
}

#[test]
fn urgent_keyword_explains_why_the_item_is_urgent() {
    let now = Utc
        .with_ymd_and_hms(2026, 7, 30, 13, 0, 0)
        .single()
        .expect("timestamp");
    let mut message = message();
    message.subject = "Urgent: please confirm your interview".to_owned();
    let candidate = classify_message(&source(), &message, now).expect("attention item");
    assert_eq!(candidate.bucket, AttentionBucket::Urgent);
    assert!(candidate.item.reason.contains("time-sensitive language"));
}

#[tokio::test]
async fn manual_preview_preserves_cursor_and_scheduled_runs_deduplicate_delivery() {
    let Some((agent, adapter, source_id)) = test_agent().await else {
        return;
    };
    let store = agent.store.as_ref().expect("store");

    let preview = agent
        .run_manual_test(ManualEmailAttentionRunRequest { deliver: false })
        .await
        .expect("manual preview");
    assert!(preview.test_run);
    assert!(!preview.cursor_advanced);
    assert_eq!(preview.notification_status, "test_preview_only");
    assert!(store
        .source_cursor(&source_id)
        .await
        .expect("manual cursor")
        .is_none());
    assert!(adapter
        .notification_requests
        .lock()
        .expect("notification requests")
        .is_empty());

    let first = agent
        .run_scheduled()
        .await
        .expect("scheduled run")
        .expect("scheduler lease");
    assert!(!first.cursor_advanced);
    assert_eq!(first.truncated_item_count, 1);
    assert_eq!(first.notification_status, "delivered");
    assert!(store
        .source_cursor(&source_id)
        .await
        .expect("held cursor")
        .is_none());

    let second = agent
        .run_scheduled()
        .await
        .expect("second scheduled run")
        .expect("scheduler lease");
    assert!(second.cursor_advanced);
    assert_eq!(second.truncated_item_count, 0);
    assert_eq!(second.notification_status, "delivered");
    assert_eq!(
        store
            .source_cursor(&source_id)
            .await
            .expect("scheduled cursor")
            .as_deref(),
        Some("cursor-1")
    );

    let third = agent
        .run_scheduled()
        .await
        .expect("third scheduled run")
        .expect("scheduler lease");
    assert!(!third.cursor_advanced);
    assert_eq!(third.notification_status, "silent");

    let notifications = adapter
        .notification_requests
        .lock()
        .expect("notification requests");
    assert_eq!(notifications.len(), 2);
    let serialized = serde_json::to_string(&*notifications).expect("notification JSON");
    assert!(!serialized.contains("Could you confirm by tomorrow"));
    assert!(!serialized.contains("review the proposal and reply"));
    assert!(!serialized.contains("snippet"));
    drop(notifications);

    let keys = adapter.notification_keys.lock().expect("notification keys");
    assert_eq!(keys.len(), 2);
    assert!(keys.iter().all(|key| key.starts_with("email-attention:")));
    assert_ne!(keys[0], keys[1]);
    drop(keys);

    let scans = adapter.scan_requests.lock().expect("scan requests");
    assert_eq!(scans.len(), 4);
    assert_eq!(scans[0]["manual_test"], true);
    assert_eq!(scans[0]["read_only"], true);
    assert!(scans[0]["cursor"].is_null());
    assert_eq!(scans[1]["manual_test"], false);
    assert_eq!(scans[1]["cursor"], Value::Null);
    assert_eq!(scans[2]["cursor"], Value::Null);
    assert_eq!(scans[3]["cursor"], "cursor-1");
}

#[test]
fn ordinary_automated_mail_is_ignored() {
    let now = Utc
        .with_ymd_and_hms(2026, 7, 30, 13, 0, 0)
        .single()
        .expect("timestamp");
    let mut message = message();
    message.automated = true;
    message.direct_request = false;
    message.user_is_next_responder = false;
    message.categories.clear();
    message.subject = "Weekly newsletter".to_owned();
    message.snippet = Some("Here are this week's updates".to_owned());
    assert!(classify_message(&source(), &message, now).is_none());
}
