use std::{env, sync::Arc, time::Duration};

use anyhow::{bail, Context};
use reqwest::{
    header::{ACCEPT, AUTHORIZATION, USER_AGENT},
    Client, Method, Response, StatusCode,
};
use serde::{Deserialize, Serialize};

use crate::error::AppError;

const DEFAULT_API_BASE_URL: &str = "https://api.github.com";
const DEFAULT_API_VERSION: &str = "2022-11-28";
const DEFAULT_USER_AGENT: &str = "ai-agent-coordinator";
const ADMIN_ENABLED_ENV: &str = "GITHUB_REPOSITORY_ADMIN_ENABLED";
const ADMIN_TOKEN_ENV: &str = "GITHUB_REPOSITORY_ADMIN_TOKEN";
const ADMIN_ALLOWED_ORGS_ENV: &str = "GITHUB_REPOSITORY_ADMIN_ALLOWED_ORGS";
const API_BASE_URL_ENV: &str = "GITHUB_API_BASE_URL";
const API_VERSION_ENV: &str = "GITHUB_API_VERSION";
const USER_AGENT_ENV: &str = "GITHUB_API_USER_AGENT";

#[derive(Clone)]
pub struct GithubRepositoryAdmin {
    client: Client,
    settings: Arc<Settings>,
    token: Option<String>,
}

#[derive(Debug)]
struct Settings {
    enabled: bool,
    allowed_orgs: Vec<String>,
    api_base_url: String,
    api_version: String,
    user_agent: String,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum RepositoryVisibility {
    Private,
    Public,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum RepositoryInitialization {
    Empty,
    Readme,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CreateRepositoryRequest {
    pub organization: String,
    pub name: String,
    pub visibility: RepositoryVisibility,
    pub initialization: RepositoryInitialization,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default = "default_dry_run")]
    pub dry_run: bool,
    #[serde(default)]
    pub confirm_repository: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct RepositoryCreationResult {
    pub dry_run: bool,
    pub created: bool,
    pub existing: bool,
    pub full_name: String,
    pub visibility: RepositoryVisibility,
    pub initialization: RepositoryInitialization,
    pub description: Option<String>,
    pub api_url: String,
    pub html_url: Option<String>,
    pub repository_id: Option<u64>,
    pub default_branch: Option<String>,
}

#[derive(Debug, Serialize)]
struct GithubCreateRepositoryBody<'a> {
    name: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    description: Option<&'a str>,
    visibility: RepositoryVisibility,
    auto_init: bool,
    has_issues: bool,
    has_projects: bool,
    has_wiki: bool,
    allow_squash_merge: bool,
    allow_merge_commit: bool,
    allow_rebase_merge: bool,
}

#[derive(Debug, Deserialize)]
struct GithubRepositoryResponse {
    id: u64,
    full_name: String,
    html_url: String,
    private: bool,
    default_branch: String,
}

#[derive(Debug, Deserialize)]
struct GithubErrorResponse {
    message: String,
}

#[derive(Debug)]
struct ValidatedRequest {
    organization: String,
    name: String,
    full_name: String,
    visibility: RepositoryVisibility,
    initialization: RepositoryInitialization,
    description: Option<String>,
    dry_run: bool,
}

impl GithubRepositoryAdmin {
    pub fn from_env() -> anyhow::Result<Self> {
        let enabled = parse_bool_env(ADMIN_ENABLED_ENV, false)?;
        let allowed_orgs = env::var(ADMIN_ALLOWED_ORGS_ENV)
            .unwrap_or_default()
            .split(',')
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .collect::<Vec<_>>();

        if enabled && allowed_orgs.is_empty() {
            bail!(
                "{ADMIN_ENABLED_ENV} is true but {ADMIN_ALLOWED_ORGS_ENV} is empty; live repository creation must be organization-allowlisted"
            );
        }

        let token = env::var(ADMIN_TOKEN_ENV)
            .ok()
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty());
        if enabled && token.is_none() {
            bail!(
                "{ADMIN_ENABLED_ENV} is true but {ADMIN_TOKEN_ENV} is not set; use a short-lived GitHub App installation token"
            );
        }

        let api_base_url = env::var(API_BASE_URL_ENV)
            .unwrap_or_else(|_| DEFAULT_API_BASE_URL.to_owned())
            .trim_end_matches('/')
            .to_owned();
        if !is_safe_api_base_url(&api_base_url) {
            bail!(
                "{API_BASE_URL_ENV} must use HTTPS, except loopback HTTP is allowed for local tests"
            );
        }

