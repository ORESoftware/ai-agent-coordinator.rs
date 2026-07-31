use std::{
    collections::HashMap,
    env,
    net::IpAddr,
    str::FromStr,
    sync::Arc,
    time::{Duration, Instant},
};

use anyhow::{bail, Context};
#[cfg(test)]
use chrono::Utc;
use reqwest::{header::RETRY_AFTER, redirect::Policy, Client, StatusCode, Url};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::{sync::Mutex, time::sleep};

use crate::{
    db::Database,
    jobs::{ClaimJobRequest, Job},
};

const LINEAR_API_TOKEN_ENV: &str = "LINEAR_API_TOKEN";
const DEFAULT_LINEAR_API_URL: &str = "https://api.linear.app/graphql";
const DEFAULT_MAX_RESPONSE_BYTES: usize = 256 * 1024;

const ISSUE_QUERY: &str = r#"
query CoordinatorIssue($id: String!) {
  issue(id: $id) {
    id
    identifier
    url
    team { id key name }
    project { id name }
    state { id name type }
    comments(first: 100) { nodes { id body } }
  }
}
"#;

const ATTACHMENT_MUTATION: &str = r#"
mutation CoordinatorAttachment($input: AttachmentCreateInput!) {
  attachmentCreate(input: $input) {
    success
    attachment { id url }
  }
}
"#;

const COMMENT_MUTATION: &str = r#"
mutation CoordinatorComment($input: CommentCreateInput!) {
  commentCreate(input: $input) {
    success
    comment { id }
  }
}
"#;

const ISSUE_UPDATE_MUTATION: &str = r#"
mutation CoordinatorIssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) {
    success
    issue { id identifier state { id name type } }
  }
}
"#;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LinearAuthScheme {
    ApiKey,
    Bearer,
}

#[derive(Debug, Clone)]
pub struct LinearDeliveryConfig {
    enabled: bool,
    dry_run: bool,
    api_url: Url,
    api_token: Option<String>,
    auth_scheme: LinearAuthScheme,
    team_key: String,
    project_names: HashMap<String, String>,
    completed_state_ids: HashMap<String, String>,
    default_completed_state_id: Option<String>,
    timeout: Duration,
    max_response_bytes: usize,
    max_retries: usize,
    min_org_interval: Duration,
}

impl LinearDeliveryConfig {
    pub fn from_env() -> anyhow::Result<Self> {
        let enabled = parse_bool_env("LINEAR_DELIVERY_ENABLED", false)?;
        let dry_run = parse_bool_env("LINEAR_DELIVERY_DRY_RUN", true)?;
        let api_url = validate_api_url(
            &env::var("LINEAR_API_URL").unwrap_or_else(|_| DEFAULT_LINEAR_API_URL.to_owned()),
        )?;
        let auth_scheme = match env::var("LINEAR_API_AUTH_SCHEME")
            .unwrap_or_else(|_| "api_key".to_owned())
            .trim()
            .to_ascii_lowercase()
            .as_str()
        {
            "api_key" | "apikey" | "key" => LinearAuthScheme::ApiKey,
            "bearer" | "oauth" => LinearAuthScheme::Bearer,
            _ => bail!("LINEAR_API_AUTH_SCHEME must be api_key or bearer"),
        };
        let api_token = env::var(LINEAR_API_TOKEN_ENV)
            .ok()
            .filter(|value| !value.trim().is_empty());
        let team_key = env::var("LINEAR_TEAM_KEY").unwrap_or_else(|_| "DEN".to_owned());
        let project_names = parse_mapping(
            &env::var("LINEAR_PROJECT_NAMES").unwrap_or_default(),
            "LINEAR_PROJECT_NAMES",
        )?;
        let completed_state_ids = parse_mapping(
            &env::var("LINEAR_COMPLETED_STATE_IDS").unwrap_or_default(),
            "LINEAR_COMPLETED_STATE_IDS",
        )?;
        let default_completed_state_id = env::var("LINEAR_COMPLETED_STATE_ID")
            .ok()
            .filter(|value| !value.trim().is_empty());
        let timeout = Duration::from_millis(parse_u64_env(
            "LINEAR_REQUEST_TIMEOUT_MS",
            10_000,
            100,
            60_000,
        )?);
        let max_response_bytes = parse_u64_env(
            "LINEAR_MAX_RESPONSE_BYTES",
            DEFAULT_MAX_RESPONSE_BYTES as u64,
            1024,
            4 * 1024 * 1024,
        )? as usize;
        let max_retries = parse_u64_env("LINEAR_MAX_RETRIES", 3, 0, 10)? as usize;
        let min_org_interval =
            Duration::from_millis(parse_u64_env("LINEAR_MIN_ORG_INTERVAL_MS", 100, 0, 60_000)?);

        if team_key.trim().is_empty() {
            bail!("LINEAR_TEAM_KEY must not be empty");
        }
        if enabled && project_names.is_empty() {
            bail!("LINEAR_DELIVERY_ENABLED=true requires LINEAR_PROJECT_NAMES");
        }
        if enabled && !dry_run && api_token.is_none() {
            bail!("live Linear delivery requires {LINEAR_API_TOKEN_ENV}");
        }

        Ok(Self {
            enabled,
            dry_run,
            api_url,
            api_token,
            auth_scheme,
            team_key,
            project_names,
            completed_state_ids,
            default_completed_state_id,
            timeout,
            max_response_bytes,
            max_retries,
            min_org_interval,
        })
    }

