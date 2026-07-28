use std::{
    collections::{HashMap, HashSet},
    env,
};

use anyhow::{bail, Context};
use axum::{body::Bytes, http::HeaderMap};
use hmac::{Hmac, Mac};
use regex::Regex;
use serde::Serialize;
use serde_json::{json, Value};
use sha2::Sha256;

use crate::{db::Database, error::AppError, jobs::CreateJobRequest};

const ORG_SECRET_ENV_MAP: &str = "GITHUB_WEBHOOK_ORG_SECRET_ENVS";
const PUSH_ALLOWED_REPOSITORIES: &str = "GITHUB_PUSH_ALLOWED_REPOSITORIES";
const PUSH_DEFAULT_BRANCHES: &str = "GITHUB_PUSH_DEFAULT_BRANCHES";
const AUTO_ENQUEUE_PUSHES: &str = "GITHUB_AUTO_ENQUEUE_PUSHES";

#[derive(Debug, Clone, Default)]
pub struct GithubWebhookPolicy {
    default_secret: Option<String>,
    organization_secrets: HashMap<String, String>,
    push_allowed_repositories: HashSet<String>,
    push_default_branches: HashMap<String, String>,
    auto_enqueue_pushes: bool,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct LinearDirective {
    pub commit_id: String,
    pub issue_identifier: String,
    pub keyword: String,
    pub closes_issue: bool,
}

impl GithubWebhookPolicy {
    pub fn from_env(default_secret: Option<String>) -> anyhow::Result<Self> {
        let secret_env_names = parse_mapping(&env::var(ORG_SECRET_ENV_MAP).unwrap_or_default(), ORG_SECRET_ENV_MAP)?;
        let mut organization_secrets = HashMap::new();
        for (organization, secret_env) in secret_env_names {
            let secret = env::var(&secret_env)
                .with_context(|| format!("{ORG_SECRET_ENV_MAP} refers to unset {secret_env}"))?;
            if secret.is_empty() {
                bail!("{ORG_SECRET_ENV_MAP} refers to empty {secret_env}");
            }
            organization_secrets.insert(organization, secret);
        }

        let push_allowed_repositories = env::var(PUSH_ALLOWED_REPOSITORIES)
            .unwrap_or_default()
            .split(',')
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .collect::<HashSet<_>>();
        let push_default_branches = parse_mapping(
            &env::var(PUSH_DEFAULT_BRANCHES).unwrap_or_default(),
            PUSH_DEFAULT_BRANCHES,
        )?;
        let auto_enqueue_pushes = parse_bool_env(AUTO_ENQUEUE_PUSHES, false)?;

        if auto_enqueue_pushes && organization_secrets.is_empty() {
            bail!("{AUTO_ENQUEUE_PUSHES}=true requires per-organization secrets in {ORG_SECRET_ENV_MAP}");
        }
        if auto_enqueue_pushes && push_allowed_repositories.is_empty() {
            bail!("{AUTO_ENQUEUE_PUSHES}=true requires an explicit {PUSH_ALLOWED_REPOSITORIES} allowlist");
        }

        Ok(Self {
            default_secret,
            organization_secrets,
            push_allowed_repositories,
            push_default_branches,
            auto_enqueue_pushes,
        })
    }

    fn secret_for_event(&self, event: &str, organization: &str) -> Option<&str> {
        self.organization_secrets
            .get(organization)
            .map(String::as_str)
            .or_else(|| (event != "push").then_some(self.default_secret.as_deref()).flatten())
    }

    fn push_default_branch<'a>(&'a self, repository: &str, payload: &'a Value) -> Option<&'a str> {
        self.push_default_branches
            .get(repository)
            .map(String::as_str)
            .or_else(|| {
                payload
                    .pointer("/repository/default_branch")
                    .and_then(Value::as_str)
            })
    }