        let api_version = env::var(API_VERSION_ENV)
            .unwrap_or_else(|_| DEFAULT_API_VERSION.to_owned())
            .trim()
            .to_owned();
        if api_version.is_empty() || api_version.len() > 32 {
            bail!("{API_VERSION_ENV} must be a non-empty API version string");
        }

        let user_agent = env::var(USER_AGENT_ENV)
            .unwrap_or_else(|_| DEFAULT_USER_AGENT.to_owned())
            .trim()
            .to_owned();
        if user_agent.is_empty() || user_agent.len() > 128 {
            bail!("{USER_AGENT_ENV} must be between 1 and 128 characters");
        }

        let client = Client::builder()
            .timeout(Duration::from_secs(30))
            .build()
            .context("failed to construct GitHub repository administration client")?;

        Ok(Self {
            client,
            settings: Arc::new(Settings {
                enabled,
                allowed_orgs,
                api_base_url,
                api_version,
                user_agent,
            }),
            token,
        })
    }

    pub async fn create_repository(
        &self,
        request: CreateRepositoryRequest,
    ) -> Result<RepositoryCreationResult, AppError> {
        let request = self.validate_request(request)?;
        let api_url = format!(
            "{}/repos/{}/{}",
            self.settings.api_base_url, request.organization, request.name
        );

        if request.dry_run {
            return Ok(RepositoryCreationResult {
                dry_run: true,
                created: false,
                existing: false,
                full_name: request.full_name,
                visibility: request.visibility,
                initialization: request.initialization,
                description: request.description,
                api_url,
                html_url: None,
                repository_id: None,
                default_branch: None,
            });
        }

        if let Some(existing) = self.fetch_repository(&api_url).await? {
            let existing_visibility = if existing.private {
                RepositoryVisibility::Private
            } else {
                RepositoryVisibility::Public
            };
            if existing_visibility != request.visibility {
                return Err(AppError::BadRequest(format!(
                    "repository {} already exists with {:?} visibility, not {:?}",
                    request.full_name, existing_visibility, request.visibility
                )));
            }

            return Ok(result_from_github(request, api_url, existing, false, true));
        }

        let create_url = format!(
            "{}/orgs/{}/repos",
            self.settings.api_base_url, request.organization
        );
        let body = GithubCreateRepositoryBody {
            name: &request.name,
            description: request.description.as_deref(),
            visibility: request.visibility,
            auto_init: matches!(request.initialization, RepositoryInitialization::Readme),
            has_issues: true,
            has_projects: false,
            has_wiki: false,
            allow_squash_merge: true,
            allow_merge_commit: true,
            allow_rebase_merge: false,
        };
        let response = self
            .authorized_request(Method::POST, &create_url)?
            .json(&body)
            .send()
            .await
            .map_err(|error| AppError::Upstream(format!("GitHub request failed: {error}")))?;

        if response.status() != StatusCode::CREATED {
            return Err(map_github_error(response).await);
        }

        let repository = response
            .json::<GithubRepositoryResponse>()
            .await
            .map_err(|error| {
                AppError::Upstream(format!(
                    "GitHub returned an invalid repository response: {error}"
                ))
            })?;

        Ok(result_from_github(
            request, api_url, repository, true, false,
        ))
    }

    fn validate_request(
        &self,
        request: CreateRepositoryRequest,
    ) -> Result<ValidatedRequest, AppError> {
        validate_organization(&request.organization)?;
        validate_repository_name(&request.name)?;

        if !self
            .settings
            .allowed_orgs
            .iter()
            .any(|allowed| allowed.eq_ignore_ascii_case(request.organization.as_str()))
        {
            return Err(AppError::Forbidden(format!(
                "organization {:?} is not present in {}",
                request.organization, ADMIN_ALLOWED_ORGS_ENV
            )));
        }

        let description = request
            .description
            .map(|description| description.trim().to_owned())
            .filter(|description| !description.is_empty());
        if description
            .as_ref()
            .is_some_and(|description| description.chars().count() > 350)
        {
            return Err(AppError::BadRequest(
                "repository description must not exceed 350 characters".to_owned(),
            ));
        }
        if description
            .as_ref()
            .is_some_and(|description| description.chars().any(char::is_control))
        {
            return Err(AppError::BadRequest(
                "repository description must not contain control characters".to_owned(),
            ));
        }

        let full_name = format!("{}/{}", request.organization, request.name);
        if !request.dry_run {
            if !self.settings.enabled {
                return Err(AppError::Forbidden(format!(
                    "live repository creation is disabled; set {ADMIN_ENABLED_ENV}=true after reviewing a dry run"
                )));
            }
            if self.token.is_none() {
                return Err(AppError::Forbidden(format!(
                    "live repository creation requires {ADMIN_TOKEN_ENV}"
                )));
            }
            if request.confirm_repository.as_deref() != Some(full_name.as_str()) {
                return Err(AppError::BadRequest(format!(
                    "live repository creation requires confirm_repository to equal {full_name:?}"
                )));
            }
        }

        Ok(ValidatedRequest {
            organization: request.organization,
            name: request.name,
            full_name,
            visibility: request.visibility,
            initialization: request.initialization,
            description,
            dry_run: request.dry_run,
        })
    }