    #[cfg(test)]
    fn test(api_url: Url, dry_run: bool) -> Self {
        Self {
            enabled: true,
            dry_run,
            api_url,
            api_token: (!dry_run).then(|| "linear-test-token".to_owned()),
            auth_scheme: LinearAuthScheme::ApiKey,
            team_key: "DEN".to_owned(),
            project_names: HashMap::from([
                (
                    "sonus-auris".to_owned(),
                    "github.com/sonus-auris".to_owned(),
                ),
                (
                    "daedalus-fab".to_owned(),
                    "github.com/daedalus-fab".to_owned(),
                ),
            ]),
            completed_state_ids: HashMap::from([
                ("sonus-auris".to_owned(), "completed-sonus".to_owned()),
                ("daedalus-fab".to_owned(), "completed-daedalus".to_owned()),
            ]),
            default_completed_state_id: None,
            timeout: Duration::from_secs(2),
            max_response_bytes: 32 * 1024,
            max_retries: 2,
            min_org_interval: Duration::ZERO,
        }
    }

    fn expected_project(&self, organization: &str) -> Result<&str, LinearDeliveryError> {
        self.project_names
            .get(organization)
            .map(String::as_str)
            .ok_or_else(|| {
                LinearDeliveryError::policy(format!(
                    "organization {organization} has no configured Linear project"
                ))
            })
    }

    fn completed_state_id(&self, organization: &str) -> Option<&str> {
        self.completed_state_ids
            .get(organization)
            .map(String::as_str)
            .or(self.default_completed_state_id.as_deref())
    }
}

#[derive(Clone)]
pub struct LinearDeliveryWorker {
    config: Arc<LinearDeliveryConfig>,
    client: Client,
    ledger: LinearMutationLedger,
    org_requests: Arc<Mutex<HashMap<String, Instant>>>,
}

impl LinearDeliveryWorker {
    pub fn from_env(database: Database) -> anyhow::Result<Self> {
        Self::new(LinearDeliveryConfig::from_env()?, database)
    }

    pub fn new(config: LinearDeliveryConfig, database: Database) -> anyhow::Result<Self> {
        Self::with_ledger(config, LinearMutationLedger::Postgres(database))
    }

    #[cfg(test)]
    fn new_for_test(config: LinearDeliveryConfig) -> anyhow::Result<Self> {
        Self::with_ledger(
            config,
            LinearMutationLedger::Memory(Arc::new(Mutex::new(HashMap::new()))),
        )
    }

    fn with_ledger(
        config: LinearDeliveryConfig,
        ledger: LinearMutationLedger,
    ) -> anyhow::Result<Self> {
        let client = Client::builder()
            .redirect(Policy::none())
            .timeout(config.timeout)
            .user_agent("ai-agent-coordinator-linear-delivery/1")
            .build()
            .context("failed to build Linear HTTP client")?;
        Ok(Self {
            config: Arc::new(config),
            client,
            ledger,
            org_requests: Arc::new(Mutex::new(HashMap::new())),
        })
    }

    pub fn enabled(&self) -> bool {
        self.config.enabled
    }

    pub fn dry_run(&self) -> bool {
        self.config.dry_run
    }

    pub fn plan_job(&self, job: &Job) -> Result<LinearDeliveryReport, LinearDeliveryError> {
        if !self.config.enabled {
            return Err(LinearDeliveryError::policy("Linear delivery is disabled"));
        }
        if job.task_type != "github_push" {
            return Err(LinearDeliveryError::policy(format!(
                "job {} has unsupported task type {}",
                job.id, job.task_type
            )));
        }
        let envelope = PushEnvelope::from_job(job)?;
        self.config.expected_project(&job.org)?;
        Ok(LinearDeliveryReport {
            job_id: job.id.clone(),
            organization: job.org.clone(),
            repository: format!("{}/{}", job.org, job.repo),
            default_branch: envelope.default_branch,
            dry_run: true,
            directives: envelope
                .directives
                .into_iter()
                .map(|directive| LinearDirectiveReport {
                    issue_identifier: directive.issue_identifier,
                    commit_id: directive.commit_id,
                    action: if directive.closes_issue {
                        "reference_and_transition".to_owned()
                    } else {
                        "reference".to_owned()
                    },
                    status: "planned".to_owned(),
                    dry_run: true,
                })
                .collect(),
        })
    }

    pub async fn deliver_job(
        &self,
        job: &Job,
    ) -> Result<LinearDeliveryReport, LinearDeliveryError> {
        if self.config.dry_run {
            return self.plan_job(job);
        }
        if !self.config.enabled {
            return Err(LinearDeliveryError::policy("Linear delivery is disabled"));
        }
        if job.task_type != "github_push" {
            return Err(LinearDeliveryError::policy(format!(
                "job {} has unsupported task type {}",
                job.id, job.task_type
            )));
        }

        let envelope = PushEnvelope::from_job(job)?;
        let expected_project = self.config.expected_project(&job.org)?.to_owned();
        let mut reports = Vec::with_capacity(envelope.directives.len());

        for directive in &envelope.directives {
            let key = mutation_key(job, directive);
            if self
                .ledger
                .is_succeeded(&key)
                .await
                .map_err(LinearDeliveryError::internal)?
            {
                reports.push(LinearDirectiveReport {
                    issue_identifier: directive.issue_identifier.clone(),
                    commit_id: directive.commit_id.clone(),
                    action: if directive.closes_issue {
                        "reference_and_transition".to_owned()
                    } else {
                        "reference".to_owned()
                    },
                    status: "already_delivered".to_owned(),
                    dry_run: false,
                });
                continue;
            }

            self.ledger
                .begin(&key, &job.id, &job.org, &job.repo, directive)
                .await
                .map_err(LinearDeliveryError::internal)?;
            let outcome = self
                .deliver_directive(job, &envelope, directive, &expected_project, &key)
                .await;
            match outcome {
                Ok(report) => {
                    self.ledger
                        .succeed(&key)
                        .await
                        .map_err(LinearDeliveryError::internal)?;
                    reports.push(report);
                }
                Err(error) => {
                    let _ = self.ledger.fail(&key, &error.public_message).await;
                    return Err(error);
                }
            }
        }

        Ok(LinearDeliveryReport {
            job_id: job.id.clone(),
            organization: job.org.clone(),
            repository: format!("{}/{}", job.org, job.repo),
            default_branch: envelope.default_branch,
            dry_run: false,
            directives: reports,
        })
    }

