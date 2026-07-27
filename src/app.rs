use std::sync::Arc;

use axum::{
    body::Bytes,
    extract::{DefaultBodyLimit, Path, State},
    http::{HeaderMap, HeaderName, HeaderValue, StatusCode},
    middleware,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};
use subtle::ConstantTimeEq;
use tower_http::{
    request_id::{MakeRequestUuid, PropagateRequestIdLayer, SetRequestIdLayer},
    trace::TraceLayer,
};

use crate::{
    config::Config,
    db::Database,
    error::AppError,
    gateway::ModelGateway,
    jobs::{ClaimJobRequest, CompleteJobRequest, CreateJobRequest, HeartbeatJobRequest},
    providers::ProviderRegistry,
    security::SecretScanner,
    webhooks,
};

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    pub database: Database,
    pub gateway: ModelGateway,
    api_token: Option<String>,
    github_webhook_secret: Option<String>,
}

impl AppState {
    pub fn new(config: Config) -> anyhow::Result<Self> {
        let config = Arc::new(config);
        let database = Database::open(&config.database.path)?;
        let providers = ProviderRegistry::from_config(&config)?;
        let scanner = SecretScanner::new()?;
        let gateway = ModelGateway::new(
            config.clone(),
            database.clone(),
            providers,
            scanner,
        );
        let api_token = config.api_token();
        let github_webhook_secret = config.github_webhook_secret();
        Ok(Self {
            config,
            database,
            gateway,
            api_token,
            github_webhook_secret,
        })
    }

    fn authorize(&self, headers: &HeaderMap) -> Result<(), AppError> {
        if !self.config.auth.required {
            return Ok(());
        }
        let expected = self.api_token.as_deref().ok_or(AppError::Unauthorized)?;
        let supplied = headers
            .get("authorization")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.strip_prefix("Bearer "))
            .ok_or(AppError::Unauthorized)?;
        if bool::from(expected.as_bytes().ct_eq(supplied.as_bytes())) {
            Ok(())
        } else {
            Err(AppError::Unauthorized)
        }
    }
}

pub fn router(state: AppState) -> Router {
    let request_id_header = HeaderName::from_static("x-request-id");
    let max_request_bytes = state.config.security.max_request_bytes;

    Router::new()
        .route("/healthz", get(health))
        .route("/readyz", get(ready))
        .route("/v1/models", get(list_models))
        .route("/v1/chat/completions", post(chat_completions))
        .route("/v1/jobs", post(create_job))
        .route("/v1/jobs/claim", post(claim_job))
        .route("/v1/jobs/:id", get(get_job))
        .route("/v1/jobs/:id/heartbeat", post(heartbeat_job))
        .route("/v1/jobs/:id/complete", post(complete_job))
        .route("/v1/jobs/:id/cancel", post(cancel_job))
        .route("/webhooks/github", post(github_webhook))
        .layer(DefaultBodyLimit::max(max_request_bytes))
        .layer(PropagateRequestIdLayer::new(request_id_header.clone()))
        .layer(SetRequestIdLayer::new(
            request_id_header,
            MakeRequestUuid,
        ))
        .layer(TraceLayer::new_for_http())
        .layer(middleware::from_fn(add_no_store))
        .with_state(state)
}

async fn add_no_store(
    request: axum::http::Request<axum::body::Body>,
    next: middleware::Next,
) -> impl IntoResponse {
    let mut response = next.run(request).await;
    response
        .headers_mut()
        .insert("cache-control", HeaderValue::from_static("no-store"));
    response
}

async fn health() -> Json<Value> {
    Json(json!({"status": "ok"}))
}

async fn ready(State(state): State<AppState>) -> Result<Json<Value>, AppError> {
    state.database.ready().map_err(AppError::Internal)?;
    Ok(Json(json!({"status": "ready"})))
}

async fn list_models(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, AppError> {
    state.authorize(&headers)?;
    Ok(Json(state.gateway.models_response()))
}

async fn chat_completions(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, AppError> {
    state.authorize(&headers)?;
    let context = state.gateway.context_from_headers(&headers)?;
    let response = state.gateway.chat_completions(context, body).await?;
    Ok(Json(response))
}

async fn create_job(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(request): Json<CreateJobRequest>,
) -> Result<(StatusCode, Json<Value>), AppError> {
    state.authorize(&headers)?;
    request.validate().map_err(AppError::BadRequest)?;
    let idempotency_key = headers
        .get("idempotency-key")
        .and_then(|value| value.to_str().ok());
    let job = state
        .database
        .create_job(&request, idempotency_key)
        .map_err(AppError::Internal)?;
    Ok((StatusCode::ACCEPTED, Json(json!({"job": job}))))
}

async fn claim_job(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(request): Json<ClaimJobRequest>,
) -> Result<impl IntoResponse, AppError> {
    state.authorize(&headers)?;
    request.validate().map_err(AppError::BadRequest)?;
    match state
        .database
        .claim_job(&request, &state.config.workers)
        .map_err(AppError::Internal)?
    {
        Some(job) => Ok((StatusCode::OK, Json(json!({"job": job})))),
        None => Ok((StatusCode::NO_CONTENT, Json(Value::Null))),
    }
}

async fn get_job(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Value>, AppError> {
    state.authorize(&headers)?;
    let job = state
        .database
        .get_job(&id)
        .map_err(AppError::Internal)?
        .ok_or_else(|| AppError::NotFound(format!("job {id}")))?;
    Ok(Json(json!({"job": job})))
}

async fn heartbeat_job(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Json(request): Json<HeartbeatJobRequest>,
) -> Result<Json<Value>, AppError> {
    state.authorize(&headers)?;
    let job = state
        .database
        .heartbeat_job(&id, &request.worker_id, request.lease_seconds)
        .map_err(|error| AppError::BadRequest(error.to_string()))?;
    Ok(Json(json!({"job": job})))
}

async fn complete_job(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Json(request): Json<CompleteJobRequest>,
) -> Result<Json<Value>, AppError> {
    state.authorize(&headers)?;
    let job = state
        .database
        .complete_job(&id, &request)
        .map_err(|error| AppError::BadRequest(error.to_string()))?;
    Ok(Json(json!({"job": job})))
}

async fn cancel_job(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Value>, AppError> {
    state.authorize(&headers)?;
    let job = state
        .database
        .cancel_job(&id)
        .map_err(|error| AppError::BadRequest(error.to_string()))?;
    Ok(Json(json!({"job": job})))
}

async fn github_webhook(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Json<Value>, AppError> {
    webhooks::verify_signature(
        &headers,
        &body,
        state.github_webhook_secret.as_deref(),
    )?;
    let response = webhooks::enqueue_from_github(
        &state.database,
        &headers,
        body,
        &state.config.github.issue_trigger_labels,
        &state.config.github.review_trigger_labels,
        state.config.github.auto_enqueue_failed_workflows,
    )?;
    Ok(Json(response))
}
