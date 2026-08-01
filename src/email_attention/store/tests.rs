use chrono::{Duration as ChronoDuration, Utc};
use serde_json::json;
use uuid::Uuid;

use super::{AttentionStore, DeliveryItem, SeenItem};

async fn test_store() -> Option<AttentionStore> {
    let Ok(url) = std::env::var("EMAIL_ATTENTION_TEST_DATABASE_URL") else {
        eprintln!(
            "skipping email-attention PostgreSQL test: EMAIL_ATTENTION_TEST_DATABASE_URL is not set"
        );
        return None;
    };
    Some(
        AttentionStore::connect(&url)
            .await
            .expect("connect to the email-attention test database"),
    )
}

#[tokio::test]
async fn delivery_success_advances_only_the_delivered_fingerprint() {
    let Some(store) = test_store().await else {
        return;
    };
    let source_id = format!("mailbox-{}", Uuid::new_v4());
    let delivery_key = format!("delivery-{}", Uuid::new_v4());
    let now = Utc::now();
    let seen = SeenItem {
        source_id: source_id.clone(),
        stable_id: "thread-1".to_owned(),
        fingerprint: "fingerprint-a".to_owned(),
        bucket: "needs_reply_soon".to_owned(),
        deadline_at: None,
        seen_at: now,
    };
    store
        .ensure_source(&source_id, "gmail", now)
        .await
        .expect("ensure source");
    store.record_seen_item(&seen).await.expect("record item");
    store
        .create_pending_delivery(
            &delivery_key,
            &json!({}),
            &[DeliveryItem {
                source_id: seen.source_id.clone(),
                stable_id: seen.stable_id.clone(),
                fingerprint: seen.fingerprint.clone(),
            }],
            now,
        )
        .await
        .expect("create delivery");

    let changed = SeenItem {
        fingerprint: "fingerprint-b".to_owned(),
        seen_at: now + ChronoDuration::minutes(1),
        ..seen
    };
    store
        .record_seen_item(&changed)
        .await
        .expect("record change");
    store
        .mark_delivery_success(&delivery_key, now + ChronoDuration::minutes(2))
        .await
        .expect("deliver");

    let state = store
        .item_state(&source_id, "thread-1")
        .await
        .expect("state")
        .expect("item");
    assert_eq!(
        state.last_emitted_fingerprint.as_deref(),
        Some("fingerprint-a")
    );
    assert!(state.pending_delivery_key.is_none());
    assert_eq!(
        store
            .delivery_payload(&delivery_key)
            .await
            .expect("delivery payload"),
        Some(json!({"redacted": true}))
    );
}

#[tokio::test]
async fn source_success_without_a_new_cursor_preserves_the_previous_cursor() {
    let Some(store) = test_store().await else {
        return;
    };
    let source_id = format!("mailbox-{}", Uuid::new_v4());
    let now = Utc::now();
    store
        .record_source_success(&source_id, "gmail", Some("cursor-a"), now)
        .await
        .expect("initial cursor");
    store
        .record_source_success(&source_id, "gmail", None, now + ChronoDuration::minutes(1))
        .await
        .expect("preserve cursor");
    assert_eq!(
        store
            .source_cursor(&source_id)
            .await
            .expect("cursor")
            .as_deref(),
        Some("cursor-a")
    );
}

#[tokio::test]
async fn scheduler_lease_is_single_holder_until_expiry() {
    let Some(store) = test_store().await else {
        return;
    };
    let lease_name = format!("scheduler-{}", Uuid::new_v4());
    let now = Utc::now();
    assert!(store
        .try_acquire_lease(
            &lease_name,
            "holder-a",
            now,
            now + ChronoDuration::minutes(10)
        )
        .await
        .expect("lease"));
    assert!(!store
        .try_acquire_lease(
            &lease_name,
            "holder-b",
            now + ChronoDuration::minutes(1),
            now + ChronoDuration::minutes(11)
        )
        .await
        .expect("lease"));
    assert!(store
        .try_acquire_lease(
            &lease_name,
            "holder-b",
            now + ChronoDuration::minutes(11),
            now + ChronoDuration::minutes(21)
        )
        .await
        .expect("lease"));
}
