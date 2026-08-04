use std::{
    collections::BTreeSet,
    env, fmt,
    sync::Arc,
    time::Duration,
};

use reqwest::{
    header::{AUTHORIZATION, CONTENT_TYPE, RETRY_AFTER},
    redirect::Policy,
    Client, Response, StatusCode, Url,
};
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use thiserror::Error;
use tokio::time::sleep;

use crate::prompt_reconciliation::{
    LinearMutationKind, LinearMutationPlan, ReconciliationPlan,
};

const APPLY_ENV: &str = "PROMPT_RECONCILIATION_APPLY_ENABLED";
const CONFIRMATION: &str = "APPLY PROMPT RECONCILIATION";
const GITHUB_TOKEN_ENV: &str = "PROMPT_RECONCILIATION_GITHUB_TOKEN";
const GITHUB_ALLOWLIST_ENV: &str = "PROMPT_RECONCILIATION_GITHUB_REPOSITORIES";
const GITHUB_API_URL_ENV: &str = "PROMPT_RECONCILIATION_GITHUB_API_URL";
const LINEAR_TOKEN_ENV: &str = "PROMPT_RECONCILIATION_LINEAR_TOKEN";
const LINEAR_API_URL_ENV: &str = "PROMPT_RECONCILIATION_LINEAR_API_URL";
const LINEAR_TEAM_ID_ENV: &str = "PROMPT_RECONCILIATION_LINEAR_TEAM_ID";
const LINEAR_AUTH_SCHEME_ENV: &str = "PROMPT_RECONCILIATION_LINEAR_AUTH_SCHEME";
const DEFAULT_GITHUB_API_URL: &str = "https://api.github.com/";
const DEFAULT_LINEAR_API_URL: &str = "https://api.linear.app/graphql";
const DEFAULT_MAX_RESPONSE_BYTES: usize = 512 * 1024;
const MAX_SECRET_BYTES: usize = 16 * 1024;
const MAX_IDENTIFIER_BYTES: usize = 256;
const MAX_TITLE_BYTES: usize = 1_024;
const MAX_BODY_BYTES: usize = 128 * 1024;
const MAX_RETRY_AFTER_SECONDS: u64 = 300;
const MAX_CANDIDATES: usize = 25;
const MAX_COMMENTS_PER_ISSUE: usize = 50;

const LINEAR_PROJECT_QUERY: &str = r#"
query PromptReconciliationProject($name: String!) {
  projects(first: 3, filter: { name: { eq: $name } }) {
    nodes { id name }
  }
}
"#;

const LINEAR_ISSUE_QUERY: &str = r#"
query PromptReconciliationIssue($id: String!) {
  issue(id: $id) {
    id
    identifier
    url
    title
    description
    project { id name }
    comments(first: 50) { nodes { body } }
  }
}
"#;

const LINEAR_CANDIDATE_QUERY: &str = r#"
query PromptReconciliationCandidates($projectId: ID!, $title: String!, $marker: String!) {
  issues(
    first: 25
    filter: {
      project: { id: { eq: $projectId } }
      or: [
        { title: { eqIgnoreCase: $title } }
        { description: { contains: $marker } }
      ]
    }
  ) {
    nodes {
      id
      identifier
      url
      title
      description
      project { id name }
      comments(first: 50) { nodes { body } }
    }
  }
}
"#;

const LINEAR_COMMENT_MUTATION: &str = r#"
mutation PromptReconciliationComment($input: CommentCreateInput!) {
  commentCreate(input: $input) {
    success
    comment { id }
  }
}
"#;

const LINEAR_CREATE_MUTATION: &str = r#"
mutation PromptReconciliationCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier url title description project { id name } }
  }
}
"#;

#[derive(Clone)]
pub struct Secret(Arc<str>);

impl Secret {
    pub fn from_environment(name: &'static str) -> Result<Self, AdapterError> {
        let value = env::var(name)
            .map_err(|_| AdapterError::policy(format!("required credential {name} is unavailable")))?;
        Self::new(value)
    }

    fn new(value: String) -> Result<Self, AdapterError> {
        if value.trim().is_empty() || value.len() > MAX_SECRET_BYTES {
            return Err(AdapterError::policy(
                "adapter credential is empty or exceeds the configured bound",
            ));
        }
        Ok(Self(Arc::from(value)))
    }

    fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for Secret {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Secret([REDACTED])")
    }
}