    async fn deliver_directive(
        &self,
        job: &Job,
        envelope: &PushEnvelope,
        directive: &LinearDirectiveInput,
        expected_project: &str,
        mutation_key: &str,
    ) -> Result<LinearDirectiveReport, LinearDeliveryError> {
        let commit_url = format!(
            "https://github.com/{}/{}/commit/{}",
            job.org, job.repo, directive.commit_id
        );
        let issue = self
            .fetch_issue(&job.org, &directive.issue_identifier)
            .await?;
        validate_issue(
            &issue,
            &directive.issue_identifier,
            &self.config.team_key,
            expected_project,
        )?;

        let marker = format!("<!-- ai-agent-coordinator:{mutation_key} -->");
        let short_commit = directive.commit_id.chars().take(12).collect::<String>();
        self.create_attachment(
            &job.org,
            &issue.id,
            &format!("Commit {short_commit} in {}/{}", job.org, job.repo),
            &format!(
                "{} {} on {}",
                directive.keyword, directive.issue_identifier, envelope.default_branch
            ),
            &commit_url,
            job,
            envelope,
            directive,
            mutation_key,
        )
        .await?;

        if !issue.comments.iter().any(|body| body.contains(&marker)) {
            let body = format!(
                "{marker}\nLinked commit [`{short_commit}`]({commit_url}) from `{}/{}` on `{}`. Parsed directive: **{} {}**.",
                job.org,
                job.repo,
                envelope.default_branch,
                directive.keyword,
                directive.issue_identifier,
            );
            self.create_comment(&job.org, &issue.id, &body).await?;
        }

        if directive.closes_issue && issue.state_type != "completed" {
            let completed_state_id = self.config.completed_state_id(&job.org).ok_or_else(|| {
                LinearDeliveryError::policy(format!(
                    "closing directives for {} require a configured completed state ID",
                    job.org
                ))
            })?;
            self.update_issue_state(&job.org, &issue.id, completed_state_id)
                .await?;
        }

        Ok(LinearDirectiveReport {
            issue_identifier: directive.issue_identifier.clone(),
            commit_id: directive.commit_id.clone(),
            action: if directive.closes_issue {
                "reference_and_transition".to_owned()
            } else {
                "reference".to_owned()
            },
            status: "delivered".to_owned(),
            dry_run: false,
        })
    }

    async fn fetch_issue(
        &self,
        organization: &str,
        identifier: &str,
    ) -> Result<LinearIssueSnapshot, LinearDeliveryError> {
        let data = self
            .graphql(
                organization,
                ISSUE_QUERY,
                json!({"id": identifier}),
                "issue query",
                true,
            )
            .await?;
        let issue = data.get("issue").ok_or_else(|| {
            LinearDeliveryError::policy(format!("Linear issue {identifier} was not found"))
        })?;
        if issue.is_null() {
            return Err(LinearDeliveryError::policy(format!(
                "Linear issue {identifier} was not found"
            )));
        }
        LinearIssueSnapshot::from_value(issue)
    }

    #[allow(clippy::too_many_arguments)]
    async fn create_attachment(
        &self,
        organization: &str,
        issue_id: &str,
        title: &str,
        subtitle: &str,
        url: &str,
        job: &Job,
        envelope: &PushEnvelope,
        directive: &LinearDirectiveInput,
        mutation_key: &str,
    ) -> Result<(), LinearDeliveryError> {
        let data = self
            .graphql(
                organization,
                ATTACHMENT_MUTATION,
                json!({
                    "input": {
                        "issueId": issue_id,
                        "title": title,
                        "subtitle": subtitle,
                        "url": url,
                        "metadata": {
                            "source": "ai-agent-coordinator",
                            "organization": job.org,
                            "repository": format!("{}/{}", job.org, job.repo),
                            "defaultBranch": envelope.default_branch,
                            "commit": directive.commit_id,
                            "keyword": directive.keyword,
                            "closesIssue": directive.closes_issue,
                            "mutationKey": mutation_key,
                        }
                    }
                }),
                "attachment mutation",
                true,
            )
            .await?;
        require_success(&data, "attachmentCreate")
    }

    async fn create_comment(
        &self,
        organization: &str,
        issue_id: &str,
        body: &str,
    ) -> Result<(), LinearDeliveryError> {
        let data = self
            .graphql(
                organization,
                COMMENT_MUTATION,
                json!({"input": {"issueId": issue_id, "body": body}}),
                "comment mutation",
                false,
            )
            .await?;
        require_success(&data, "commentCreate")
    }

