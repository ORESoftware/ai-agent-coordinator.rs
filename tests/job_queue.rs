use ai_agent_coordinator::{
    config::WorkerConfig,
    db::Database,
    jobs::{ClaimJobRequest, CompleteJobRequest, CompletionOutcome, CreateJobRequest, JobStatus},
};
use serde_json::json;

#[test]
fn job_lifecycle_is_leased_and_idempotent() {
    let database = Database::open(":memory:").unwrap();
    let request = CreateJobRequest {
        org: "oresoftware".to_owned(),
        repo: "coordinator".to_owned(),
        task_type: "code_change".to_owned(),
        payload: json!({"ticket": "ENG-1"}),
        priority: 10,
        max_attempts: 3,
        available_at: None,
        budget_usd: Some(1.0),
    };

    let first = database.create_job(&request, Some("linear:ENG-1")).unwrap();
    let duplicate = database.create_job(&request, Some("linear:ENG-1")).unwrap();
    assert_eq!(first.id, duplicate.id);

    let claimed = database
        .claim_job(
            &ClaimJobRequest {
                worker_id: "worker-1".to_owned(),
                orgs: vec!["oresoftware".to_owned()],
                repositories: vec![],
                lease_seconds: 60,
            },
            &WorkerConfig::default(),
        )
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
        .unwrap();
    assert_eq!(completed.status, JobStatus::Succeeded);
}

#[test]
fn repository_concurrency_cap_prevents_overclaiming() {
    let database = Database::open(":memory:").unwrap();
    for ticket in ["ENG-2", "ENG-3"] {
        database
            .create_job(
                &CreateJobRequest {
                    org: "oresoftware".to_owned(),
                    repo: "busy-repo".to_owned(),
                    task_type: "code_change".to_owned(),
                    payload: json!({"ticket": ticket}),
                    priority: 0,
                    max_attempts: 3,
                    available_at: None,
                    budget_usd: None,
                },
                Some(ticket),
            )
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
        orgs: vec!["oresoftware".to_owned()],
        repositories: vec!["busy-repo".to_owned()],
        lease_seconds: 60,
    };

    assert!(database
        .claim_job(&claim("worker-1"), &worker_config)
        .unwrap()
        .is_some());
    assert!(database
        .claim_job(&claim("worker-2"), &worker_config)
        .unwrap()
        .is_none());
}