#[derive(Debug, Error)]
pub enum AdapterError {
    #[error("policy refusal: {0}")]
    Policy(String),
    #[error("safe read failed for {operation}; retryable={retryable}")]
    SafeRead {
        operation: &'static str,
        retryable: bool,
    },
    #[error("remote mutation outcome is ambiguous for {operation}")]
    AmbiguousMutation { operation: &'static str },
    #[error("remote service rejected {operation} with status {status}")]
    Upstream {
        operation: &'static str,
        status: u16,
    },
    #[error("remote response for {operation} is malformed")]
    Malformed { operation: &'static str },
}

impl AdapterError {
    fn policy(message: impl Into<String>) -> Self {
        Self::Policy(message.into())
    }

    pub fn retryable(&self) -> bool {
        matches!(
            self,
            Self::SafeRead {
                retryable: true,
                ..
            }
        )
    }

    pub fn ambiguous_mutation(&self) -> bool {
        matches!(self, Self::AmbiguousMutation { .. })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RepositoryAllowlist {
    repositories: BTreeSet<String>,
}

impl RepositoryAllowlist {
    pub fn parse(value: &str) -> Result<Self, AdapterError> {
        let mut repositories = BTreeSet::new();
        for entry in value.split(',').map(str::trim).filter(|entry| !entry.is_empty()) {
            let (owner, repository) = split_repository(entry)?;
            repositories.insert(repository_key(owner, repository));
        }
        if repositories.is_empty() {
            return Err(AdapterError::policy(
                "repository allowlist must contain at least one exact owner/repository",
            ));
        }
        Ok(Self { repositories })
    }

    pub fn permits(&self, repository: &str) -> bool {
        split_repository(repository)
            .map(|(owner, name)| self.repositories.contains(&repository_key(owner, name)))
            .unwrap_or(false)
    }
}

#[derive(Debug, Clone)]
struct HttpPolicy {
    timeout: Duration,
    max_response_bytes: usize,
    max_read_attempts: usize,
    retry_delay: Duration,
}

impl Default for HttpPolicy {
    fn default() -> Self {
        Self {
            timeout: Duration::from_secs(10),
            max_response_bytes: DEFAULT_MAX_RESPONSE_BYTES,
            max_read_attempts: 3,
            retry_delay: Duration::from_millis(100),
        }
    }
}

#[derive(Debug, Clone)]
pub struct GithubEvidenceConfig {
    api_base: Url,
    token: Secret,
    allowlist: RepositoryAllowlist,
    policy: HttpPolicy,
}

impl GithubEvidenceConfig {
    pub fn from_env() -> Result<Self, AdapterError> {
        let api_base = env::var(GITHUB_API_URL_ENV)
            .unwrap_or_else(|_| DEFAULT_GITHUB_API_URL.to_owned())
            .parse::<Url>()
            .map_err(|_| AdapterError::policy("GitHub API URL is invalid"))?;
        validate_endpoint(&api_base, "api.github.com", false)?;
        let allowlist = RepositoryAllowlist::parse(
            &env::var(GITHUB_ALLOWLIST_ENV).map_err(|_| {
                AdapterError::policy(format!("{GITHUB_ALLOWLIST_ENV} is required"))
            })?,
        )?;
        Ok(Self {
            api_base: ensure_trailing_slash(api_base),
            token: Secret::from_environment(GITHUB_TOKEN_ENV)?,
            allowlist,
            policy: HttpPolicy::default(),
        })
    }

    #[cfg(test)]
    fn test(api_base: Url, allowlist: &str) -> Self {
        Self {
            api_base: ensure_trailing_slash(api_base),
            token: Secret::new("github-test-token".to_owned()).unwrap(),
            allowlist: RepositoryAllowlist::parse(allowlist).unwrap(),
            policy: HttpPolicy {
                timeout: Duration::from_secs(2),
                max_response_bytes: 32 * 1024,
                max_read_attempts: 2,
                retry_delay: Duration::ZERO,
            },
        }
    }
}

#[derive(Clone)]
pub struct GithubEvidenceClient {
    client: Client,
    config: Arc<GithubEvidenceConfig>,
}

impl GithubEvidenceClient {
    pub fn new(config: GithubEvidenceConfig) -> Result<Self, AdapterError> {
        let client = Client::builder()
            .redirect(Policy::none())
            .timeout(config.policy.timeout)
            .user_agent("ai-agent-coordinator-prompt-reconciliation/1")
            .build()
            .map_err(|_| AdapterError::policy("could not build GitHub evidence client"))?;
        Ok(Self {
            client,
            config: Arc::new(config),
        })
    }

    pub async fn resolve_link(&self, link: &str) -> Result<ResolvedGithubEvidence, AdapterError> {
        let parsed = parse_github_evidence_link(link)?;
        if !self.config.allowlist.permits(&parsed.repository) {
            return Err(AdapterError::policy(format!(
                "repository {} is outside the reviewed GitHub allowlist",
                parsed.repository
            )));
        }

        match parsed.kind {
            ParsedGithubKind::Commit(ref sha) => {
                let path = format!("repos/{}/commits/{sha}", parsed.repository);
                let value = self.get_json(&path, "GitHub commit lookup").await?;
                let canonical_url = required_string(&value, "html_url", "GitHub commit lookup")?;
                validate_canonical_github_url(canonical_url, &parsed.repository, "commit", sha)?;
                let response_sha = required_string(&value, "sha", "GitHub commit lookup")?;
                if response_sha != sha {
                    return Err(AdapterError::Malformed {
                        operation: "GitHub commit lookup",
                    });
                }
                Ok(ResolvedGithubEvidence {
                    repository: parsed.repository,
                    kind: GithubEvidenceKind::Commit,
                    canonical_url: canonical_url.to_owned(),
                    object_id: response_sha.to_owned(),
                    merged: None,
                    base_branch: None,
                })
            }
            ParsedGithubKind::PullRequest(number) => {
                let path = format!("repos/{}/pulls/{number}", parsed.repository);
                let value = self.get_json(&path, "GitHub pull request lookup").await?;
                let canonical_url =
                    required_string(&value, "html_url", "GitHub pull request lookup")?;
                validate_canonical_github_url(
                    canonical_url,
                    &parsed.repository,
                    "pull",
                    &number.to_string(),
                )?;
                let returned_number = value
                    .get("number")
                    .and_then(Value::as_u64)
                    .ok_or(AdapterError::Malformed {
                        operation: "GitHub pull request lookup",
                    })?;
                if returned_number != number {
                    return Err(AdapterError::Malformed {
                        operation: "GitHub pull request lookup",
                    });
                }
                let merged = value.get("merged").and_then(Value::as_bool);
                let base_branch = value
                    .pointer("/base/ref")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                Ok(ResolvedGithubEvidence {
                    repository: parsed.repository,
                    kind: GithubEvidenceKind::PullRequest,
                    canonical_url: canonical_url.to_owned(),
                    object_id: number.to_string(),
                    merged,
                    base_branch,
                })
            }
        }
    }

    async fn get_json(
        &self,
        path: &str,
        operation: &'static str,
    ) -> Result<Value, AdapterError> {
        let url = self
            .config
            .api_base
            .join(path)
            .map_err(|_| AdapterError::policy("could not construct GitHub API URL"))?;
        let maximum_attempts = self.config.policy.max_read_attempts.max(1);

        for attempt in 1..=maximum_attempts {
            let response = self
                .client
                .get(url.clone())
                .header(AUTHORIZATION, format!("Bearer {}", self.config.token.expose()))
                .header("x-github-api-version", "2022-11-28")
                .header("accept", "application/vnd.github+json")
                .send()
                .await;

            match response {
                Ok(response) => {
                    let status = response.status();
                    let retry_after = bounded_retry_after(&response);
                    let bytes = match read_bounded(
                        response,
                        self.config.policy.max_response_bytes,
                        operation,
                    )
                    .await
                    {
                        Ok(bytes) => bytes,
                        Err(error) if error.retryable() && attempt < maximum_attempts => {
                            sleep(self.config.policy.retry_delay).await;
                            continue;
                        }
                        Err(error) => return Err(error),
                    };
                    if status.is_success() {
                        return serde_json::from_slice(&bytes)
                            .map_err(|_| AdapterError::Malformed { operation });
                    }
                    if retryable_status(status) && attempt < maximum_attempts {
                        sleep(retry_after.unwrap_or(self.config.policy.retry_delay)).await;
                        continue;
                    }
                    return if retryable_status(status) {
                        Err(AdapterError::SafeRead {
                            operation,
                            retryable: true,
                        })
                    } else {
                        Err(AdapterError::Upstream {
                            operation,
                            status: status.as_u16(),
                        })
                    };
                }
                Err(_) if attempt < maximum_attempts => {
                    sleep(self.config.policy.retry_delay).await;
                }
                Err(_) => {
                    return Err(AdapterError::SafeRead {
                        operation,
                        retryable: true,
                    });
                }
            }
        }
        Err(AdapterError::SafeRead {
            operation,
            retryable: true,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum GithubEvidenceKind {
    Commit,
    PullRequest,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ResolvedGithubEvidence {
    pub repository: String,
    pub kind: GithubEvidenceKind,
    pub canonical_url: String,
    pub object_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub merged: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub base_branch: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LinearAuthScheme {
    ApiKey,
    Bearer,
}

#[derive(Debug, Clone)]
pub struct LinearReconciliationConfig {
    api_url: Url,
    token: Secret,
    auth_scheme: LinearAuthScheme,
    team_id: String,
    dry_run: bool,
    policy: HttpPolicy,
}

impl LinearReconciliationConfig {
    pub fn from_env(dry_run: bool) -> Result<Self, AdapterError> {
        let api_url = env::var(LINEAR_API_URL_ENV)
            .unwrap_or_else(|_| DEFAULT_LINEAR_API_URL.to_owned())
            .parse::<Url>()
            .map_err(|_| AdapterError::policy("Linear API URL is invalid"))?;
        validate_endpoint(&api_url, "api.linear.app", false)?;
        let team_id = env::var(LINEAR_TEAM_ID_ENV)
            .map_err(|_| AdapterError::policy(format!("{LINEAR_TEAM_ID_ENV} is required")))?;
        validate_safe_token("Linear team ID", &team_id)?;
        let auth_scheme = match env::var(LINEAR_AUTH_SCHEME_ENV)
            .unwrap_or_else(|_| "api_key".to_owned())
            .trim()
            .to_ascii_lowercase()
            .as_str()
        {
            "api_key" | "apikey" | "key" => LinearAuthScheme::ApiKey,
            "bearer" | "oauth" => LinearAuthScheme::Bearer,
            _ => {
                return Err(AdapterError::policy(format!(
                    "{LINEAR_AUTH_SCHEME_ENV} must be api_key or bearer"
                )))
            }
        };
        let token = Secret::from_environment(LINEAR_TOKEN_ENV)?;
        Ok(Self {
            api_url,
            token,
            auth_scheme,
            team_id,
            dry_run,
            policy: HttpPolicy::default(),
        })
    }

    #[cfg(test)]
    fn test(api_url: Url, dry_run: bool) -> Self {
        Self {
            api_url,
            token: Secret::new("linear-test-token".to_owned()).unwrap(),
            auth_scheme: LinearAuthScheme::ApiKey,
            team_id: "team-uuid".to_owned(),
            dry_run,
            policy: HttpPolicy {
                timeout: Duration::from_secs(2),
                max_response_bytes: 32 * 1024,
                max_read_attempts: 2,
                retry_delay: Duration::ZERO,
            },
        }
    }
}

#[derive(Clone)]
pub struct LinearReconciliationClient {
    client: Client,
    config: Arc<LinearReconciliationConfig>,
}

impl LinearReconciliationClient {
    pub fn new(config: LinearReconciliationConfig) -> Result<Self, AdapterError> {
        let client = Client::builder()
            .redirect(Policy::none())
            .timeout(config.policy.timeout)
            .user_agent("ai-agent-coordinator-prompt-reconciliation/1")
            .build()
            .map_err(|_| AdapterError::policy("could not build Linear reconciliation client"))?;
        Ok(Self {
            client,
            config: Arc::new(config),
        })
    }

    pub fn dry_run(&self) -> bool {
        self.config.dry_run
    }

    pub async fn apply_plan(
        &self,
        plan_bytes: &[u8],
        plan: &ReconciliationPlan,
        authorization: &ApplyAuthorization,
    ) -> Result<ApplyReport, AdapterError> {
        authorization.validate_for(plan_bytes, plan, self.config.dry_run)?;
        let mut mutations = Vec::new();
        for prompt in &plan.prompts {
            if let Some(mutation) = &prompt.mutation {
                mutations.push(self.apply_mutation(mutation, authorization).await?);
            }
        }
        Ok(ApplyReport {
            account_fingerprint: authorization.account_fingerprint.clone(),
            plan_digest: authorization.plan_digest.clone(),
            dry_run: self.config.dry_run,
            mutations,
        })
    }

    pub async fn apply_mutation(
        &self,
        mutation: &LinearMutationPlan,
        authorization: &ApplyAuthorization,
    ) -> Result<AppliedMutation, AdapterError> {
        validate_mutation(mutation)?;
        let project = self.resolve_project(&mutation.project).await?;
        let marker = operation_marker(&authorization.plan_digest, &mutation.operation_id)?;

        match mutation.kind {
            LinearMutationKind::Amend => {
                let issue_id = mutation.issue_id.as_deref().ok_or_else(|| {
                    AdapterError::policy("amend mutation requires a canonical issue identifier")
                })?;
                self.amend_existing(mutation, &project, issue_id, &marker)
                    .await
            }
            LinearMutationKind::Create => {
                if mutation.issue_id.is_some() {
                    return Err(AdapterError::policy(
                        "create mutation must not include a canonical issue identifier",
                    ));
                }
                self.update_before_create(mutation, &project, &marker).await
            }
        }
    }

    async fn update_before_create(
        &self,
        mutation: &LinearMutationPlan,
        project: &LinearProject,
        marker: &str,
    ) -> Result<AppliedMutation, AdapterError> {
        let candidates = self
            .find_candidates(project, &mutation.title, marker)
            .await?;
        if let Some(candidate) = single_candidate(candidates)? {
            return self
                .amend_candidate(mutation, project, candidate, marker)
                .await;
        }
        if self.config.dry_run {
            return Ok(AppliedMutation::planned(
                mutation,
                MutationApplyOutcome::PlannedCreate,
                None,
            ));
        }

        let final_candidates = self
            .find_candidates(project, &mutation.title, marker)
            .await?;
        if let Some(candidate) = single_candidate(final_candidates)? {
            return self
                .amend_candidate(mutation, project, candidate, marker)
                .await;
        }

        let body = body_with_marker(marker, &mutation.body)?;
        let data = self
            .mutate_graphql(
                LINEAR_CREATE_MUTATION,
                json!({
                    "input": {
                        "teamId": self.config.team_id,
                        "projectId": project.id,
                        "title": mutation.title,
                        "description": body,
                    }
                }),
                "Linear issue creation",
            )
            .await?;
        let created = data
            .pointer("/issueCreate/issue")
            .ok_or(AdapterError::AmbiguousMutation {
                operation: "Linear issue creation",
            })?;
        let issue = LinearIssue::from_value(created, "Linear issue creation")?;
        validate_issue_project(&issue, project)?;
        if issue.title != mutation.title || !issue.contains_marker(marker) {
            return Err(AdapterError::AmbiguousMutation {
                operation: "Linear issue creation",
            });
        }
        Ok(AppliedMutation::from_issue(
            mutation,
            MutationApplyOutcome::Created,
            issue,
        ))
    }

    async fn amend_candidate(
        &self,
        mutation: &LinearMutationPlan,
        project: &LinearProject,
        candidate: LinearIssue,
        marker: &str,
    ) -> Result<AppliedMutation, AdapterError> {
        validate_issue_project(&candidate, project)?;
        if candidate.contains_marker(marker) {
            return Ok(AppliedMutation::from_issue(
                mutation,
                MutationApplyOutcome::AlreadyApplied,
                candidate,
            ));
        }
        self.amend_loaded_issue(mutation, candidate, marker).await
    }

    async fn amend_existing(
        &self,
        mutation: &LinearMutationPlan,
        project: &LinearProject,
        issue_id: &str,
        marker: &str,
    ) -> Result<AppliedMutation, AdapterError> {
        let issue = self.fetch_issue(issue_id).await?;
        validate_issue_project(&issue, project)?;
        if issue.contains_marker(marker) {
            return Ok(AppliedMutation::from_issue(
                mutation,
                MutationApplyOutcome::AlreadyApplied,
                issue,
            ));
        }
        self.amend_loaded_issue(mutation, issue, marker).await
    }

    async fn amend_loaded_issue(
        &self,
        mutation: &LinearMutationPlan,
        issue: LinearIssue,
        marker: &str,
    ) -> Result<AppliedMutation, AdapterError> {
        if self.config.dry_run {
            return Ok(AppliedMutation::from_issue(
                mutation,
                MutationApplyOutcome::PlannedAmend,
                issue,
            ));
        }
        let body = body_with_marker(marker, &mutation.body)?;
        let data = self
            .mutate_graphql(
                LINEAR_COMMENT_MUTATION,
                json!({"input": {"issueId": issue.id, "body": body}}),
                "Linear issue amendment",
            )
            .await?;
        let success = data
            .pointer("/commentCreate/success")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        if !success {
            return Err(AdapterError::AmbiguousMutation {
                operation: "Linear issue amendment",
            });
        }
        Ok(AppliedMutation::from_issue(
            mutation,
            MutationApplyOutcome::Amended,
            issue,
        ))
    }

    async fn resolve_project(&self, name: &str) -> Result<LinearProject, AdapterError> {
        validate_identifier("Linear project name", name)?;
        let data = self
            .read_graphql(
                LINEAR_PROJECT_QUERY,
                json!({"name": name}),
                "Linear project lookup",
            )
            .await?;
        let nodes = data
            .pointer("/projects/nodes")
            .and_then(Value::as_array)
            .ok_or(AdapterError::Malformed {
                operation: "Linear project lookup",
            })?;
        let exact = nodes
            .iter()
            .filter_map(|value| LinearProject::from_value(value).ok())
            .filter(|project| project.name == name)
            .collect::<Vec<_>>();
        match exact.as_slice() {
            [project] => Ok(project.clone()),
            [] => Err(AdapterError::policy(format!(
                "Linear project {name} was not found"
            ))),
            _ => Err(AdapterError::policy(format!(
                "Linear project {name} is ambiguous"
            ))),
        }
    }

    async fn fetch_issue(&self, identifier: &str) -> Result<LinearIssue, AdapterError> {
        validate_safe_token("Linear issue identifier", identifier)?;
        let data = self
            .read_graphql(
                LINEAR_ISSUE_QUERY,
                json!({"id": identifier}),
                "Linear issue lookup",
            )
            .await?;
        let value = data.get("issue").ok_or(AdapterError::Malformed {
            operation: "Linear issue lookup",
        })?;
        if value.is_null() {
            return Err(AdapterError::policy(format!(
                "Linear issue {identifier} was not found"
            )));
        }
        LinearIssue::from_value(value, "Linear issue lookup")
    }

    async fn find_candidates(
        &self,
        project: &LinearProject,
        title: &str,
        marker: &str,
    ) -> Result<Vec<LinearIssue>, AdapterError> {
        let data = self
            .read_graphql(
                LINEAR_CANDIDATE_QUERY,
                json!({"projectId": project.id, "title": title, "marker": marker}),
                "Linear duplicate search",
            )
            .await?;
        let nodes = data
            .pointer("/issues/nodes")
            .and_then(Value::as_array)
            .ok_or(AdapterError::Malformed {
                operation: "Linear duplicate search",
            })?;
        if nodes.len() > MAX_CANDIDATES {
            return Err(AdapterError::policy(
                "Linear duplicate search exceeded the bounded candidate count",
            ));
        }
        let mut issues = nodes
            .iter()
            .map(|value| LinearIssue::from_value(value, "Linear duplicate search"))
            .collect::<Result<Vec<_>, _>>()?;
        issues.sort_by(|left, right| left.identifier.cmp(&right.identifier));
        issues.dedup_by(|left, right| left.id == right.id);
        for issue in &issues {
            validate_issue_project(issue, project)?;
        }
        Ok(issues)
    }

    async fn read_graphql(
        &self,
        query: &str,
        variables: Value,
        operation: &'static str,
    ) -> Result<Value, AdapterError> {
        let maximum_attempts = self.config.policy.max_read_attempts.max(1);
        for attempt in 1..=maximum_attempts {
            let response = self.graphql_request(query, variables.clone()).send().await;
            match response {
                Ok(response) => {
                    let status = response.status();
                    let retry_after = bounded_retry_after(&response);
                    let bytes = match read_bounded(
                        response,
                        self.config.policy.max_response_bytes,
                        operation,
                    )
                    .await
                    {
                        Ok(bytes) => bytes,
                        Err(error) if error.retryable() && attempt < maximum_attempts => {
                            sleep(self.config.policy.retry_delay).await;
                            continue;
                        }
                        Err(error) => return Err(error),
                    };
                    if status.is_success() {
                        return parse_graphql_data(&bytes, operation, false);
                    }
                    if retryable_status(status) && attempt < maximum_attempts {
                        sleep(retry_after.unwrap_or(self.config.policy.retry_delay)).await;
                        continue;
                    }
                    return if retryable_status(status) {
                        Err(AdapterError::SafeRead {
                            operation,
                            retryable: true,
                        })
                    } else {
                        Err(AdapterError::Upstream {
                            operation,
                            status: status.as_u16(),
                        })
                    };
                }
                Err(_) if attempt < maximum_attempts => {
                    sleep(self.config.policy.retry_delay).await;
                }
                Err(_) => {
                    return Err(AdapterError::SafeRead {
                        operation,
                        retryable: true,
                    });
                }
            }
        }
        Err(AdapterError::SafeRead {
            operation,
            retryable: true,
        })
    }

    async fn mutate_graphql(
        &self,
        query: &str,
        variables: Value,
        operation: &'static str,
    ) -> Result<Value, AdapterError> {
        if self.config.dry_run {
            return Err(AdapterError::policy(
                "dry-run reconciliation attempted a remote mutation",
            ));
        }
        let response = self
            .graphql_request(query, variables)
            .send()
            .await
            .map_err(|_| AdapterError::AmbiguousMutation { operation })?;
        let status = response.status();
        let bytes = read_bounded(response, self.config.policy.max_response_bytes, operation)
            .await
            .map_err(|_| AdapterError::AmbiguousMutation { operation })?;
        if !status.is_success() {
            return if status.is_server_error() || status == StatusCode::TOO_MANY_REQUESTS {
                Err(AdapterError::AmbiguousMutation { operation })
            } else {
                Err(AdapterError::Upstream {
                    operation,
                    status: status.as_u16(),
                })
            };
        }
        parse_graphql_data(&bytes, operation, true)
    }

    fn graphql_request(&self, query: &str, variables: Value) -> reqwest::RequestBuilder {
        let authorization = match self.config.auth_scheme {
            LinearAuthScheme::ApiKey => self.config.token.expose().to_owned(),
            LinearAuthScheme::Bearer => format!("Bearer {}", self.config.token.expose()),
        };
        self.client
            .post(self.config.api_url.clone())
            .header(CONTENT_TYPE, "application/json")
            .header(AUTHORIZATION, authorization)
            .json(&json!({"query": query, "variables": variables}))
    }
}

#[derive(Debug, Clone)]
pub struct ApplyAuthorization {
    account_fingerprint: String,
    plan_digest: String,
    live_apply_authorized: bool,
}

impl ApplyAuthorization {
    pub fn verify(
        plan_bytes: &[u8],
        plan: &ReconciliationPlan,
        supplied_account: &str,
        supplied_digest: &str,
        supplied_confirmation: &str,
        live_apply: bool,
    ) -> Result<Self, AdapterError> {
        if live_apply && env::var(APPLY_ENV).as_deref() != Ok("true") {
            return Err(AdapterError::policy(format!(
                "live apply requires {APPLY_ENV}=true"
            )));
        }
        let expected_digest = sha256_hex(plan_bytes);
        if supplied_account != plan.account_fingerprint {
            return Err(AdapterError::policy(
                "account confirmation does not match the reviewed plan",
            ));
        }
        if supplied_digest != expected_digest || !is_lower_hex_digest(supplied_digest) {
            return Err(AdapterError::policy(
                "digest confirmation does not match the exact reviewed plan bytes",
            ));
        }
        if supplied_confirmation != CONFIRMATION {
            return Err(AdapterError::policy(
                "the exact apply confirmation phrase is required",
            ));
        }
        Ok(Self {
            account_fingerprint: supplied_account.to_owned(),
            plan_digest: expected_digest,
            live_apply_authorized: live_apply,
        })
    }

    #[cfg(test)]
    fn test(account_fingerprint: &str, plan_digest: &str) -> Self {
        Self {
            account_fingerprint: account_fingerprint.to_owned(),
            plan_digest: plan_digest.to_owned(),
            live_apply_authorized: true,
        }
    }

    fn validate_for(
        &self,
        plan_bytes: &[u8],
        plan: &ReconciliationPlan,
        dry_run: bool,
    ) -> Result<(), AdapterError> {
        if self.account_fingerprint != plan.account_fingerprint
            || self.plan_digest != sha256_hex(plan_bytes)
        {
            return Err(AdapterError::policy(
                "apply authorization does not match the exact plan",
            ));
        }
        if !dry_run && !self.live_apply_authorized {
            return Err(AdapterError::policy(
                "dry-run authorization cannot be reused for live mutations",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MutationApplyOutcome {
    PlannedAmend,
    PlannedCreate,
    AlreadyApplied,
    Amended,
    Created,
}

#[derive(Debug, Clone, Serialize)]
pub struct AppliedMutation {
    pub operation_id: String,
    pub outcome: MutationApplyOutcome,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub issue_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub issue_url: Option<String>,
}

impl AppliedMutation {
    fn planned(
        mutation: &LinearMutationPlan,
        outcome: MutationApplyOutcome,
        issue: Option<&LinearIssue>,
    ) -> Self {
        Self {
            operation_id: mutation.operation_id.clone(),
            outcome,
            issue_id: issue.map(|issue| issue.identifier.clone()),
            issue_url: issue.map(|issue| issue.url.clone()),
        }
    }

    fn from_issue(
        mutation: &LinearMutationPlan,
        outcome: MutationApplyOutcome,
        issue: LinearIssue,
    ) -> Self {
        Self::planned(mutation, outcome, Some(&issue))
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct ApplyReport {
    pub account_fingerprint: String,
    pub plan_digest: String,
    pub dry_run: bool,
    pub mutations: Vec<AppliedMutation>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct LinearProject {
    id: String,
    name: String,
}

impl LinearProject {
    fn from_value(value: &Value) -> Result<Self, AdapterError> {
        Ok(Self {
            id: required_string(value, "id", "Linear project lookup")?.to_owned(),
            name: required_string(value, "name", "Linear project lookup")?.to_owned(),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct LinearIssue {
    id: String,
    identifier: String,
    url: String,
    title: String,
    description: String,
    project: LinearProject,
    comments: Vec<String>,
}

impl LinearIssue {
    fn from_value(value: &Value, operation: &'static str) -> Result<Self, AdapterError> {
        let project = value
            .get("project")
            .ok_or(AdapterError::Malformed { operation })?;
        let comment_nodes = value
            .pointer("/comments/nodes")
            .and_then(Value::as_array)
            .ok_or(AdapterError::Malformed { operation })?;
        if comment_nodes.len() > MAX_COMMENTS_PER_ISSUE {
            return Err(AdapterError::policy(
                "Linear issue response exceeded the bounded comment count",
            ));
        }
        let comments = comment_nodes
            .iter()
            .map(|node| required_string(node, "body", operation).map(str::to_owned))
            .collect::<Result<Vec<_>, _>>()?;
        let url = required_string(value, "url", operation)?;
        validate_linear_issue_url(url)?;
        Ok(Self {
            id: required_string(value, "id", operation)?.to_owned(),
            identifier: required_string(value, "identifier", operation)?.to_owned(),
            url: url.to_owned(),
            title: required_string(value, "title", operation)?.to_owned(),
            description: value
                .get("description")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
            project: LinearProject {
                id: required_string(project, "id", operation)?.to_owned(),
                name: required_string(project, "name", operation)?.to_owned(),
            },
            comments,
        })
    }

    fn contains_marker(&self, marker: &str) -> bool {
        self.description.contains(marker)
            || self.comments.iter().any(|body| body.contains(marker))
    }
}

#[derive(Debug)]
struct ParsedGithubEvidence {
    repository: String,
    kind: ParsedGithubKind,
}

#[derive(Debug)]
enum ParsedGithubKind {
    Commit(String),
    PullRequest(u64),
}

fn parse_github_evidence_link(link: &str) -> Result<ParsedGithubEvidence, AdapterError> {
    let url = link
        .parse::<Url>()
        .map_err(|_| AdapterError::policy("GitHub evidence link is invalid"))?;
    if url.scheme() != "https"
        || url.host_str() != Some("github.com")
        || url.port().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(AdapterError::policy(
            "GitHub evidence must be a canonical HTTPS github.com URL",
        ));
    }
    let segments = url
        .path_segments()
        .map(|segments| segments.filter(|segment| !segment.is_empty()).collect::<Vec<_>>())
        .unwrap_or_default();
    if segments.len() != 4 {
        return Err(AdapterError::policy(
            "GitHub evidence must identify one commit or pull request",
        ));
    }
    validate_slug("GitHub owner", segments[0])?;
    validate_slug("GitHub repository", segments[1])?;
    let repository = format!("{}/{}", segments[0], segments[1]);
    let kind = match segments[2] {
        "commit" => {
            if segments[3].len() != 40
                || !segments[3]
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            {
                return Err(AdapterError::policy(
                    "GitHub commit evidence requires a full lowercase SHA-1",
                ));
            }
            ParsedGithubKind::Commit(segments[3].to_owned())
        }
        "pull" => {
            let number = segments[3]
                .parse::<u64>()
                .ok()
                .filter(|number| *number > 0)
                .ok_or_else(|| {
                    AdapterError::policy("GitHub pull request evidence number is invalid")
                })?;
            ParsedGithubKind::PullRequest(number)
        }
        _ => {
            return Err(AdapterError::policy(
                "GitHub evidence must identify a commit or pull request",
            ))
        }
    };
    Ok(ParsedGithubEvidence { repository, kind })
}

fn validate_canonical_github_url(
    value: &str,
    repository: &str,
    object: &str,
    object_id: &str,
) -> Result<(), AdapterError> {
    let parsed = value
        .parse::<Url>()
        .map_err(|_| AdapterError::Malformed {
            operation: "GitHub canonical URL validation",
        })?;
    let expected_path = format!("/{repository}/{object}/{object_id}");
    if parsed.scheme() == "https"
        && parsed.host_str() == Some("github.com")
        && parsed.port().is_none()
        && parsed.username().is_empty()
        && parsed.password().is_none()
        && parsed.query().is_none()
        && parsed.fragment().is_none()
        && parsed.path() == expected_path
    {
        Ok(())
    } else {
        Err(AdapterError::Malformed {
            operation: "GitHub canonical URL validation",
        })
    }
}

fn validate_linear_issue_url(value: &str) -> Result<(), AdapterError> {
    let parsed = value
        .parse::<Url>()
        .map_err(|_| AdapterError::Malformed {
            operation: "Linear issue URL validation",
        })?;
    let segments = parsed
        .path_segments()
        .map(|segments| segments.filter(|segment| !segment.is_empty()).collect::<Vec<_>>())
        .unwrap_or_default();
    let valid_path = segments.len() >= 4 && segments.get(1) == Some(&"issue");
    if parsed.scheme() == "https"
        && parsed.host_str() == Some("linear.app")
        && parsed.port().is_none()
        && parsed.username().is_empty()
        && parsed.password().is_none()
        && parsed.query().is_none()
        && parsed.fragment().is_none()
        && valid_path
    {
        Ok(())
    } else {
        Err(AdapterError::Malformed {
            operation: "Linear issue URL validation",
        })
    }
}

fn validate_endpoint(
    url: &Url,
    expected_https_host: &str,
    allow_loopback_http: bool,
) -> Result<(), AdapterError> {
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(AdapterError::policy(
            "adapter endpoint must not contain credentials, query, or fragment",
        ));
    }
    let host = url
        .host_str()
        .ok_or_else(|| AdapterError::policy("adapter endpoint has no host"))?;
    let https_ok = url.scheme() == "https"
        && host.eq_ignore_ascii_case(expected_https_host)
        && url.port_or_known_default() == Some(443);
    let loopback_ok = url.scheme() == "http"
        && allow_loopback_http
        && matches!(host, "localhost" | "127.0.0.1" | "::1")
        && url.port().is_some();
    if https_ok || loopback_ok {
        Ok(())
    } else {
        Err(AdapterError::policy(
            "adapter endpoint must use its pinned HTTPS host or explicit loopback test HTTP",
        ))
    }
}

fn ensure_trailing_slash(mut url: Url) -> Url {
    if !url.path().ends_with('/') {
        let path = format!("{}/", url.path());
        url.set_path(&path);
    }
    url
}

fn split_repository(value: &str) -> Result<(&str, &str), AdapterError> {
    if value.matches('/').count() != 1 {
        return Err(AdapterError::policy(
            "repository must use exact owner/repository form",
        ));
    }
    let (owner, repository) = value
        .split_once('/')
        .ok_or_else(|| AdapterError::policy("repository must use owner/repository form"))?;
    validate_slug("repository owner", owner)?;
    validate_slug("repository name", repository)?;
    Ok((owner, repository))
}

fn repository_key(owner: &str, repository: &str) -> String {
    format!(
        "{}/{}",
        owner.to_ascii_lowercase(),
        repository.to_ascii_lowercase()
    )
}

fn validate_slug(label: &str, value: &str) -> Result<(), AdapterError> {
    if value.is_empty()
        || value.len() > MAX_IDENTIFIER_BYTES
        || value.bytes().any(|byte| {
            !byte.is_ascii_alphanumeric() && !matches!(byte, b'-' | b'_' | b'.')
        })
    {
        return Err(AdapterError::policy(format!("{label} is invalid")));
    }
    Ok(())
}

fn validate_safe_token(label: &str, value: &str) -> Result<(), AdapterError> {
    if value.is_empty()
        || value.len() > MAX_IDENTIFIER_BYTES
        || value.bytes().any(|byte| {
            !byte.is_ascii_alphanumeric()
                && !matches!(byte, b'-' | b'_' | b'.' | b':' | b'/')
        })
    {
        return Err(AdapterError::policy(format!("{label} is invalid")));
    }
    Ok(())
}

fn validate_identifier(label: &str, value: &str) -> Result<(), AdapterError> {
    if value.trim().is_empty()
        || value.len() > MAX_IDENTIFIER_BYTES
        || value.chars().any(char::is_control)
    {
        return Err(AdapterError::policy(format!("{label} is invalid")));
    }
    Ok(())
}

fn validate_mutation(mutation: &LinearMutationPlan) -> Result<(), AdapterError> {
    validate_safe_token("operation ID", &mutation.operation_id)?;
    validate_safe_token("idempotency key", &mutation.idempotency_key)?;
    validate_identifier("Linear project", &mutation.project)?;
    if mutation.operation_id != mutation.idempotency_key {
        return Err(AdapterError::policy(
            "operation ID and idempotency key must match",
        ));
    }
    if mutation.title.trim().is_empty()
        || mutation.title.len() > MAX_TITLE_BYTES
        || mutation.title.chars().any(char::is_control)
    {
        return Err(AdapterError::policy("Linear mutation title is invalid"));
    }
    if mutation.body.trim().is_empty()
        || mutation.body.len() > MAX_BODY_BYTES
        || mutation.body.contains('\0')
    {
        return Err(AdapterError::policy("Linear mutation body is invalid"));
    }
    Ok(())
}

fn validate_issue_project(
    issue: &LinearIssue,
    project: &LinearProject,
) -> Result<(), AdapterError> {
    if issue.project.id == project.id && issue.project.name == project.name {
        Ok(())
    } else {
        Err(AdapterError::policy(format!(
            "Linear issue {} does not belong to project {}",
            issue.identifier, project.name
        )))
    }
}

fn single_candidate(candidates: Vec<LinearIssue>) -> Result<Option<LinearIssue>, AdapterError> {
    match candidates.len() {
        0 => Ok(None),
        1 => Ok(candidates.into_iter().next()),
        count => Err(AdapterError::policy(format!(
            "Linear duplicate search returned {count} possible canonical issues"
        ))),
    }
}

fn operation_marker(plan_digest: &str, operation_id: &str) -> Result<String, AdapterError> {
    if !is_lower_hex_digest(plan_digest) {
        return Err(AdapterError::policy(
            "operation marker requires a lowercase SHA-256 plan digest",
        ));
    }
    validate_safe_token("operation ID", operation_id)?;
    Ok(format!(
        "<!-- prompt-reconciliation:{plan_digest}:{operation_id} -->"
    ))
}

fn body_with_marker(marker: &str, body: &str) -> Result<String, AdapterError> {
    let value = format!("{marker}\n{body}");
    if value.len() > MAX_BODY_BYTES {
        Err(AdapterError::policy(
            "Linear mutation body plus operation marker exceeds the configured bound",
        ))
    } else {
        Ok(value)
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn is_lower_hex_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn required_string<'a>(
    value: &'a Value,
    field: &str,
    operation: &'static str,
) -> Result<&'a str, AdapterError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= MAX_BODY_BYTES)
        .ok_or(AdapterError::Malformed { operation })
}

fn retryable_status(status: StatusCode) -> bool {
    matches!(
        status,
        StatusCode::REQUEST_TIMEOUT
            | StatusCode::TOO_EARLY
            | StatusCode::TOO_MANY_REQUESTS
            | StatusCode::INTERNAL_SERVER_ERROR
            | StatusCode::BAD_GATEWAY
            | StatusCode::SERVICE_UNAVAILABLE
            | StatusCode::GATEWAY_TIMEOUT
    )
}

fn bounded_retry_after(response: &Response) -> Option<Duration> {
    response
        .headers()
        .get(RETRY_AFTER)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.trim().parse::<u64>().ok())
        .filter(|seconds| *seconds <= MAX_RETRY_AFTER_SECONDS)
        .map(Duration::from_secs)
}

async fn read_bounded(
    mut response: Response,
    maximum_bytes: usize,
    operation: &'static str,
) -> Result<Vec<u8>, AdapterError> {
    let mut bytes = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|_| AdapterError::SafeRead {
            operation,
            retryable: true,
        })?
    {
        if bytes.len().saturating_add(chunk.len()) > maximum_bytes {
            return Err(AdapterError::policy(
                "adapter response exceeds the configured byte bound",
            ));
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

fn parse_graphql_data(
    bytes: &[u8],
    operation: &'static str,
    mutation: bool,
) -> Result<Value, AdapterError> {
    let envelope: Value = serde_json::from_slice(bytes).map_err(|_| {
        if mutation {
            AdapterError::AmbiguousMutation { operation }
        } else {
            AdapterError::Malformed { operation }
        }
    })?;
    if envelope
        .get("errors")
        .and_then(Value::as_array)
        .is_some_and(|errors| !errors.is_empty())
    {
        return if mutation {
            Err(AdapterError::AmbiguousMutation { operation })
        } else {
            Err(AdapterError::Upstream {
                operation,
                status: 200,
            })
        };
    }
    envelope
        .get("data")
        .cloned()
        .ok_or_else(|| {
            if mutation {
                AdapterError::AmbiguousMutation { operation }
            } else {
                AdapterError::Malformed { operation }
            }
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{
        body::Body,
        extract::{Request, State},
        http::StatusCode,
        response::Response as AxumResponse,
        routing::get,
        Router,
    };
    use std::{collections::VecDeque, sync::Mutex as StdMutex};
    use tokio::net::TcpListener;

    #[derive(Clone)]
    struct MockState {
        responses: Arc<StdMutex<VecDeque<(StatusCode, Value)>>>,
        requests: Arc<StdMutex<Vec<RecordedRequest>>>,
    }

    #[derive(Debug, Clone)]
    struct RecordedRequest {
        method: String,
        path: String,
        authorization: Option<String>,
        body: Value,
    }

    async fn mock_request(
        State(state): State<MockState>,
        request: Request,
    ) -> AxumResponse {
        let method = request.method().to_string();
        let path = request.uri().path().to_owned();
        let authorization = request
            .headers()
            .get(AUTHORIZATION)
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned);
        let body = axum::body::to_bytes(request.into_body(), MAX_BODY_BYTES)
            .await
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
            .unwrap_or(Value::Null);
        state.requests.lock().unwrap().push(RecordedRequest {
            method,
            path,
            authorization,
            body,
        });
        let (status, value) = state.responses.lock().unwrap().pop_front().unwrap();
        AxumResponse::builder()
            .status(status)
            .header(CONTENT_TYPE, "application/json")
            .body(Body::from(serde_json::to_vec(&value).unwrap()))
            .unwrap()
    }

    async fn mock_server(
        responses: Vec<(StatusCode, Value)>,
    ) -> (Url, Arc<StdMutex<Vec<RecordedRequest>>>) {
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
                    .route("/", get(mock_request).post(mock_request))
                    .route("/*path", get(mock_request).post(mock_request))
                    .with_state(state),
            )
            .await
            .unwrap();
        });
        (Url::parse(&format!("http://{address}/")).unwrap(), requests)
    }

    fn project_response() -> Value {
        json!({"data": {"projects": {"nodes": [{"id": "project-uuid", "name": "github.com/ORESoftware"}]}}})
    }

    fn issue_value(identifier: &str, description: &str, comments: Vec<&str>) -> Value {
        json!({
            "id": format!("issue-{identifier}"),
            "identifier": identifier,
            "url": format!("https://linear.app/denman/issue/{identifier}/test"),
            "title": "[Prompt intake] Ticket management",
            "description": description,
            "project": {"id": "project-uuid", "name": "github.com/ORESoftware"},
            "comments": {"nodes": comments.into_iter().map(|body| json!({"body": body})).collect::<Vec<_>>()}
        })
    }

    fn mutation(kind: LinearMutationKind) -> LinearMutationPlan {
        LinearMutationPlan {
            operation_id: "operation-1".to_owned(),
            idempotency_key: "operation-1".to_owned(),
            kind,
            project: "github.com/ORESoftware".to_owned(),
            issue_id: (kind == LinearMutationKind::Amend).then(|| "DEN-1610".to_owned()),
            title: "[Prompt intake] Ticket management".to_owned(),
            body: "Add guarded ticket reconciliation.".to_owned(),
        }
    }

    fn authorization() -> ApplyAuthorization {
        ApplyAuthorization::test("account", &"a".repeat(64))
    }

    #[test]
    fn secret_debug_is_redacted() {
        let secret = Secret::new("do-not-log".to_owned()).unwrap();
        let rendered = format!("{secret:?}");
        assert_eq!(rendered, "Secret([REDACTED])");
        assert!(!rendered.contains("do-not-log"));
    }

    #[test]
    fn allowlist_is_exact_and_case_insensitive() {
        let allowlist = RepositoryAllowlist::parse(
            "ORESoftware/ai-agent-coordinator.rs,sonus-auris/mobile",
        )
        .unwrap();
        assert!(allowlist.permits("oresoftware/AI-AGENT-COORDINATOR.RS"));
        assert!(!allowlist.permits("ORESoftware/other"));
        assert!(!allowlist.permits("evil/ai-agent-coordinator.rs"));
        assert!(RepositoryAllowlist::parse("owner/repo/extra").is_err());
    }

    #[tokio::test]
    async fn github_commit_lookup_is_authenticated_and_canonical() {
        let sha = "a".repeat(40);
        let link = format!(
            "https://github.com/ORESoftware/ai-agent-coordinator.rs/commit/{sha}"
        );
        let (base, requests) = mock_server(vec![(
            StatusCode::OK,
            json!({"sha": sha, "html_url": link}),
        )])
        .await;
        let client = GithubEvidenceClient::new(GithubEvidenceConfig::test(
            base,
            "ORESoftware/ai-agent-coordinator.rs",
        ))
        .unwrap();
        let resolved = client.resolve_link(&link).await.unwrap();
        assert_eq!(resolved.kind, GithubEvidenceKind::Commit);
        let requests = requests.lock().unwrap();
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].method, "GET");
        assert_eq!(
            requests[0].path,
            format!("/repos/ORESoftware/ai-agent-coordinator.rs/commits/{sha}")
        );
        assert_eq!(
            requests[0].authorization.as_deref(),
            Some("Bearer github-test-token")
        );
    }

    #[tokio::test]
    async fn github_lookup_rejects_outside_allowlist_before_request() {
        let (base, requests) = mock_server(vec![]).await;
        let client = GithubEvidenceClient::new(GithubEvidenceConfig::test(
            base,
            "ORESoftware/ai-agent-coordinator.rs",
        ))
        .unwrap();
        let result = client
            .resolve_link(
                "https://github.com/evil/other/commit/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            )
            .await;
        assert!(matches!(result, Err(AdapterError::Policy(_))));
        assert!(requests.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn github_pull_request_lookup_is_authenticated_and_canonical() {
        let link = "https://github.com/ORESoftware/ai-agent-coordinator.rs/pull/75";
        let (base, requests) = mock_server(vec![(
            StatusCode::OK,
            json!({
                "number": 75,
                "html_url": link,
                "merged": false,
                "base": {"ref": "main"}
            }),
        )])
        .await;
        let client = GithubEvidenceClient::new(GithubEvidenceConfig::test(
            base,
            "ORESoftware/ai-agent-coordinator.rs",
        ))
        .unwrap();
        let resolved = client.resolve_link(link).await.unwrap();
        assert_eq!(resolved.kind, GithubEvidenceKind::PullRequest);
        assert_eq!(resolved.object_id, "75");
        assert_eq!(resolved.merged, Some(false));
        assert_eq!(resolved.base_branch.as_deref(), Some("main"));
        let requests = requests.lock().unwrap();
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].path, "/repos/ORESoftware/ai-agent-coordinator.rs/pulls/75");
        assert_eq!(
            requests[0].authorization.as_deref(),
            Some("Bearer github-test-token")
        );
    }

    #[tokio::test]
    async fn github_safe_read_retries_one_transient_failure() {
        let sha = "b".repeat(40);
        let link = format!(
            "https://github.com/ORESoftware/ai-agent-coordinator.rs/commit/{sha}"
        );
        let (base, requests) = mock_server(vec![
            (
                StatusCode::TOO_MANY_REQUESTS,
                json!({"message": "try again"}),
            ),
            (StatusCode::OK, json!({"sha": sha, "html_url": link})),
        ])
        .await;
        let client = GithubEvidenceClient::new(GithubEvidenceConfig::test(
            base,
            "ORESoftware/ai-agent-coordinator.rs",
        ))
        .unwrap();
        assert!(client.resolve_link(&link).await.is_ok());
        assert_eq!(requests.lock().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn github_rejects_canonical_url_for_a_different_object() {
        let sha = "c".repeat(40);
        let different = "d".repeat(40);
        let link = format!(
            "https://github.com/ORESoftware/ai-agent-coordinator.rs/commit/{sha}"
        );
        let (base, requests) = mock_server(vec![(
            StatusCode::OK,
            json!({
                "sha": sha,
                "html_url": format!(
                    "https://github.com/ORESoftware/ai-agent-coordinator.rs/commit/{different}"
                )
            }),
        )])
        .await;
        let client = GithubEvidenceClient::new(GithubEvidenceConfig::test(
            base,
            "ORESoftware/ai-agent-coordinator.rs",
        ))
        .unwrap();
        let error = client.resolve_link(&link).await.unwrap_err();
        assert!(matches!(error, AdapterError::Malformed { .. }));
        assert_eq!(requests.lock().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn github_rejects_oversized_response() {
        let sha = "e".repeat(40);
        let link = format!(
            "https://github.com/ORESoftware/ai-agent-coordinator.rs/commit/{sha}"
        );
        let (base, requests) = mock_server(vec![(
            StatusCode::OK,
            json!({"sha": sha, "html_url": link, "padding": "x".repeat(40 * 1024)}),
        )])
        .await;
        let client = GithubEvidenceClient::new(GithubEvidenceConfig::test(
            base,
            "ORESoftware/ai-agent-coordinator.rs",
        ))
        .unwrap();
        let error = client.resolve_link(&link).await.unwrap_err();
        assert!(matches!(error, AdapterError::Policy(_)));
        assert_eq!(requests.lock().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn create_updates_single_existing_candidate_before_create() {
        let (url, requests) = mock_server(vec![
            (StatusCode::OK, project_response()),
            (
                StatusCode::OK,
                json!({"data": {"issues": {"nodes": [issue_value("DEN-1610", "", vec![])]}}}),
            ),
            (
                StatusCode::OK,
                json!({"data": {"commentCreate": {"success": true, "comment": {"id": "comment"}}}}),
            ),
        ])
        .await;
        let client = LinearReconciliationClient::new(LinearReconciliationConfig::test(url, false))
            .unwrap();
        let result = client
            .apply_mutation(&mutation(LinearMutationKind::Create), &authorization())
            .await
            .unwrap();
        assert_eq!(result.outcome, MutationApplyOutcome::Amended);
        let requests = requests.lock().unwrap();
        assert_eq!(requests.len(), 3);
        assert!(requests[1].body["query"]
            .as_str()
            .unwrap()
            .contains("PromptReconciliationCandidates"));
        assert!(requests[2].body["query"]
            .as_str()
            .unwrap()
            .contains("commentCreate"));
        assert!(!requests.iter().any(|request| request.body["query"]
            .as_str()
            .is_some_and(|query| query.contains("issueCreate"))));
    }

    #[tokio::test]
    async fn linear_safe_read_retries_one_transient_failure() {
        let (url, requests) = mock_server(vec![
            (
                StatusCode::TOO_MANY_REQUESTS,
                json!({"errors": [{"message": "try again"}]}),
            ),
            (StatusCode::OK, project_response()),
            (StatusCode::OK, json!({"data": {"issues": {"nodes": []}}})),
        ])
        .await;
        let client = LinearReconciliationClient::new(LinearReconciliationConfig::test(url, true))
            .unwrap();
        let result = client
            .apply_mutation(&mutation(LinearMutationKind::Create), &authorization())
            .await
            .unwrap();
        assert_eq!(result.outcome, MutationApplyOutcome::PlannedCreate);
        let requests = requests.lock().unwrap();
        assert_eq!(requests.len(), 3);
        assert!(requests
            .iter()
            .all(|request| request.authorization.as_deref() == Some("linear-test-token")));
    }

    #[tokio::test]
    async fn multiple_candidates_fail_closed_before_mutation() {
        let (url, requests) = mock_server(vec![
            (StatusCode::OK, project_response()),
            (
                StatusCode::OK,
                json!({"data": {"issues": {"nodes": [
                    issue_value("DEN-1610", "", vec![]),
                    issue_value("DEN-1612", "", vec![])
                ]}}}),
            ),
        ])
        .await;
        let client = LinearReconciliationClient::new(LinearReconciliationConfig::test(url, false))
            .unwrap();
        let error = client
            .apply_mutation(&mutation(LinearMutationKind::Create), &authorization())
            .await
            .unwrap_err();
        assert!(matches!(error, AdapterError::Policy(_)));
        assert_eq!(requests.lock().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn duplicate_race_on_final_search_amends_instead_of_creating() {
        let (url, requests) = mock_server(vec![
            (StatusCode::OK, project_response()),
            (StatusCode::OK, json!({"data": {"issues": {"nodes": []}}})),
            (
                StatusCode::OK,
                json!({"data": {"issues": {"nodes": [issue_value("DEN-1701", "", vec![])]}}}),
            ),
            (
                StatusCode::OK,
                json!({"data": {"commentCreate": {"success": true, "comment": {"id": "comment"}}}}),
            ),
        ])
        .await;
        let client = LinearReconciliationClient::new(LinearReconciliationConfig::test(url, false))
            .unwrap();
        let result = client
            .apply_mutation(&mutation(LinearMutationKind::Create), &authorization())
            .await
            .unwrap();
        assert_eq!(result.outcome, MutationApplyOutcome::Amended);
        let requests = requests.lock().unwrap();
        assert_eq!(requests.len(), 4);
        assert!(!requests.iter().any(|request| {
            request.body["query"]
                .as_str()
                .is_some_and(|query| query.contains("issueCreate"))
        }));
    }

    #[tokio::test]
    async fn create_runs_final_duplicate_search_then_creates_once() {
        let marker = operation_marker(&"a".repeat(64), "operation-1").unwrap();
        let created = issue_value("DEN-1700", &marker, vec![]);
        let (url, requests) = mock_server(vec![
            (StatusCode::OK, project_response()),
            (StatusCode::OK, json!({"data": {"issues": {"nodes": []}}})),
            (StatusCode::OK, json!({"data": {"issues": {"nodes": []}}})),
            (
                StatusCode::OK,
                json!({"data": {"issueCreate": {"success": true, "issue": created}}}),
            ),
        ])
        .await;
        let client = LinearReconciliationClient::new(LinearReconciliationConfig::test(url, false))
            .unwrap();
        let result = client
            .apply_mutation(&mutation(LinearMutationKind::Create), &authorization())
            .await
            .unwrap();
        assert_eq!(result.outcome, MutationApplyOutcome::Created);
        let requests = requests.lock().unwrap();
        assert_eq!(requests.len(), 4);
        assert_eq!(
            requests
                .iter()
                .filter(|request| request.body["query"]
                    .as_str()
                    .is_some_and(|query| query.contains("PromptReconciliationCandidates")))
                .count(),
            2
        );
        assert_eq!(
            requests
                .iter()
                .filter(|request| request.body["query"]
                    .as_str()
                    .is_some_and(|query| query.contains("issueCreate")))
                .count(),
            1
        );
    }

    #[tokio::test]
    async fn rerun_with_marker_performs_zero_mutations() {
        let marker = operation_marker(&"a".repeat(64), "operation-1").unwrap();
        let (url, requests) = mock_server(vec![
            (StatusCode::OK, project_response()),
            (
                StatusCode::OK,
                json!({"data": {"issues": {"nodes": [issue_value("DEN-1700", &marker, vec![])]}}}),
            ),
        ])
        .await;
        let client = LinearReconciliationClient::new(LinearReconciliationConfig::test(url, false))
            .unwrap();
        let result = client
            .apply_mutation(&mutation(LinearMutationKind::Create), &authorization())
            .await
            .unwrap();
        assert_eq!(result.outcome, MutationApplyOutcome::AlreadyApplied);
        assert_eq!(requests.lock().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn amend_rejects_wrong_project_before_mutation() {
        let mut issue = issue_value("DEN-1610", "", vec![]);
        issue["project"] = json!({"id": "other", "name": "github.com/other"});
        let (url, requests) = mock_server(vec![
            (StatusCode::OK, project_response()),
            (StatusCode::OK, json!({"data": {"issue": issue}})),
        ])
        .await;
        let client = LinearReconciliationClient::new(LinearReconciliationConfig::test(url, false))
            .unwrap();
        let error = client
            .apply_mutation(&mutation(LinearMutationKind::Amend), &authorization())
            .await
            .unwrap_err();
        assert!(matches!(error, AdapterError::Policy(_)));
        assert_eq!(requests.lock().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn ambiguous_create_failure_is_not_retried() {
        let (url, requests) = mock_server(vec![
            (StatusCode::OK, project_response()),
            (StatusCode::OK, json!({"data": {"issues": {"nodes": []}}})),
            (StatusCode::OK, json!({"data": {"issues": {"nodes": []}}})),
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                json!({"errors": [{"message": "ambiguous"}]}),
            ),
            (
                StatusCode::OK,
                json!({"data": {"issueCreate": {"success": true, "issue": issue_value("DEN-duplicate", "", vec![])}}}),
            ),
        ])
        .await;
        let client = LinearReconciliationClient::new(LinearReconciliationConfig::test(url, false))
            .unwrap();
        let error = client
            .apply_mutation(&mutation(LinearMutationKind::Create), &authorization())
            .await
            .unwrap_err();
        assert!(error.ambiguous_mutation());
        assert_eq!(requests.lock().unwrap().len(), 4);
    }

    #[tokio::test]
    async fn ambiguous_amend_failure_is_not_retried() {
        let (url, requests) = mock_server(vec![
            (StatusCode::OK, project_response()),
            (
                StatusCode::OK,
                json!({"data": {"issue": issue_value("DEN-1610", "", vec![])}}),
            ),
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                json!({"errors": [{"message": "ambiguous"}]}),
            ),
            (
                StatusCode::OK,
                json!({"data": {"commentCreate": {"success": true, "comment": {"id": "duplicate"}}}}),
            ),
        ])
        .await;
        let client = LinearReconciliationClient::new(LinearReconciliationConfig::test(url, false))
            .unwrap();
        let error = client
            .apply_mutation(&mutation(LinearMutationKind::Amend), &authorization())
            .await
            .unwrap_err();
        assert!(error.ambiguous_mutation());
        assert_eq!(requests.lock().unwrap().len(), 3);
    }

    #[tokio::test]
    async fn create_response_without_operation_marker_is_ambiguous() {
        let created = issue_value("DEN-1702", "missing marker", vec![]);
        let (url, requests) = mock_server(vec![
            (StatusCode::OK, project_response()),
            (StatusCode::OK, json!({"data": {"issues": {"nodes": []}}})),
            (StatusCode::OK, json!({"data": {"issues": {"nodes": []}}})),
            (
                StatusCode::OK,
                json!({"data": {"issueCreate": {"success": true, "issue": created}}}),
            ),
        ])
        .await;
        let client = LinearReconciliationClient::new(LinearReconciliationConfig::test(url, false))
            .unwrap();
        let error = client
            .apply_mutation(&mutation(LinearMutationKind::Create), &authorization())
            .await
            .unwrap_err();
        assert!(error.ambiguous_mutation());
        assert_eq!(requests.lock().unwrap().len(), 4);
    }

    #[tokio::test]
    async fn dry_run_makes_no_mutation_request() {
        let (url, requests) = mock_server(vec![
            (StatusCode::OK, project_response()),
            (StatusCode::OK, json!({"data": {"issues": {"nodes": []}}})),
        ])
        .await;
        let client = LinearReconciliationClient::new(LinearReconciliationConfig::test(url, true))
            .unwrap();
        let result = client
            .apply_mutation(&mutation(LinearMutationKind::Create), &authorization())
            .await
            .unwrap();
        assert_eq!(result.outcome, MutationApplyOutcome::PlannedCreate);
        assert_eq!(requests.lock().unwrap().len(), 2);
    }
}
