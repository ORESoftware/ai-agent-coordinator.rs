#[cfg(test)]
mod tests {
    use chrono::{Duration as ChronoDuration, Utc};

    use super::{AttentionStore, DeliveryItem, SeenItem};

    #[test]
    fn delivery_success_advances_only_the_delivered_fingerprint() {
        let store = AttentionStore::open(":memory:").expect("store");
        let now = Utc::now();
        let seen = SeenItem {
            source_id: "mailbox-a".to_owned(),
            stable_id: "thread-1".to_owned(),
            fingerprint: "fingerprint-a".to_owned(),
            bucket: "needs_reply_soon".to_owned(),
            deadline_at: None,
            seen_at: now,
        };
        store.record_seen_item(&seen).expect("record item");
        store
            .create_pending_delivery(
                "delivery-a",
                "{}",
                &[DeliveryItem {
                    source_id: seen.source_id.clone(),
                    stable_id: seen.stable_id.clone(),
                    fingerprint: seen.fingerprint.clone(),
                }],
                now,
            )
            .expect("create delivery");

        let changed = SeenItem {
            fingerprint: "fingerprint-b".to_owned(),
            seen_at: now + ChronoDuration::minutes(1),
            ..seen
        };
        store.record_seen_item(&changed).expect("record change");
        store
            .mark_delivery_success("delivery-a", now + ChronoDuration::minutes(2))
            .expect("deliver");

        let state = store
            .item_state("mailbox-a", "thread-1")
            .expect("state")
            .expect("item");
        assert_eq!(
            state.last_emitted_fingerprint.as_deref(),
            Some("fingerprint-a")
        );
        assert!(state.pending_delivery_key.is_none());
        assert_eq!(
            store
                .delivery_payload("delivery-a")
                .expect("delivery payload")
                .as_deref(),
            Some(r#"{"redacted":true}"#)
        );
    }

    #[test]
    fn source_success_without_a_new_cursor_preserves_the_previous_cursor() {
        let store = AttentionStore::open(":memory:").expect("store");
        let now = Utc::now();
        store
            .record_source_success("mailbox-a", "gmail", Some("cursor-a"), now)
            .expect("initial cursor");
        store
            .record_source_success(
                "mailbox-a",
                "gmail",
                None,
                now + ChronoDuration::minutes(1),
            )
            .expect("preserve cursor");
        assert_eq!(
            store.source_cursor("mailbox-a").expect("cursor").as_deref(),
            Some("cursor-a")
        );
    }

    #[test]
    fn scheduler_lease_is_single_holder_until_expiry() {
        let store = AttentionStore::open(":memory:").expect("store");
        let now = Utc::now();
        assert!(store
            .try_acquire_lease(
                "scheduler",
                "holder-a",
                now,
                now + ChronoDuration::minutes(10)
            )
            .expect("lease"));
        assert!(!store
            .try_acquire_lease(
                "scheduler",
                "holder-b",
                now + ChronoDuration::minutes(1),
                now + ChronoDuration::minutes(11)
            )
            .expect("lease"));
        assert!(store
            .try_acquire_lease(
                "scheduler",
                "holder-b",
                now + ChronoDuration::minutes(11),
                now + ChronoDuration::minutes(21)
            )
            .expect("lease"));
    }
}