    async fn fetch_repository(
        &self,
        api_url: &str,
    ) -> Result<Option<GithubRepositoryResponse>, AppError> {
        let response = self
            .authorized_request(Method::GET, api_url)?
            .send()
            .await
            .map_err(|error| AppError::Upstream(format!("GitHub request failed: {error}")))?;

        if response.status() == StatusCode::NOT_FOUND {
            return Ok(None);
        }
        if !response.status().is_success() {
            return Err(map_github_error(response).await);
        }

        response
            .json::<GithubRepositoryResponse>()
            .await
            .map(Some)
            .map_err(|error| {
                AppError::Upstream(format!(
                    "GitHub returned an invalid repository response: {error}"
                ))
            })
    }

    fn authorized_request(
        &self,
        method: Method,
        url: &str,
    ) -> Result<reqwest::RequestBuilder, AppError> {
        let token = self.token.as_deref().ok_or_else(|| {
            AppError::Forbidden(format!(
                "GitHub repository administration token {} is unavailable",
                ADMIN_TOKEN_ENV
            ))
        })?;

        Ok(self
            .client
            .request(method, url)
            .header(ACCEPT, "application/vnd.github+json")
            .header(USER_AGENT, self.settings.user_agent.as_str())
            .header("X-GitHub-Api-Version", self.settings.api_version.as_str())
            .header(AUTHORIZATION, format!("Bearer {token}")))
    }
}

fn result_from_github(
    request: ValidatedRequest,
    api_url: String,
    repository: GithubRepositoryResponse,
    created: bool,
    existing: bool,
) -> RepositoryCreationResult {
    RepositoryCreationResult {
        dry_run: false,
        created,
        existing,
        full_name: repository.full_name,
        visibility: request.visibility,
        initialization: request.initialization,
        description: request.description,
        api_url,
        html_url: Some(repository.html_url),
        repository_id: Some(repository.id),
        default_branch: if repository.default_branch.is_empty() {
            None
        } else {
            Some(repository.default_branch)
        },
    }
}

async fn map_github_error(response: Response) -> AppError {
    let status = response.status();
    let body = response.text().await.unwrap_or_default();
    let message = serde_json::from_str::<GithubErrorResponse>(&body)
        .map(|error| error.message)
        .unwrap_or_else(|_| "GitHub rejected the repository administration request".to_owned());
    let message = message.chars().take(500).collect::<String>();

    match status {
        StatusCode::UNAUTHORIZED => AppError::Upstream(format!(
            "GitHub rejected the repository administration credential: {message}"
        )),
        StatusCode::FORBIDDEN => AppError::Forbidden(format!(
            "GitHub denied repository administration: {message}"
        )),
        StatusCode::UNPROCESSABLE_ENTITY | StatusCode::CONFLICT => {
            AppError::BadRequest(format!("GitHub rejected repository settings: {message}"))
        }
        _ => AppError::Upstream(format!(
            "GitHub repository administration failed with HTTP {status}: {message}"
        )),
    }
}

fn validate_organization(value: &str) -> Result<(), AppError> {
    if value.is_empty() || value.len() > 39 {
        return Err(AppError::BadRequest(
            "organization must be between 1 and 39 ASCII characters".to_owned(),
        ));
    }
    if value.starts_with('-')
        || value.ends_with('-')
        || value.contains("--")
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    {
        return Err(AppError::BadRequest(
            "organization must contain only ASCII letters, numbers, and single interior hyphens"
                .to_owned(),
        ));
    }
    Ok(())
}