    async fn update_issue_state(
        &self,
        organization: &str,
        issue_id: &str,
        state_id: &str,
    ) -> Result<(), LinearDeliveryError> {
        let data = self
            .graphql(
                organization,
                ISSUE_UPDATE_MUTATION,
                json!({"id": issue_id, "input": {"stateId": state_id}}),
                "issue state mutation",
                true,
            )
            .await?;
        require_success(&data, "issueUpdate")
    }

    async fn graphql(
        &self,
        organization: &str,
        query: &str,
        variables: Value,
        operation: &str,
        retry_ambiguous: bool,
    ) -> Result<Value, LinearDeliveryError> {
        let token = self.config.api_token.as_deref().ok_or_else(|| {
            LinearDeliveryError::policy(format!("{LINEAR_API_TOKEN_ENV} is not configured"))
        })?;
        let mut last_error = None;

        for attempt in 0..=self.config.max_retries {
            self.wait_for_org_slot(organization).await;
            let authorization = match self.config.auth_scheme {
                LinearAuthScheme::ApiKey => token.to_owned(),
                LinearAuthScheme::Bearer => format!("Bearer {token}"),
            };
            let request = self
                .client
                .post(self.config.api_url.clone())
                .header("authorization", authorization)
                .header("content-type", "application/json")
                .json(&json!({"query": query, "variables": variables.clone()}));

            match request.send().await {
                Ok(mut response) => {
                    let status = response.status();
                    let retry_after = response
                        .headers()
                        .get(RETRY_AFTER)
                        .and_then(|value| value.to_str().ok())
                        .and_then(|value| value.parse::<u64>().ok())
                        .map(Duration::from_secs);
                    let body = read_bounded(&mut response, self.config.max_response_bytes).await?;

                    if status == StatusCode::TOO_MANY_REQUESTS
                        || (retry_ambiguous && status.is_server_error())
                    {
                        let error = LinearDeliveryError::retryable(
                            format!("Linear {operation} returned HTTP {status}"),
                            retry_after.unwrap_or_else(|| retry_delay(attempt)),
                        );
                        if attempt < self.config.max_retries {
                            sleep(error.retry_after).await;
                            last_error = Some(error);
                            continue;
                        }
                        return Err(error);
                    }
                    if status.is_server_error() {
                        return Err(LinearDeliveryError::retryable(
                            format!("Linear {operation} returned HTTP {status}"),
                            retry_after.unwrap_or_else(|| retry_delay(attempt)),
                        ));
                    }
                    if !status.is_success() {
                        return Err(LinearDeliveryError::policy(format!(
                            "Linear {operation} returned HTTP {status}"
                        )));
                    }

                    let response: Value = serde_json::from_slice(&body).map_err(|_| {
                        LinearDeliveryError::retryable(
                            format!("Linear {operation} returned malformed JSON"),
                            retry_delay(attempt),
                        )
                    })?;
                    if let Some(errors) = response.get("errors").and_then(Value::as_array) {
                        let rate_limited = errors.iter().any(|error| {
                            error
                                .pointer("/extensions/code")
                                .and_then(Value::as_str)
                                .is_some_and(|code| {
                                    code.eq_ignore_ascii_case("RATELIMITED")
                                        || code.eq_ignore_ascii_case("RATE_LIMITED")
                                })
                        });
                        if rate_limited && attempt < self.config.max_retries {
                            let delay = retry_delay(attempt);
                            sleep(delay).await;
                            last_error = Some(LinearDeliveryError::retryable(
                                format!("Linear {operation} was rate limited"),
                                delay,
                            ));
                            continue;
                        }
                        return Err(LinearDeliveryError::policy(format!(
                            "Linear {operation} returned GraphQL errors"
                        )));
                    }
                    return response.get("data").cloned().ok_or_else(|| {
                        LinearDeliveryError::retryable(
                            format!("Linear {operation} returned no data"),
                            retry_delay(attempt),
                        )
                    });
                }
                Err(error) => {
                    let retryable = error.is_timeout() || error.is_connect() || error.is_request();
                    let delivery_error = if retryable {
                        LinearDeliveryError::retryable(
                            format!("Linear {operation} request failed"),
                            retry_delay(attempt),
                        )
                    } else {
                        LinearDeliveryError::policy(format!(
                            "Linear {operation} request could not be completed"
                        ))
                    };
                    if retryable && retry_ambiguous && attempt < self.config.max_retries {
                        sleep(delivery_error.retry_after).await;
                        last_error = Some(delivery_error);
                        continue;
                    }
                    return Err(delivery_error);
                }
            }
        }

        Err(last_error.unwrap_or_else(|| {
            LinearDeliveryError::retryable(
                format!("Linear {operation} exhausted retries"),
                Duration::from_secs(30),
            )
        }))
    }

