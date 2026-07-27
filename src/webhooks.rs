use axum::{body::Bytes, http::HeaderMap};
use hmac::{Hmac, Mac};
use serde_json::{json, Value};
use sha2::Sha256;

use crate::{
    db::Database,
    error::AppError,
    jobs::CreateJobRequest,
};

pub fn verify_signature(
    headers: &HeaderMap,
    body: &[u8],
    webhook_secret: Option<&str>,
) -> Result<(), AppError> {
    let secret = webhook_secret.ok_or_else(|| {
        AppError::Forbidden("GitHub webhook ingestion is not configured".to_owned())
    })?;
    let signature = headers
        .get("x-hub-signature-256")
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("sha256="))
        .ok_or_else(|| AppError::Unauthorized)?;
    let signature = hex::decode(signature).map_err(|_| AppError::Unauthorized)?;

    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes())
        .map_err(|_| AppError::Internal(anyhow::anyhow!("invalid webhook secret")))?;
    mac.update(body);
    mac.verify_slice(&signature)
        .map_err(|_| AppError::Unauthorized)
}

pub fn enqueue_from_github(
    database: &Database,
    headers: &HeaderMap,
    body: Bytes,
    issue_trigger_labels: &[String],
    review_trigger_labels: &[String],
    auto_enqueue_failed_workflows: bool,
) -> Result<Value, AppError> {
    let event = header(headers, "x-github-event")?.to_owned();
    let delivery = header(headers, "x-github-delivery")?.to_owned();
    let payload: Value = serde_json::from_slice(&body)
        .map_err(|error| AppError::BadRequest(format!("invalid webhook JSON: {error}")))?;

    let repository_full_name = payload
        .pointer("/repository/full_name")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::BadRequest("webhook is missing repository.full_name".into()))?;
    let (org, repo) = repository_full_name
        .split_once('/')
        .ok_or_else(|| AppError::BadRequest("invalid repository.full_name".into()))?;
    let org = org.to_owned();
    let repo = repo.to_owned();

    let action = payload
        .get("action")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_owned();
    let labels = payload_labels(&payload);

    let task_type = match event.as_str() {
        "issues"
            if matches!(action.as_str(), "opened" | "reopened" | "labeled")
                && has_any_label(&labels, issue_trigger_labels) =>
        {
            Some("github_issue")
        }
        "pull_request"
            if matches!(action.as_str(), "opened" | "reopened" | "synchronize" | "labeled")
                && has_any_label(&labels, review_trigger_labels) =>
        {
            Some("github_pr_review")
        }
        "workflow_run"
            if auto_enqueue_failed_workflows
                && action == "completed"
                && payload
                    .pointer("/workflow_run/conclusion")
                    .and_then(Value::as_str)
                    .is_some_and(|value| matches!(value, "failure" | "timed_out")) =>
        {
            Some("github_ci_failure")
        }
        _ => None,
    };

    let Some(task_type) = task_type else {
        return Ok(json!({
            "accepted": false,
            "reason": "event did not match an enqueue rule",
            "event": event,
            "action": action,
        }));
    };

    let request = CreateJobRequest {
        org,
        repo,
        task_type: task_type.to_owned(),
        payload,
        priority: if task_type == "github_ci_failure" { 50 } else { 0 },
        max_attempts: 3,
        available_at: None,
        budget_usd: None,
    };
    let idempotency_key = format!("github:{delivery}:{event}:{action}");
    let job = database
        .create_job(&request, Some(&idempotency_key))
        .map_err(AppError::Internal)?;

    Ok(json!({
        "accepted": true,
        "job": job,
    }))
}

fn header<'a>(headers: &'a HeaderMap, name: &str) -> Result<&'a str, AppError> {
    headers
        .get(name)
        .ok_or_else(|| AppError::BadRequest(format!("missing {name} header")))?
        .to_str()
        .map_err(|_| AppError::BadRequest(format!("invalid {name} header")))
}

fn payload_labels(payload: &Value) -> Vec<String> {
    payload
        .pointer("/issue/labels")
        .or_else(|| payload.pointer("/pull_request/labels"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|label| label.get("name").and_then(Value::as_str))
        .map(str::to_owned)
        .collect()
}

fn has_any_label(labels: &[String], triggers: &[String]) -> bool {
    labels.iter().any(|label| triggers.contains(label))
}