fn validate_repository_name(value: &str) -> Result<(), AppError> {
    if value.is_empty() || value.len() > 100 {
        return Err(AppError::BadRequest(
            "repository name must be between 1 and 100 ASCII characters".to_owned(),
        ));
    }
    if matches!(value, "." | "..")
        || value.ends_with(".git")
        || value.starts_with('.')
        || value.ends_with('.')
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
    {
        return Err(AppError::BadRequest(
            "repository name must contain only ASCII letters, numbers, hyphens, underscores, and interior dots"
                .to_owned(),
        ));
    }
    Ok(())
}

fn parse_bool_env(name: &str, default: bool) -> anyhow::Result<bool> {
    let Ok(value) = env::var(name) else {
        return Ok(default);
    };
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Ok(true),
        "0" | "false" | "no" | "off" => Ok(false),
        _ => bail!("{name} must be one of true/false, 1/0, yes/no, or on/off"),
    }
}

fn is_safe_api_base_url(value: &str) -> bool {
    value.starts_with("https://")
        || value.starts_with("http://127.0.0.1")
        || value.starts_with("http://localhost")
        || value.starts_with("http://[::1]")
}

fn default_dry_run() -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    fn admin(enabled: bool, token: Option<&str>, allowed_orgs: &[&str]) -> GithubRepositoryAdmin {
        GithubRepositoryAdmin {
            client: Client::new(),
            settings: Arc::new(Settings {
                enabled,
                allowed_orgs: allowed_orgs
                    .iter()
                    .map(|value| (*value).to_owned())
                    .collect(),
                api_base_url: DEFAULT_API_BASE_URL.to_owned(),
                api_version: DEFAULT_API_VERSION.to_owned(),
                user_agent: DEFAULT_USER_AGENT.to_owned(),
            }),
            token: token.map(str::to_owned),
        }
    }

    fn request() -> CreateRepositoryRequest {
        CreateRepositoryRequest {
            organization: "declarative-migrations".to_owned(),
            name: "declarative-migrations-monorepo".to_owned(),
            visibility: RepositoryVisibility::Private,
            initialization: RepositoryInitialization::Readme,
            description: Some("Organization monorepo".to_owned()),
            dry_run: true,
            confirm_repository: None,
        }
    }

    #[test]
    fn dry_run_is_the_deserialization_default() {
        let request: CreateRepositoryRequest = serde_json::from_value(serde_json::json!({
            "organization": "declarative-migrations",
            "name": "declarative-migrations-monorepo",
            "visibility": "private",
            "initialization": "readme"
        }))
        .unwrap();
        assert!(request.dry_run);
    }

    #[test]
    fn dry_run_does_not_require_live_mode_or_token() {
        let validated = admin(false, None, &["declarative-migrations"])
            .validate_request(request())
            .unwrap();
        assert!(validated.dry_run);
        assert_eq!(
            validated.full_name,
            "declarative-migrations/declarative-migrations-monorepo"
        );
    }

    #[test]
    fn live_mode_requires_exact_repository_confirmation() {
        let mut request = request();
        request.dry_run = false;
        let error = admin(true, Some("ephemeral-token"), &["declarative-migrations"])
            .validate_request(request)
            .unwrap_err();
        assert!(error.to_string().contains("confirm_repository"));
    }

    #[test]
    fn organization_allowlist_is_case_insensitive() {
        let validated = admin(false, None, &["Declarative-Migrations"])
            .validate_request(request())
            .unwrap();
        assert_eq!(validated.organization, "declarative-migrations");
    }

    #[test]
    fn rejects_unlisted_organizations() {
        let error = admin(false, None, &["oresoftware"])
            .validate_request(request())
            .unwrap_err();
        assert!(matches!(error, AppError::Forbidden(_)));
    }

    #[test]
    fn rejects_repository_names_that_can_escape_a_url_path() {
        let mut request = request();
        request.name = "../other".to_owned();
        let error = admin(false, None, &["declarative-migrations"])
            .validate_request(request)
            .unwrap_err();
        assert!(matches!(error, AppError::BadRequest(_)));
    }

    #[test]
    fn creation_body_disables_rebase_merges() {
        let request = request();
        let body = GithubCreateRepositoryBody {
            name: &request.name,
            description: request.description.as_deref(),
            visibility: request.visibility,
            auto_init: true,
            has_issues: true,
            has_projects: false,
            has_wiki: false,
            allow_squash_merge: true,
            allow_merge_commit: true,
            allow_rebase_merge: false,
        };
        let value = serde_json::to_value(body).unwrap();
        assert_eq!(value["allow_rebase_merge"], false);
        assert_eq!(value["visibility"], "private");
    }
}
