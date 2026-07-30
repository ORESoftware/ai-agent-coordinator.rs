use std::{collections::HashSet, sync::Arc};

use axum::http::HeaderMap;
use serde::Serialize;
use serde_json::{json, Value};
use tokio::sync::Semaphore;
use tracing::{info, warn};
use uuid::Uuid;

use crate::{
    config::{Config, ProviderTrust},
    db::{Database, UsageRecord},
    error::AppError,
    providers::ProviderRegistry,
    security::{SecretScanReport, SecretScanner},
};

#[derive(Clone)]
pub struct ModelGateway {
    config: Arc<Config>,
    database: Database,
    providers: ProviderRegistry,
    scanner: SecretScanner,
    semaphore: Arc<Semaphore>,
}

#[derive(Debug, Clone)]
pub struct RequestContext {
    pub request_id: String,
    pub org: String,
    pub repo: String,
    pub task_type: String,
    pub sensitivity: Sensitivity,
    pub allow_downgrade: bool,
    pub max_cost_usd: Option<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Sensitivity {
    Public,
    Internal,
    Confidential,
    Restricted,
}

impl Sensitivity {
    fn parse(value: Option<&str>) -> Result<Self, AppError> {
        match value.unwrap_or("internal") {
            "public" => Ok(Self::Public),
            "internal" => Ok(Self::Internal),
            "confidential" => Ok(Self::Confidential),
            "restricted" => Ok(Self::Restricted),
            other => Err(AppError::BadRequest(format!(
                "invalid x-fiducia-sensitivity value {other:?}"
            ))),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
struct RouteAttempt {
    model: String,
    provider: String,
    outcome: String,
    reason: Option<String>,
    estimated_cost_usd: f64,
}

impl ModelGateway {
    pub fn new(
        config: Arc<Config>,
        database: Database,
        providers: ProviderRegistry,
        scanner: SecretScanner,
    ) -> Self {
        let semaphore = Arc::new(Semaphore::new(
            config.server.max_concurrent_model_requests.max(1),
        ));
        Self {
            config,
            database,
            providers,
            scanner,
            semaphore,
        }
    }

    pub fn context_from_headers(&self, headers: &HeaderMap) -> Result<RequestContext, AppError> {
        let header = |name: &str| -> Result<Option<&str>, AppError> {
            headers
                .get(name)
                .map(|value| {
                    value.to_str().map_err(|_| {
                        AppError::BadRequest(format!("header {name} is not valid UTF-8"))
                    })
                })
                .transpose()
        };

        let org = header("x-fiducia-org")?.unwrap_or("").trim().to_owned();
        let repo = header("x-fiducia-repo")?.unwrap_or("").trim().to_owned();
        if self.config.routing.require_repository_context && (org.is_empty() || repo.is_empty()) {
            return Err(AppError::BadRequest(
                "x-fiducia-org and x-fiducia-repo headers are required".to_owned(),
            ));
        }

        let request_id = header("x-request-id")?
            .filter(|value| !value.is_empty())
            .map(str::to_owned)
            .unwrap_or_else(|| Uuid::new_v4().to_string());
        let task_type = header("x-fiducia-task")?
            .unwrap_or("general")
            .trim()
            .to_owned();
        let sensitivity = Sensitivity::parse(header("x-fiducia-sensitivity")?)?;
        let allow_downgrade = header("x-fiducia-allow-downgrade")?
            .map(parse_bool)
            .transpose()?
            .unwrap_or(true);
        let max_cost_usd = header("x-fiducia-max-cost-usd")?
            .map(|value| {
                value.parse::<f64>().map_err(|_| {
                    AppError::BadRequest(
                        "x-fiducia-max-cost-usd must be a positive number".to_owned(),
                    )
                })
            })
            .transpose()?
            .filter(|value| *value > 0.0);

        Ok(RequestContext {
            request_id,
            org,
            repo,
            task_type,
            sensitivity,
            allow_downgrade,
            max_cost_usd,
        })
    }

    pub async fn chat_completions(
        &self,
        context: RequestContext,
        mut body: Value,
    ) -> Result<Value, AppError> {
        if body.get("stream").and_then(Value::as_bool) == Some(true) {
            return Err(AppError::BadRequest(
                "streaming responses are not implemented in this release".to_owned(),
            ));
        }
        let requested_model = body
            .get("model")
            .and_then(Value::as_str)
            .unwrap_or("auto")
            .to_owned();

        let secret_report = self
            .scanner
            .scan_and_redact(&mut body, self.config.security.redact_secrets);
        let input_tokens = estimate_input_tokens(&body);
        let output_tokens = requested_output_tokens(&body);
        let org_spend = self
            .database
            .org_usage_today_usd(&context.org)
            .await
            .map_err(AppError::Internal)?;
        let repo_spend = self
            .database
            .repo_usage_today_usd(&context.org, &context.repo)
            .await
            .map_err(AppError::Internal)?;
        let org_limit = self.config.org_daily_limit(&context.org);
        let repo_limit = self.config.repo_daily_limit(&context.org, &context.repo);

        let candidates = self
            .config
            .model_candidates(&requested_model, &context.task_type);
        if candidates.is_empty() {
            return Err(AppError::BadRequest(
                "no model route is configured for this request".to_owned(),
            ));
        }

        let mut attempts = Vec::new();
        let mut seen_providers = HashSet::new();
        for (index, model_id) in candidates.iter().enumerate() {
            if index > 0 && !context.allow_downgrade {
                break;
            }
            let Some(model) = self.config.models.get(model_id) else {
                continue;
            };
            if !model.enabled {
                continue;
            }
            if !model.task_types.is_empty()
                && !model
                    .task_types
                    .iter()
                    .any(|task| task == &context.task_type || task == "*")
            {
                continue;
            }
            let Some(provider) = self.providers.get(&model.provider) else {
                attempts.push(RouteAttempt {
                    model: model_id.clone(),
                    provider: model.provider.clone(),
                    outcome: "skipped".to_owned(),
                    reason: Some("provider is unavailable or missing credentials".to_owned()),
                    estimated_cost_usd: 0.0,
                });
                continue;
            };
            seen_providers.insert(provider.name().to_owned());

            if !self.security_allows_route(&context, &secret_report, provider.trust()) {
                attempts.push(RouteAttempt {
                    model: model_id.clone(),
                    provider: model.provider.clone(),
                    outcome: "skipped".to_owned(),
                    reason: Some("security policy rejected this provider".to_owned()),
                    estimated_cost_usd: 0.0,
                });
                continue;
            }

            let estimated_cost = model.estimated_cost_usd(input_tokens, output_tokens);
            let budget_reason = budget_rejection_reason(
                estimated_cost,
                org_spend,
                org_limit,
                repo_spend,
                repo_limit,
                context.max_cost_usd,
            );
            if let Some(reason) = budget_reason {
                attempts.push(RouteAttempt {
                    model: model_id.clone(),
                    provider: model.provider.clone(),
                    outcome: "skipped".to_owned(),
                    reason: Some(reason),
                    estimated_cost_usd: estimated_cost,
                });
                continue;
            }

            let _permit = self
                .semaphore
                .clone()
                .acquire_owned()
                .await
                .map_err(|_| AppError::Internal(anyhow::anyhow!("model semaphore closed")))?;
            match provider
                .chat_completions(&model.upstream_model, &body)
                .await
            {
                Ok(mut response) => {
                    let (prompt_tokens, completion_tokens) =
                        response_usage(&response).unwrap_or((input_tokens, output_tokens));
                    let actual_cost = model.estimated_cost_usd(prompt_tokens, completion_tokens);
                    self.database
                        .record_usage(&UsageRecord {
                            request_id: context.request_id.clone(),
                            org: context.org.clone(),
                            repo: context.repo.clone(),
                            provider: provider.name().to_owned(),
                            model: model_id.clone(),
                            prompt_tokens,
                            completion_tokens,
                            cost_usd: actual_cost,
                        })
                        .await
                        .map_err(AppError::Internal)?;

                    attempts.push(RouteAttempt {
                        model: model_id.clone(),
                        provider: model.provider.clone(),
                        outcome: "selected".to_owned(),
                        reason: None,
                        estimated_cost_usd: estimated_cost,
                    });
                    attach_coordinator_metadata(
                        &mut response,
                        &context,
                        model_id,
                        provider.name(),
                        actual_cost,
                        &secret_report,
                        &attempts,
                    );
                    info!(
                        request_id = %context.request_id,
                        org = %context.org,
                        repo = %context.repo,
                        model = %model_id,
                        provider = %provider.name(),
                        cost_usd = actual_cost,
                        "model request completed"
                    );
                    return Ok(response);
                }
                Err(error) => {
                    warn!(
                        request_id = %context.request_id,
                        model = %model_id,
                        provider = %provider.name(),
                        error = %error.safe_summary(),
                        "model route failed"
                    );
                    attempts.push(RouteAttempt {
                        model: model_id.clone(),
                        provider: model.provider.clone(),
                        outcome: "failed".to_owned(),
                        reason: Some(error.safe_summary()),
                        estimated_cost_usd: estimated_cost,
                    });
                }
            }
        }

        if attempts.iter().any(|attempt| {
            attempt
                .reason
                .as_deref()
                .is_some_and(|reason| reason.contains("budget"))
        }) {
            return Err(AppError::BudgetExceeded(format!(
                "no configured route fit the remaining org/repository/request budget; attempts={}",
                serde_json::to_string(&attempts).unwrap_or_default()
            )));
        }

        Err(AppError::Upstream(format!(
            "no usable route among providers {:?}; attempts={}",
            seen_providers,
            serde_json::to_string(&attempts).unwrap_or_default()
        )))
    }

    pub fn models_response(&self) -> Value {
        let mut models = self
            .config
            .models
            .iter()
            .map(|(id, model)| {
                json!({
                    "id": id,
                    "object": "model",
                    "owned_by": "ai-agent-coordinator",
                    "provider": model.provider,
                    "tier": model.tier,
                    "enabled": model.enabled && self.providers.get(&model.provider).is_some(),
                    "task_types": model.task_types,
                })
            })
            .collect::<Vec<_>>();
        models.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
        json!({
            "object": "list",
            "data": models,
            "provider_status": self.providers.available_names(),
        })
    }

    fn security_allows_route(
        &self,
        context: &RequestContext,
        secret_report: &SecretScanReport,
        trust: ProviderTrust,
    ) -> bool {
        if context.sensitivity == Sensitivity::Restricted
            && self.config.security.restricted_requires_local
            && !trust.is_local()
        {
            return false;
        }

        if context.sensitivity == Sensitivity::Confidential && trust == ProviderTrust::Public {
            return false;
        }

        if secret_report.matches > 0
            && !self.config.security.redact_secrets
            && self
                .config
                .security
                .deny_remote_when_secrets_cannot_be_redacted
            && !trust.is_local()
        {
            return false;
        }

        true
    }
}

fn parse_bool(value: &str) -> Result<bool, AppError> {
    match value {
        "true" | "1" | "yes" => Ok(true),
        "false" | "0" | "no" => Ok(false),
        _ => Err(AppError::BadRequest(format!(
            "invalid boolean value {value:?}"
        ))),
    }
}

fn estimate_input_tokens(body: &Value) -> u64 {
    let bytes = body
        .get("messages")
        .map(|messages| messages.to_string().len())
        .unwrap_or_else(|| body.to_string().len());
    ((bytes as u64) / 4).max(1)
}

fn requested_output_tokens(body: &Value) -> u64 {
    body.get("max_completion_tokens")
        .or_else(|| body.get("max_tokens"))
        .and_then(Value::as_u64)
        .unwrap_or(1_024)
}

fn response_usage(response: &Value) -> Option<(u64, u64)> {
    let usage = response.get("usage")?;
    let prompt = usage
        .get("prompt_tokens")
        .or_else(|| usage.get("input_tokens"))?
        .as_u64()?;
    let completion = usage
        .get("completion_tokens")
        .or_else(|| usage.get("output_tokens"))?
        .as_u64()?;
    Some((prompt, completion))
}

fn budget_rejection_reason(
    estimated_cost: f64,
    org_spend: f64,
    org_limit: f64,
    repo_spend: f64,
    repo_limit: f64,
    request_limit: Option<f64>,
) -> Option<String> {
    if estimated_cost > (org_limit - org_spend).max(0.0) {
        return Some(format!(
            "org budget would be exceeded (${org_spend:.4} spent of ${org_limit:.4})"
        ));
    }
    if estimated_cost > (repo_limit - repo_spend).max(0.0) {
        return Some(format!(
            "repository budget would be exceeded (${repo_spend:.4} spent of ${repo_limit:.4})"
        ));
    }
    if request_limit.is_some_and(|limit| estimated_cost > limit) {
        return Some(format!(
            "request budget would be exceeded (estimated ${estimated_cost:.6})"
        ));
    }
    None
}

fn attach_coordinator_metadata(
    response: &mut Value,
    context: &RequestContext,
    model: &str,
    provider: &str,
    cost_usd: f64,
    secret_report: &SecretScanReport,
    attempts: &[RouteAttempt],
) {
    if let Some(object) = response.as_object_mut() {
        object.insert(
            "coordinator".to_owned(),
            json!({
                "request_id": context.request_id,
                "org": context.org,
                "repo": context.repo,
                "task_type": context.task_type,
                "selected_model": model,
                "selected_provider": provider,
                "estimated_cost_usd": cost_usd,
                "secret_scan": secret_report,
                "attempts": attempts,
            }),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::budget_rejection_reason;

    #[test]
    fn rejects_route_that_exceeds_repo_budget() {
        let result = budget_rejection_reason(0.5, 1.0, 10.0, 0.8, 1.0, None);
        assert!(result.unwrap().contains("repository budget"));
    }
}