    #[cfg(test)]
    fn test_policy(
        organization: &str,
        secret: &str,
        allowed_repository: &str,
        default_branch: &str,
    ) -> Self {
        Self {
            default_secret: None,
            organization_secrets: HashMap::from([(
                organization.to_owned(),
                secret.to_owned(),
            )]),
            push_allowed_repositories: HashSet::from([allowed_repository.to_owned()]),
            push_default_branches: HashMap::from([(
                allowed_repository.to_owned(),
                default_branch.to_owned(),
            )]),
            auto_enqueue_pushes: true,
        }
    }
}

pub fn process_github_webhook(
    database: &Database,
    headers: &HeaderMap,
    body: Bytes,
    policy: &GithubWebhookPolicy,
    issue_trigger_labels: &[String],
    review_trigger_labels: &[String],
    auto_enqueue_failed_workflows: bool,
) -> Result<Value, AppError> {
    let event = header(headers, "x-github-event")?.to_owned();
    let delivery = header(headers, "x-github-delivery")?.to_owned();
    let mut payload: Value = serde_json::from_slice(&body)
        .map_err(|error| AppError::BadRequest(format!("invalid webhook JSON: {error}")))?;

    let repository_full_name = payload
        .pointer("/repository/full_name")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::BadRequest("webhook is missing repository.full_name".into()))?
        .to_owned();
    let (organization, repository) = repository_full_name
        .split_once('/')
        .ok_or_else(|| AppError::BadRequest("invalid repository.full_name".into()))?;

    let secret = policy
        .secret_for_event(&event, organization)
        .ok_or_else(|| {
            AppError::Forbidden(format!(
                "GitHub webhook ingestion is not configured for organization {organization}"
            ))
        })?;
    verify_signature(headers, &body, Some(secret))?;

    let action = payload
        .get("action")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_owned();
    let labels = payload_labels(&payload);

    let (task_type, priority, idempotency_key) = match event.as_str() {
        "issues"
            if matches!(action.as_str(), "opened" | "reopened" | "labeled")
                && has_any_label(&labels, issue_trigger_labels) =>
        {
            (
                Some("github_issue"),
                0,
                format!("github:{delivery}:{event}:{action}"),
            )
        }
        "pull_request"
            if matches!(
                action.as_str(),
                "opened" | "reopened" | "synchronize" | "labeled"
            ) && has_any_label(&labels, review_trigger_labels) =>
        {
            (
                Some("github_pr_review"),
                0,
                format!("github:{delivery}:{event}:{action}"),
            )
        }
        "workflow_run"
            if auto_enqueue_failed_workflows
                && action == "completed"
                && payload
                    .pointer("/workflow_run/conclusion")
                    .and_then(Value::as_str)
                    .is_some_and(|value| matches!(value, "failure" | "timed_out")) =>
        {
            (
                Some("github_ci_failure"),
                50,
                format!("github:{delivery}:{event}:{action}"),
            )
        }
        "push" => match push_job_metadata(policy, &repository_full_name, &payload)? {
            PushDecision::Enqueue {
                after,
                default_branch,
            } => {
                let directives = extract_linear_directives(&payload);
                payload["coordinator"] = json!({
                    "default_branch": default_branch,
                    "linear_directives": directives,
                });
                (
                    Some("github_push"),
                    10,
                    format!("github:push:{repository_full_name}:{after}"),
                )
            }
            PushDecision::Ignore(reason) => {
                return Ok(json!({
                    "accepted": false,
                    "reason": reason,
                    "event": event,
                    "repository": repository_full_name,
                }));
            }
        },
        _ => (
            None,
            0,
            format!("github:{delivery}:{event}:{action}"),
        ),
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
        org: organization.to_owned(),
        repo: repository.to_owned(),
        task_type: task_type.to_owned(),
        payload,
        priority,
        max_attempts: 3,
        available_at: None,
        budget_usd: None,
    };
    let job = database
        .create_job(&request, Some(&idempotency_key))
        .map_err(AppError::Internal)?;

    Ok(json!({
        "accepted": true,
        "job": job,
    }))
}

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
        .ok_or(AppError::Unauthorized)?;
    let signature = hex::decode(signature).map_err(|_| AppError::Unauthorized)?;

    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes())
        .map_err(|_| AppError::Internal(anyhow::anyhow!("invalid webhook secret")))?;
    mac.update(body);
    mac.verify_slice(&signature)
        .map_err(|_| AppError::Unauthorized)
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum PushDecision {
    Enqueue {
        after: String,
        default_branch: String,
    },
    Ignore(String),
}

