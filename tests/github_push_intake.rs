use std::env;

use ai_agent_coordinator::{
    db::Database,
    webhooks::{process_github_webhook, GithubWebhookPolicy},
};
use axum::{
    body::Bytes,
    http::{HeaderMap, HeaderValue},
};
use hmac::{Hmac, Mac};
use serde_json::{json, Value};
use sha2::Sha256;

const SONUS_SECRET: &str = "sonus-test-webhook-secret";
const DAEDALUS_SECRET: &str = "daedalus-test-webhook-secret";
const SONUS_REPOSITORY: &str = "sonus-auris/sonus-auris-site.web";
const DAEDALUS_REPOSITORY: &str = "daedalus-fab/daedalus-clients";
const AFTER_A: &str = "3333333333333333333333333333333333333333";
const AFTER_B: &str = "4444444444444444444444444444444444444444";

#[tokio::test]
async fn signed_push_policy_rejects_untrusted_events_and_parses_multiple_commits() {
    configure_policy_environment();

    let policy = GithubWebhookPolicy::from_env(None).expect("load webhook policy");
    let Ok(database_url) = env::var("TEST_DATABASE_URL") else {
        eprintln!("skipping PostgreSQL integration test: TEST_DATABASE_URL is not set");
        clear_policy_environment();
        return;
    };
    let database = Database::open(&database_url)
        .await
        .expect("connect to test database");

    let accepted_body = push_body(
        SONUS_REPOSITORY,
        "refs/heads/main",
        AFTER_A,
        false,
        false,
        false,
        vec![
            json!({
                "id": AFTER_A,
                "message": "Refs DEN-453\nFixes den-455"
            }),
            json!({
                "id": AFTER_B,
                "message": "Related to DEN-60\nImplements DEN-456"
            }),
        ],
    );
    let accepted = send(
        &database,
        &policy,
        "sonus-delivery-one",
        SONUS_SECRET,
        accepted_body.clone(),
    )
    .await
    .expect("accept allowlisted signed push");
    assert_eq!(accepted["accepted"], true);
    assert_eq!(accepted["job"]["task_type"], "github_push");

    let directives = accepted["job"]["payload"]["coordinator"]["linear_directives"]
        .as_array()
        .expect("linear directives array");
    assert_eq!(directives.len(), 4);
    assert_eq!(directives[0]["issue_identifier"], "DEN-453");
    assert_eq!(directives[0]["closes_issue"], false);
    assert_eq!(directives[1]["issue_identifier"], "DEN-455");
    assert_eq!(directives[1]["closes_issue"], true);
    assert_eq!(directives[2]["issue_identifier"], "DEN-60");
    assert_eq!(directives[2]["closes_issue"], false);
    assert_eq!(directives[3]["issue_identifier"], "DEN-456");
    assert_eq!(directives[3]["closes_issue"], true);

    let replay = send(
        &database,
        &policy,
        "sonus-delivery-two",
        SONUS_SECRET,
        accepted_body,
    )
    .await
    .expect("deduplicate by repository and commit");
    assert_eq!(accepted["job"]["id"], replay["job"]["id"]);

    let daedalus_body = push_body(
        DAEDALUS_REPOSITORY,
        "refs/heads/main",
        AFTER_B,
        false,
        false,
        false,
        vec![json!({"id": AFTER_B, "message": "Refs DEN-138"})],
    );
    assert!(send(
        &database,
        &policy,
        "wrong-org-secret",
        SONUS_SECRET,
        daedalus_body.clone(),
    )
    .await
    .is_err());
    assert_eq!(
        send(
            &database,
            &policy,
            "correct-org-secret",
            DAEDALUS_SECRET,
            daedalus_body,
        )
        .await
        .expect("select Daedalus organization secret")["accepted"],
        true
    );

    let unknown_repository = push_body(
        "sonus-auris/not-allowlisted",
        "refs/heads/main",
        "cccccccccccccccccccccccccccccccccccccccc",
        false,
        false,
        false,
        vec![],
    );
    let ignored = send(
        &database,
        &policy,
        "unknown-repository",
        SONUS_SECRET,
        unknown_repository,
    )
    .await
    .expect("return an audited ignore decision");
    assert_eq!(ignored["accepted"], false);
    assert!(ignored["reason"]
        .as_str()
        .is_some_and(|reason| reason.contains("allowlist")));

    for (delivery, body, expected_reason) in [
        (
            "fork",
            push_body(
                SONUS_REPOSITORY,
                "refs/heads/main",
                "dddddddddddddddddddddddddddddddddddddddd",
                true,
                false,
                false,
                vec![],
            ),
            "fork repository",
        ),
        (
            "deleted",
            push_body(
                SONUS_REPOSITORY,
                "refs/heads/main",
                "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                false,
                true,
                false,
                vec![],
            ),
            "deleted branch",
        ),
        (
            "forced",
            push_body(
                SONUS_REPOSITORY,
                "refs/heads/main",
                "ffffffffffffffffffffffffffffffffffffffff",
                false,
                false,
                true,
                vec![],
            ),
            "force pushes",
        ),
        (
            "feature-branch",
            push_body(
                SONUS_REPOSITORY,
                "refs/heads/feature",
                "1111111111111111111111111111111111111111",
                false,
                false,
                false,
                vec![],
            ),
            "not the configured default branch",
        ),
    ] {
        let response = send(&database, &policy, delivery, SONUS_SECRET, body)
            .await
            .expect("return an audited ignore decision");
        assert_eq!(response["accepted"], false);
        assert!(response["reason"]
            .as_str()
            .is_some_and(|reason| reason.contains(expected_reason)));
    }

    let malformed_commit = push_body(
        SONUS_REPOSITORY,
        "refs/heads/main",
        "not-a-commit",
        false,
        false,
        false,
        vec![],
    );
    assert!(send(
        &database,
        &policy,
        "malformed-commit",
        SONUS_SECRET,
        malformed_commit,
    )
    .await
    .is_err());

    let unknown_org_body = push_body(
        "unknown-org/repository",
        "refs/heads/main",
        "2222222222222222222222222222222222222222",
        false,
        false,
        false,
        vec![],
    );
    assert!(send(
        &database,
        &policy,
        "unknown-organization",
        SONUS_SECRET,
        unknown_org_body,
    )
    .await
    .is_err());

    clear_policy_environment();
}