    async fn wait_for_org_slot(&self, organization: &str) {
        if self.config.min_org_interval.is_zero() {
            return;
        }

        loop {
            let delay = {
                let mut requests = self.org_requests.lock().await;
                match requests.get(organization) {
                    Some(last) if last.elapsed() < self.config.min_org_interval => {
                        self.config.min_org_interval - last.elapsed()
                    }
                    _ => {
                        requests.insert(organization.to_owned(), Instant::now());
                        return;
                    }
                }
            };
            sleep(delay).await;
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct LinearDeliveryRequest {
    pub worker_id: String,
    #[serde(default)]
    pub orgs: Vec<String>,
    #[serde(default)]
    pub repositories: Vec<String>,
    #[serde(default = "default_lease_seconds")]
    pub lease_seconds: i64,
}

impl LinearDeliveryRequest {
    pub fn into_claim(self) -> ClaimJobRequest {
        ClaimJobRequest {
            worker_id: self.worker_id,
            orgs: self.orgs,
            repositories: self.repositories,
            task_types: vec!["github_push".to_owned()],
            lease_seconds: self.lease_seconds,
        }
    }
}

fn default_lease_seconds() -> i64 {
    120
}

#[derive(Debug, Clone, Serialize)]
pub struct LinearDeliveryReport {
    pub job_id: String,
    pub organization: String,
    pub repository: String,
    pub default_branch: String,
    pub dry_run: bool,
    pub directives: Vec<LinearDirectiveReport>,
}

#[derive(Debug, Clone, Serialize)]
pub struct LinearDirectiveReport {
    pub issue_identifier: String,
    pub commit_id: String,
    pub action: String,
    pub status: String,
    pub dry_run: bool,
}

#[derive(Debug, Clone)]
pub struct LinearDeliveryError {
    pub public_message: String,
    pub retryable: bool,
    pub retry_after: Duration,
}

impl LinearDeliveryError {
    fn policy(message: impl Into<String>) -> Self {
        Self {
            public_message: message.into(),
            retryable: false,
            retry_after: Duration::ZERO,
        }
    }

    fn retryable(message: impl Into<String>, retry_after: Duration) -> Self {
        Self {
            public_message: message.into(),
            retryable: true,
            retry_after,
        }
    }

    fn internal(error: anyhow::Error) -> Self {
        Self::retryable(
            format!("Linear delivery persistence failed: {error}"),
            Duration::from_secs(30),
        )
    }
}

#[derive(Debug, Clone)]
struct PushEnvelope {
    default_branch: String,
    directives: Vec<LinearDirectiveInput>,
}

impl PushEnvelope {
    fn from_job(job: &Job) -> Result<Self, LinearDeliveryError> {
        let repository = job
            .payload
            .pointer("/repository/full_name")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                LinearDeliveryError::policy("push job is missing repository.full_name")
            })?;
        if repository != format!("{}/{}", job.org, job.repo) {
            return Err(LinearDeliveryError::policy(
                "push job repository does not match durable job scope",
            ));
        }
        let default_branch = job
            .payload
            .pointer("/coordinator/default_branch")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| {
                LinearDeliveryError::policy("push job is missing default-branch policy")
            })?
            .to_owned();
        let pushed_ref = job
            .payload
            .get("ref")
            .and_then(Value::as_str)
            .ok_or_else(|| LinearDeliveryError::policy("push job is missing ref"))?;
        if pushed_ref != format!("refs/heads/{default_branch}") {
            return Err(LinearDeliveryError::policy(
                "push job no longer satisfies default-branch policy",
            ));
        }
        let directives = job
            .payload
            .pointer("/coordinator/linear_directives")
            .and_then(Value::as_array)
            .ok_or_else(|| LinearDeliveryError::policy("push job has no Linear directives"))?
            .iter()
            .map(LinearDirectiveInput::from_value)
            .collect::<Result<Vec<_>, _>>()?;
        if directives.is_empty() {
            return Err(LinearDeliveryError::policy(
                "push job has no deliverable Linear directives",
            ));
        }
        Ok(Self {
            default_branch,
            directives,
        })
    }
}

#[derive(Debug, Clone)]
struct LinearDirectiveInput {
    commit_id: String,
    issue_identifier: String,
    keyword: String,
    closes_issue: bool,
}

impl LinearDirectiveInput {
    fn from_value(value: &Value) -> Result<Self, LinearDeliveryError> {
        let commit_id = value
            .get("commit_id")
            .and_then(Value::as_str)
            .filter(|value| is_commit_identifier(value))
            .ok_or_else(|| LinearDeliveryError::policy("directive has invalid commit_id"))?
            .to_ascii_lowercase();
        let issue_identifier = value
            .get("issue_identifier")
            .and_then(Value::as_str)
            .filter(|value| is_issue_identifier(value))
            .ok_or_else(|| LinearDeliveryError::policy("directive has invalid issue identifier"))?
            .to_ascii_uppercase();
        let keyword = value
            .get("keyword")
            .and_then(Value::as_str)
            .filter(|value| is_keyword(value))
            .ok_or_else(|| LinearDeliveryError::policy("directive has unsupported keyword"))?
            .to_ascii_lowercase();
        let closes_issue = value
            .get("closes_issue")
            .and_then(Value::as_bool)
            .ok_or_else(|| LinearDeliveryError::policy("directive is missing closes_issue"))?;
        Ok(Self {
            commit_id,
            issue_identifier,
            keyword,
            closes_issue,
        })
    }
}

#[derive(Debug)]
struct LinearIssueSnapshot {
    id: String,
    identifier: String,
    team_key: String,
    project_name: Option<String>,
    state_type: String,
    comments: Vec<String>,
}

impl LinearIssueSnapshot {
    fn from_value(value: &Value) -> Result<Self, LinearDeliveryError> {
        Ok(Self {
            id: required_string(value, "/id", "issue.id")?,
            identifier: required_string(value, "/identifier", "issue.identifier")?,
            team_key: required_string(value, "/team/key", "issue.team.key")?,
            project_name: value
                .pointer("/project/name")
                .and_then(Value::as_str)
                .map(str::to_owned),
            state_type: required_string(value, "/state/type", "issue.state.type")?,
            comments: value
                .pointer("/comments/nodes")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|comment| comment.get("body").and_then(Value::as_str))
                .map(str::to_owned)
                .collect(),
        })
    }
}