fn push_job_metadata(
    policy: &GithubWebhookPolicy,
    repository_full_name: &str,
    payload: &Value,
) -> Result<PushDecision, AppError> {
    if !policy.auto_enqueue_pushes {
        return Ok(PushDecision::Ignore(
            "push ingestion is disabled".to_owned(),
        ));
    }
    if !policy
        .push_allowed_repositories
        .contains(repository_full_name)
    {
        return Ok(PushDecision::Ignore(
            "repository is not in the push allowlist".to_owned(),
        ));
    }
    if payload
        .pointer("/repository/fork")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Ok(PushDecision::Ignore(
            "fork repository pushes are not accepted".to_owned(),
        ));
    }
    if payload.get("deleted").and_then(Value::as_bool).unwrap_or(false) {
        return Ok(PushDecision::Ignore(
            "deleted branch pushes are not accepted".to_owned(),
        ));
    }
    if payload.get("forced").and_then(Value::as_bool).unwrap_or(false) {
        return Ok(PushDecision::Ignore(
            "force pushes are not accepted".to_owned(),
        ));
    }

    let default_branch = policy
        .push_default_branch(repository_full_name, payload)
        .ok_or_else(|| AppError::BadRequest("push payload is missing repository.default_branch".into()))?
        .to_owned();
    let expected_ref = format!("refs/heads/{default_branch}");
    let pushed_ref = payload
        .get("ref")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::BadRequest("push payload is missing ref".into()))?;
    if pushed_ref != expected_ref {
        return Ok(PushDecision::Ignore(format!(
            "push ref {pushed_ref} is not the configured default branch {expected_ref}"
        )));
    }

    let after = payload
        .get("after")
        .and_then(Value::as_str)
        .filter(|value| is_commit_identifier(value))
        .ok_or_else(|| AppError::BadRequest("push payload has an invalid after commit".into()))?
        .to_owned();

    Ok(PushDecision::Enqueue {
        after,
        default_branch,
    })
}

fn extract_linear_directives(payload: &Value) -> Vec<LinearDirective> {
    let pattern = Regex::new(
        r"(?i)\b(fixes|closes|resolves|completes|implements|refs|references|part\s+of|related\s+to|contributes\s+to)\s+([A-Z][A-Z0-9]*-[0-9]+)\b",
    )
    .expect("linear directive regex must compile");
    let closing_keywords = ["fixes", "closes", "resolves", "completes", "implements"];
    let mut seen = HashSet::new();
    let mut directives = Vec::new();

    for commit in payload
        .get("commits")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let Some(commit_id) = commit.get("id").and_then(Value::as_str) else {
            continue;
        };
        let Some(message) = commit.get("message").and_then(Value::as_str) else {
            continue;
        };

        for captures in pattern.captures_iter(message) {
            let keyword = captures[1]
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ")
                .to_ascii_lowercase();
            let issue_identifier = captures[2].to_ascii_uppercase();
            let key = (
                commit_id.to_owned(),
                issue_identifier.clone(),
                keyword.clone(),
            );
            if !seen.insert(key) {
                continue;
            }
            directives.push(LinearDirective {
                commit_id: commit_id.to_owned(),
                issue_identifier,
                closes_issue: closing_keywords.contains(&keyword.as_str()),
                keyword,
            });
        }
    }

    directives
}

fn is_commit_identifier(value: &str) -> bool {
    matches!(value.len(), 40 | 64)
        && value.bytes().all(|byte| byte.is_ascii_hexdigit())
        && value.bytes().any(|byte| byte != b'0')
}

fn parse_mapping(value: &str, variable: &str) -> anyhow::Result<HashMap<String, String>> {
    let mut result = HashMap::new();
    for entry in value.split(',').map(str::trim).filter(|entry| !entry.is_empty()) {
        let (key, mapped_value) = entry
            .split_once('=')
            .with_context(|| format!("invalid {variable} entry {entry:?}; expected key=value"))?;
        let key = key.trim();
        let mapped_value = mapped_value.trim();
        if key.is_empty() || mapped_value.is_empty() {
            bail!("invalid {variable} entry {entry:?}; key and value are required");
        }
        if result
            .insert(key.to_owned(), mapped_value.to_owned())
            .is_some()
        {
            bail!("duplicate {variable} key {key:?}");
        }
    }
    Ok(result)
}

fn parse_bool_env(variable: &str, default: bool) -> anyhow::Result<bool> {
    let Ok(value) = env::var(variable) else {
        return Ok(default);
    };
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Ok(true),
        "0" | "false" | "no" | "off" => Ok(false),
        _ => bail!("{variable} must be a boolean value"),
    }
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