fn configure_policy_environment() {
    env::set_var(
        "GITHUB_WEBHOOK_ORG_SECRET_ENVS",
        "sonus-auris=SONUS_TEST_WEBHOOK_SECRET,daedalus-fab=DAEDALUS_TEST_WEBHOOK_SECRET",
    );
    env::set_var("SONUS_TEST_WEBHOOK_SECRET", SONUS_SECRET);
    env::set_var("DAEDALUS_TEST_WEBHOOK_SECRET", DAEDALUS_SECRET);
    env::set_var(
        "GITHUB_PUSH_ALLOWED_REPOSITORIES",
        format!("{SONUS_REPOSITORY},{DAEDALUS_REPOSITORY}"),
    );
    env::set_var(
        "GITHUB_PUSH_DEFAULT_BRANCHES",
        format!("{SONUS_REPOSITORY}=main,{DAEDALUS_REPOSITORY}=main"),
    );
    env::set_var("GITHUB_AUTO_ENQUEUE_PUSHES", "true");
}

fn clear_policy_environment() {
    for variable in [
        "GITHUB_WEBHOOK_ORG_SECRET_ENVS",
        "SONUS_TEST_WEBHOOK_SECRET",
        "DAEDALUS_TEST_WEBHOOK_SECRET",
        "GITHUB_PUSH_ALLOWED_REPOSITORIES",
        "GITHUB_PUSH_DEFAULT_BRANCHES",
        "GITHUB_AUTO_ENQUEUE_PUSHES",
    ] {
        env::remove_var(variable);
    }
}

fn push_body(
    repository: &str,
    pushed_ref: &str,
    after: &str,
    fork: bool,
    deleted: bool,
    forced: bool,
    commits: Vec<Value>,
) -> Bytes {
    Bytes::from(
        serde_json::to_vec(&json!({
            "ref": pushed_ref,
            "after": after,
            "deleted": deleted,
            "forced": forced,
            "repository": {
                "full_name": repository,
                "default_branch": "main",
                "fork": fork
            },
            "commits": commits
        }))
        .expect("serialize push body"),
    )
}

async fn send(
    database: &Database,
    policy: &GithubWebhookPolicy,
    delivery: &str,
    secret: &str,
    body: Bytes,
) -> Result<Value, ai_agent_coordinator::error::AppError> {
    process_github_webhook(
        database,
        &push_headers(delivery, secret, &body),
        body,
        policy,
        &[],
        &[],
        false,
    )
    .await
}

fn push_headers(delivery: &str, secret: &str, body: &[u8]) -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert("x-github-event", HeaderValue::from_static("push"));
    headers.insert(
        "x-github-delivery",
        HeaderValue::from_str(delivery).expect("valid delivery header"),
    );
    headers.insert(
        "x-hub-signature-256",
        HeaderValue::from_str(&signature(secret, body)).expect("valid signature header"),
    );
    headers
}

fn signature(secret: &str, body: &[u8]) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).expect("valid HMAC key");
    mac.update(body);
    format!("sha256={}", hex::encode(mac.finalize().into_bytes()))
}