fn validate_issue(
    issue: &LinearIssueSnapshot,
    expected_identifier: &str,
    expected_team_key: &str,
    expected_project: &str,
) -> Result<(), LinearDeliveryError> {
    if issue.identifier != expected_identifier {
        return Err(LinearDeliveryError::policy(format!(
            "resolved issue {} does not match directive {expected_identifier}",
            issue.identifier
        )));
    }
    if issue.team_key != expected_team_key {
        return Err(LinearDeliveryError::policy(format!(
            "issue {expected_identifier} belongs to unexpected team {}",
            issue.team_key
        )));
    }
    if issue.project_name.as_deref() != Some(expected_project) {
        return Err(LinearDeliveryError::policy(format!(
            "issue {expected_identifier} does not belong to project {expected_project}"
        )));
    }
    Ok(())
}

fn require_success(data: &Value, field: &str) -> Result<(), LinearDeliveryError> {
    if data
        .pointer(&format!("/{field}/success"))
        .and_then(Value::as_bool)
        == Some(true)
    {
        Ok(())
    } else {
        Err(LinearDeliveryError::retryable(
            format!("Linear {field} did not report success"),
            Duration::from_secs(30),
        ))
    }
}

#[derive(Clone)]
enum LinearMutationLedger {
    Postgres(Database),
    #[cfg(test)]
    Memory(Arc<Mutex<HashMap<String, TestMutationState>>>),
}

impl LinearMutationLedger {
    async fn is_succeeded(&self, key: &str) -> anyhow::Result<bool> {
        match self {
            Self::Postgres(database) => database.linear_mutation_succeeded(key).await,
            #[cfg(test)]
            Self::Memory(states) => Ok(states
                .lock()
                .await
                .get(key)
                .is_some_and(|state| state.status == "succeeded")),
        }
    }

    async fn begin(
        &self,
        key: &str,
        job_id: &str,
        organization: &str,
        repository: &str,
        directive: &LinearDirectiveInput,
    ) -> anyhow::Result<()> {
        let action = if directive.closes_issue {
            "reference_and_transition"
        } else {
            "reference"
        };
        match self {
            Self::Postgres(database) => {
                database
                    .begin_linear_mutation(
                        key,
                        job_id,
                        organization,
                        repository,
                        &directive.issue_identifier,
                        &directive.commit_id,
                        &directive.keyword,
                        action,
                    )
                    .await
            }
            #[cfg(test)]
            Self::Memory(states) => {
                let mut states = states.lock().await;
                let state = states.entry(key.to_owned()).or_default();
                state.status = "pending".to_owned();
                state.attempts += 1;
                Ok(())
            }
        }
    }

    async fn succeed(&self, key: &str) -> anyhow::Result<()> {
        match self {
            Self::Postgres(database) => database.succeed_linear_mutation(key).await,
            #[cfg(test)]
            Self::Memory(states) => {
                let mut states = states.lock().await;
                states.entry(key.to_owned()).or_default().status = "succeeded".to_owned();
                Ok(())
            }
        }
    }

    async fn fail(&self, key: &str, error: &str) -> anyhow::Result<()> {
        match self {
            Self::Postgres(database) => database.fail_linear_mutation(key, error).await,
            #[cfg(test)]
            Self::Memory(states) => {
                let mut states = states.lock().await;
                let state = states.entry(key.to_owned()).or_default();
                state.status = "failed".to_owned();
                Ok(())
            }
        }
    }
}

#[cfg(test)]
#[derive(Default)]
struct TestMutationState {
    status: String,
    attempts: i64,
}

async fn read_bounded(
    response: &mut reqwest::Response,
    max_bytes: usize,
) -> Result<Vec<u8>, LinearDeliveryError> {
    if response
        .content_length()
        .is_some_and(|length| length > max_bytes as u64)
    {
        return Err(LinearDeliveryError::retryable(
            "Linear response exceeded configured size limit",
            Duration::from_secs(30),
        ));
    }
    let mut body = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| {
        LinearDeliveryError::retryable(
            "Linear response body could not be read",
            Duration::from_secs(30),
        )
    })? {
        if body.len().saturating_add(chunk.len()) > max_bytes {
            return Err(LinearDeliveryError::retryable(
                "Linear response exceeded configured size limit",
                Duration::from_secs(30),
            ));
        }
        body.extend_from_slice(&chunk);
    }
    Ok(body)
}

fn mutation_key(job: &Job, directive: &LinearDirectiveInput) -> String {
    let action = if directive.closes_issue {
        "close"
    } else {
        "reference"
    };
    format!(
        "linear:{}:{}:{}:{}:{}:{}",
        job.org,
        job.repo,
        directive.commit_id,
        directive.issue_identifier,
        directive.keyword,
        action
    )
}

fn retry_delay(attempt: usize) -> Duration {
    let exponent = attempt.min(6) as u32;
    let base = 100_u64.saturating_mul(2_u64.saturating_pow(exponent));
    Duration::from_millis(base.saturating_add((attempt as u64 * 73) % 211))
}

fn required_string(
    value: &Value,
    pointer: &str,
    field: &str,
) -> Result<String, LinearDeliveryError> {
    value
        .pointer(pointer)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| {
            LinearDeliveryError::retryable(
                format!("Linear response is missing {field}"),
                Duration::from_secs(30),
            )
        })
}

fn is_commit_identifier(value: &str) -> bool {
    matches!(value.len(), 40 | 64)
        && value.bytes().all(|byte| byte.is_ascii_hexdigit())
        && value.bytes().any(|byte| byte != b'0')
}