#[cfg(test)]
mod tests {
    use axum::{body::Bytes, http::HeaderValue};
    use hmac::{Hmac, Mac};
    use serde_json::json;
    use sha2::Sha256;

    use super::*;

    const SECRET: &str = "test-webhook-secret";
    const REPOSITORY: &str = "sonus-auris/sonus-auris-site.web";
    const AFTER: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    #[test]
    fn verifies_valid_signatures_and_rejects_invalid_signatures() {
        let body = br#"{"repository":{"full_name":"sonus-auris/example"}}"#;
        let mut headers = HeaderMap::new();
        headers.insert(
            "x-hub-signature-256",
            HeaderValue::from_str(&signature(SECRET, body)).unwrap(),
        );
        assert!(verify_signature(&headers, body, Some(SECRET)).is_ok());
        assert!(verify_signature(&headers, b"changed", Some(SECRET)).is_err());
    }

    #[test]
    fn signed_default_branch_pushes_enqueue_directives_idempotently() {
        let database = Database::open(":memory:").unwrap();
        let policy = GithubWebhookPolicy::test_policy(
            "sonus-auris",
            SECRET,
            REPOSITORY,
            "main",
        );
        let body = Bytes::from(
            serde_json::to_vec(&json!({
                "ref": "refs/heads/main",
                "after": AFTER,
                "deleted": false,
                "forced": false,
                "repository": {
                    "full_name": REPOSITORY,
                    "default_branch": "main",
                    "fork": false
                },
                "commits": [{
                    "id": AFTER,
                    "message": "Refs DEN-453\nFixes den-455"
                }]
            }))
            .unwrap(),
        );
        let first = process_github_webhook(
            &database,
            &push_headers("delivery-one", &body),
            body.clone(),
            &policy,
            &[],
            &[],
            false,
        )
        .unwrap();
        let second = process_github_webhook(
            &database,
            &push_headers("delivery-two", &body),
            body,
            &policy,
            &[],
            &[],
            false,
        )
        .unwrap();

        assert_eq!(first["accepted"], true);
        assert_eq!(first["job"]["id"], second["job"]["id"]);
        assert_eq!(first["job"]["task_type"], "github_push");
        let directives = first["job"]["payload"]["coordinator"]["linear_directives"]
            .as_array()
            .unwrap();
        assert_eq!(directives.len(), 2);
        assert_eq!(directives[0]["issue_identifier"], "DEN-453");
        assert_eq!(directives[0]["closes_issue"], false);
        assert_eq!(directives[1]["issue_identifier"], "DEN-455");
        assert_eq!(directives[1]["closes_issue"], true);
    }

    #[test]
    fn force_pushes_and_non_default_branches_are_ignored() {
        let policy = GithubWebhookPolicy::test_policy(
            "sonus-auris",
            SECRET,
            REPOSITORY,
            "main",
        );
        let forced = json!({
            "ref": "refs/heads/main",
            "after": AFTER,
            "forced": true,
            "repository": {"full_name": REPOSITORY, "default_branch": "main"}
        });
        assert!(matches!(
            push_job_metadata(&policy, REPOSITORY, &forced).unwrap(),
            PushDecision::Ignore(reason) if reason.contains("force pushes")
        ));

        let feature = json!({
            "ref": "refs/heads/feature",
            "after": AFTER,
            "forced": false,
            "repository": {"full_name": REPOSITORY, "default_branch": "main"}
        });
        assert!(matches!(
            push_job_metadata(&policy, REPOSITORY, &feature).unwrap(),
            PushDecision::Ignore(reason) if reason.contains("not the configured default branch")
        ));
    }

    fn push_headers(delivery: &str, body: &[u8]) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert("x-github-event", HeaderValue::from_static("push"));
        headers.insert(
            "x-github-delivery",
            HeaderValue::from_str(delivery).unwrap(),
        );
        headers.insert(
            "x-hub-signature-256",
            HeaderValue::from_str(&signature(SECRET, body)).unwrap(),
        );
        headers
    }

    fn signature(secret: &str, body: &[u8]) -> String {
        let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).unwrap();
        mac.update(body);
        format!("sha256={}", hex::encode(mac.finalize().into_bytes()))
    }
}
