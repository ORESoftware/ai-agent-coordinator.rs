use ai_agent_coordinator::{
    config::WorkerConfig,
    db::Database,
    jobs::{ClaimJobRequest, CompleteJobRequest, CompletionOutcome, CreateJobRequest, JobStatus},
};
use serde_json::json;
use uuid::Uuid;

async fn test_database() -> Option<Database> {
    let Ok(url) = std::env::var("TEST_DATABASE_URL") else {
        eprintln!("skipping PostgreSQL integration test: TEST_DATABASE_URL is not set");
        return None;
    };
    Some(
        Database::open(&url)
            .await
            .expect("connect to test database"),
    )
}

#[tokio::test]
async fn job_lifecycle_is_leased_and_idempotent() {
    let Some(database) = test_database().await else {
        return;
    };
    let org = format!("job-lifecycle-{}", Uuid::new_v4());
    let idempotency_key = format!("linear:{}", Uuid::new_v4());
    let request = CreateJobRequest {
        org: org.clone(),
        repo: "coordinator".to_owned(),
        task_type: "code_change".to_owned(),
        payload: json!({"ticket": "ENG-1"}),
        priority: 10,
        max_attempts: 3,
        available_at: None,
        budget_usd: Some(1.0),
    };

    let first = database
        .create_job(&request, Some(&idempotency_key))
        .await
        .unwrap();
    let duplicate = database
        .create_job(&request, Some(&idempotency_key))
        .await
        .unwrap();
    assert_eq!(first.id, duplicate.id);

    let claimed = database
        .claim_job(
            &ClaimJobRequest {
                worker_id: "worker-1".to_owned(),
                orgs: vec![org],
                repositories: vec![],
                task_types: vec![],
                lease_seconds: 60,
            },
            &WorkerConfig::default(),
        )
        .await
        .unwrap()
        .unwrap();
    assert_eq!(claimed.status, JobStatus::Running);
    assert_eq!(claimed.attempts, 1);

    let completed = database
        .complete_job(
            &claimed.id,
            &CompleteJobRequest {
                worker_id: "worker-1".to_owned(),
                outcome: CompletionOutcome::Succeeded,
                result: Some(json!({"pr": 42})),
                error: None,
                retryable: false,
                retry_delay_seconds: 0,
            },
        )
        .await
        .unwrap();
    assert_eq!(completed.status, JobStatus::Succeeded);
}

#[tokio::test]
async fn repository_concurrency_cap_prevents_overclaiming() {
    let Some(database) = test_database().await else {
        return;
    };
    let org = format!("repo-cap-{}", Uuid::new_v4());
    for ticket in ["ENG-2", "ENG-3"] {
        database
            .create_job(
                &CreateJobRequest {
                    org: org.clone(),
                    repo: "busy-repo".to_owned(),
                    task_type: "code_change".to_owned(),
                    payload: json!({"ticket": ticket}),
                    priority: 0,
                    max_attempts: 3,
                    available_at: None,
                    budget_usd: None,
                },
                Some(&format!("{ticket}:{}", Uuid::new_v4())),
            )
            .await
            .unwrap();
    }

    let worker_config = WorkerConfig {
        default_org_concurrency: 10,
        default_repo_concurrency: 1,
        org_concurrency: Default::default(),
        repo_concurrency: Default::default(),
    };
    let claim = |worker_id: &str| ClaimJobRequest {
        worker_id: worker_id.to_owned(),
        orgs: vec![org.clone()],
        repositories: vec!["busy-repo".to_owned()],
        task_types: vec![],
        lease_seconds: 60,
    };

    let first_request = claim("worker-1");
    let second_request = claim("worker-2");
    let (first, second) = tokio::join!(
        database.claim_job(&first_request, &worker_config),
        database.claim_job(&second_request, &worker_config),
    );
    let claimed = [first.unwrap(), second.unwrap()]
        .into_iter()
        .filter(Option::is_some)
        .count();
    assert_eq!(claimed, 1);
}