fn is_issue_identifier(value: &str) -> bool {
    let Some((team, number)) = value.split_once('-') else {
        return false;
    };
    !team.is_empty()
        && team
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit())
        && !number.is_empty()
        && number.bytes().all(|byte| byte.is_ascii_digit())
}

fn is_keyword(value: &str) -> bool {
    matches!(
        value.to_ascii_lowercase().as_str(),
        "fixes"
            | "closes"
            | "resolves"
            | "completes"
            | "implements"
            | "refs"
            | "references"
            | "part of"
            | "related to"
            | "contributes to"
    )
}

fn parse_mapping(value: &str, variable: &str) -> anyhow::Result<HashMap<String, String>> {
    let mut result = HashMap::new();
    for entry in value
        .split(',')
        .map(str::trim)
        .filter(|entry| !entry.is_empty())
    {
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

fn parse_u64_env(variable: &str, default: u64, min: u64, max: u64) -> anyhow::Result<u64> {
    let value = match env::var(variable) {
        Ok(value) => value
            .parse::<u64>()
            .with_context(|| format!("{variable} must be an integer"))?,
        Err(_) => default,
    };
    if !(min..=max).contains(&value) {
        bail!("{variable} must be between {min} and {max}");
    }
    Ok(value)
}

fn validate_api_url(value: &str) -> anyhow::Result<Url> {
    let url = Url::parse(value).context("LINEAR_API_URL must be an absolute URL")?;
    if url.username() != "" || url.password().is_some() {
        bail!("LINEAR_API_URL must not contain credentials");
    }
    if url.query().is_some() || url.fragment().is_some() {
        bail!("LINEAR_API_URL must not contain a query or fragment");
    }
    match url.scheme() {
        "https" => Ok(url),
        "http" if is_loopback_host(url.host_str()) => Ok(url),
        _ => bail!("LINEAR_API_URL must use HTTPS, except loopback HTTP in tests"),
    }
}

fn is_loopback_host(host: Option<&str>) -> bool {
    match host {
        Some("localhost") => true,
        Some(host) => IpAddr::from_str(host).is_ok_and(|address| address.is_loopback()),
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use std::{
        collections::VecDeque,
        sync::{Arc, Mutex as StdMutex},
    };

    use axum::{extract::State, http::StatusCode, routing::post, Json, Router};
    use tokio::net::TcpListener;

    use super::*;
    use crate::jobs::JobStatus;

    #[derive(Clone)]
    struct MockState {
        responses: Arc<StdMutex<VecDeque<(StatusCode, Value)>>>,
        requests: Arc<StdMutex<Vec<Value>>>,
    }

    async fn mock_graphql(
        State(state): State<MockState>,
        Json(request): Json<Value>,
    ) -> (StatusCode, Json<Value>) {
        state.requests.lock().unwrap().push(request);
        let (status, response) = state
            .responses
            .lock()
            .unwrap()
            .pop_front()
            .expect("mock response");
        (status, Json(response))
    }

    async fn mock_server(responses: Vec<(StatusCode, Value)>) -> (Url, Arc<StdMutex<Vec<Value>>>) {
        let state = MockState {
            responses: Arc::new(StdMutex::new(VecDeque::from(responses))),
            requests: Arc::new(StdMutex::new(Vec::new())),
        };
        let requests = state.requests.clone();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move {
            axum::serve(
                listener,
                Router::new()
                    .route("/graphql", post(mock_graphql))
                    .with_state(state),
            )
            .await
            .unwrap();
        });
        (
            Url::parse(&format!("http://{address}/graphql")).unwrap(),
            requests,
        )
    }

    fn job(closes_issue: bool) -> Job {
        Job {
            id: "job-1".to_owned(),
            org: "sonus-auris".to_owned(),
            repo: "sonus-auris-site.web".to_owned(),
            task_type: "github_push".to_owned(),
            payload: json!({
                "ref": "refs/heads/main",
                "repository": {"full_name": "sonus-auris/sonus-auris-site.web"},
                "coordinator": {
                    "default_branch": "main",
                    "linear_directives": [{
                        "commit_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "issue_identifier": "DEN-455",
                        "keyword": if closes_issue { "fixes" } else { "refs" },
                        "closes_issue": closes_issue
                    }]
                }
            }),
            priority: 10,
            status: JobStatus::Queued,
            created_at: Utc::now(),
            updated_at: Utc::now(),
            available_at: Utc::now(),
            claimed_by: None,
            lease_expires_at: None,
            attempts: 0,
            max_attempts: 3,
            result: None,
            last_error: None,
            budget_usd: None,
        }
    }

    fn issue_response(project: &str, state_type: &str, comments: Vec<&str>) -> Value {
        json!({
            "data": {
                "issue": {
                    "id": "issue-uuid",
                    "identifier": "DEN-455",
                    "url": "https://linear.app/denman/issue/DEN-455/test",
                    "team": {"id": "team", "key": "DEN", "name": "Denman"},
                    "project": {"id": "project", "name": project},
                    "state": {"id": "state", "name": "Started", "type": state_type},
                    "comments": {"nodes": comments.into_iter().map(|body| json!({"id": "comment", "body": body})).collect::<Vec<_>>()}
                }
            }
        })
    }

    #[tokio::test]
    async fn dry_run_is_safe_and_makes_no_requests_or_ledger_entries() {
        let (url, requests) = mock_server(vec![]).await;
        let worker =
            LinearDeliveryWorker::new_for_test(LinearDeliveryConfig::test(url, true)).unwrap();
        let report = worker.plan_job(&job(false)).unwrap();
        assert!(report.dry_run);
        assert_eq!(report.directives[0].status, "planned");
        assert!(requests.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn creates_reference_comment_and_is_idempotent() {
        let (url, requests) = mock_server(vec![
            (
                StatusCode::OK,
                issue_response("github.com/sonus-auris", "started", vec![]),
            ),
            (
                StatusCode::OK,
                json!({"data": {"attachmentCreate": {"success": true, "attachment": {"id": "attachment", "url": "https://github.com/example"}}}}),
            ),
            (
                StatusCode::OK,
                json!({"data": {"commentCreate": {"success": true, "comment": {"id": "comment"}}}}),
            ),
        ])
        .await;
        let worker =
            LinearDeliveryWorker::new_for_test(LinearDeliveryConfig::test(url, false)).unwrap();
        let first = worker.deliver_job(&job(false)).await.unwrap();
        let second = worker.deliver_job(&job(false)).await.unwrap();
        assert_eq!(first.directives[0].status, "delivered");
        assert_eq!(second.directives[0].status, "already_delivered");
        assert_eq!(requests.lock().unwrap().len(), 3);
    }

    #[tokio::test]
    async fn closing_directive_updates_configured_state() {
        let (url, requests) = mock_server(vec![
            (
                StatusCode::OK,
                issue_response("github.com/sonus-auris", "started", vec![]),
            ),
            (
                StatusCode::OK,
                json!({"data": {"attachmentCreate": {"success": true, "attachment": {"id": "attachment", "url": "https://github.com/example"}}}}),
            ),
            (
                StatusCode::OK,
                json!({"data": {"commentCreate": {"success": true, "comment": {"id": "comment"}}}}),
            ),
            (
                StatusCode::OK,
                json!({"data": {"issueUpdate": {"success": true, "issue": {"id": "issue-uuid", "identifier": "DEN-455", "state": {"id": "completed-sonus", "name": "Done", "type": "completed"}}}}}),
            ),
        ])
        .await;
        let worker =
            LinearDeliveryWorker::new_for_test(LinearDeliveryConfig::test(url, false)).unwrap();
        let report = worker.deliver_job(&job(true)).await.unwrap();
        assert_eq!(report.directives[0].action, "reference_and_transition");
        let requests = requests.lock().unwrap();
        assert_eq!(requests.len(), 4);
        assert!(requests[3]["query"]
            .as_str()
            .unwrap()
            .contains("issueUpdate"));
        assert_eq!(
            requests[3]["variables"]["input"]["stateId"],
            "completed-sonus"
        );
    }

    #[tokio::test]
    async fn rejects_wrong_project_before_mutation() {
        let (url, requests) = mock_server(vec![(
            StatusCode::OK,
            issue_response("github.com/fiducia-cloud", "started", vec![]),
        )])
        .await;
        let worker =
            LinearDeliveryWorker::new_for_test(LinearDeliveryConfig::test(url, false)).unwrap();
        let error = worker.deliver_job(&job(false)).await.unwrap_err();
        assert!(!error.retryable);
        assert!(error.public_message.contains("does not belong to project"));
        assert_eq!(requests.lock().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn retries_rate_limits_before_delivery() {
        let (url, requests) = mock_server(vec![
            (
                StatusCode::TOO_MANY_REQUESTS,
                json!({"errors": [{"message": "rate"}]}),
            ),
            (
                StatusCode::OK,
                issue_response("github.com/sonus-auris", "started", vec![]),
            ),
            (
                StatusCode::OK,
                json!({"data": {"attachmentCreate": {"success": true, "attachment": {"id": "attachment", "url": "https://github.com/example"}}}}),
            ),
            (
                StatusCode::OK,
                json!({"data": {"commentCreate": {"success": true, "comment": {"id": "comment"}}}}),
            ),
        ])
        .await;
        let worker =
            LinearDeliveryWorker::new_for_test(LinearDeliveryConfig::test(url, false)).unwrap();
        worker.deliver_job(&job(false)).await.unwrap();
        assert_eq!(requests.lock().unwrap().len(), 4);
    }

    #[tokio::test]
    async fn ambiguous_comment_failure_is_deferred_to_the_job_queue() {
        let (url, requests) = mock_server(vec![
            (
                StatusCode::OK,
                issue_response("github.com/sonus-auris", "started", vec![]),
            ),
            (
                StatusCode::OK,
                json!({"data": {"attachmentCreate": {"success": true, "attachment": {"id": "attachment", "url": "https://github.com/example"}}}}),
            ),
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                json!({"errors": [{"message": "ambiguous comment failure"}]}),
            ),
            (
                StatusCode::OK,
                json!({"data": {"commentCreate": {"success": true, "comment": {"id": "duplicate"}}}}),
            ),
        ])
        .await;
        let worker =
            LinearDeliveryWorker::new_for_test(LinearDeliveryConfig::test(url, false)).unwrap();

        let error = worker.deliver_job(&job(false)).await.unwrap_err();
        assert!(error.retryable);
        assert_eq!(requests.lock().unwrap().len(), 3);
    }

    #[test]
    fn claim_request_filters_to_github_push_jobs() {
        let claim = LinearDeliveryRequest {
            worker_id: "linear-worker".to_owned(),
            orgs: vec![],
            repositories: vec![],
            lease_seconds: 120,
        }
        .into_claim();
        assert_eq!(claim.task_types, vec!["github_push"]);
        assert!(claim.accepts(&job(false)));
        let mut other = job(false);
        other.task_type = "github_issue".to_owned();
        assert!(!claim.accepts(&other));
    }
}
