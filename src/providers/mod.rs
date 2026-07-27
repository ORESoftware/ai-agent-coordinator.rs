use std::{collections::HashMap, env, sync::Arc, time::Duration};

use anyhow::{Context, Result};
use reqwest::{header::HeaderMap, Client};
use serde_json::Value;
use thiserror::Error;
use tracing::warn;

use crate::config::{Config, ProviderConfig, ProviderKind, ProviderTrust};

#[derive(Clone)]
pub struct ProviderRegistry {
    providers: HashMap<String, Arc<OpenAiCompatibleProvider>>,
}

impl ProviderRegistry {
    pub fn from_config(config: &Config) -> Result<Self> {
        let mut providers = HashMap::new();
        for (name, provider_config) in &config.providers {
            match provider_config.kind {
                ProviderKind::OpenaiCompatible => {
                    match OpenAiCompatibleProvider::new(name, provider_config) {
                        Ok(provider) => {
                            providers.insert(name.clone(), Arc::new(provider));
                        }
                        Err(error) => {
                            warn!(provider = %name, error = %error, "provider is disabled");
                        }
                    }
                }
            }
        }
        Ok(Self { providers })
    }

    pub fn get(&self, name: &str) -> Option<Arc<OpenAiCompatibleProvider>> {
        self.providers.get(name).cloned()
    }

    pub fn available_names(&self) -> Vec<String> {
        let mut names: Vec<_> = self.providers.keys().cloned().collect();
        names.sort();
        names
    }
}

pub struct OpenAiCompatibleProvider {
    name: String,
    base_url: String,
    api_key: Option<String>,
    trust: ProviderTrust,
    extra_headers: HeaderMap,
    client: Client,
}

impl OpenAiCompatibleProvider {
    fn new(name: &str, config: &ProviderConfig) -> Result<Self> {
        let api_key = match &config.api_key_env {
            Some(variable) => Some(
                env::var(variable)
                    .with_context(|| format!("environment variable {variable} is not set"))?,
            ),
            None => None,
        };

        let mut extra_headers = HeaderMap::new();
        for (key, value) in &config.extra_headers {
            let header_name: reqwest::header::HeaderName = key
                .parse()
                .with_context(|| format!("invalid header name {key:?}"))?;
            let header_value: reqwest::header::HeaderValue = value
                .parse()
                .with_context(|| format!("invalid value for header {key:?}"))?;
            extra_headers.insert(header_name, header_value);
        }

        let client = Client::builder()
            .timeout(Duration::from_secs(config.timeout_seconds))
            .build()
            .with_context(|| format!("failed to build HTTP client for provider {name}"))?;

        Ok(Self {
            name: name.to_owned(),
            base_url: config.base_url.trim_end_matches('/').to_owned(),
            api_key,
            trust: config.trust,
            extra_headers,
            client,
        })
    }

    pub fn trust(&self) -> ProviderTrust {
        self.trust
    }

    pub async fn chat_completions(
        &self,
        upstream_model: &str,
        body: &Value,
    ) -> Result<Value, ProviderError> {
        let mut forwarded = body.clone();
        let object = forwarded.as_object_mut().ok_or_else(|| {
            ProviderError::InvalidResponse("request body is not an object".into())
        })?;
        object.insert("model".to_owned(), Value::String(upstream_model.to_owned()));
        object.remove("coordinator");

        let url = format!("{}/chat/completions", self.base_url);
        let mut request = self
            .client
            .post(url)
            .headers(self.extra_headers.clone())
            .json(&forwarded);
        if let Some(api_key) = &self.api_key {
            request = request.bearer_auth(api_key);
        }

        let response = request
            .send()
            .await
            .map_err(|error| ProviderError::Transport(error.to_string()))?;
        let status = response.status();
        let response_text = response
            .text()
            .await
            .map_err(|error| ProviderError::Transport(error.to_string()))?;

        if !status.is_success() {
            return Err(ProviderError::Http {
                status: status.as_u16(),
                body: truncate(&response_text, 2_000),
            });
        }

        serde_json::from_str(&response_text)
            .map_err(|error| ProviderError::InvalidResponse(error.to_string()))
    }

    pub fn name(&self) -> &str {
        &self.name
    }
}

#[derive(Debug, Error)]
pub enum ProviderError {
    #[error("transport failure: {0}")]
    Transport(String),
    #[error("provider returned HTTP {status}: {body}")]
    Http { status: u16, body: String },
    #[error("invalid provider response: {0}")]
    InvalidResponse(String),
}

impl ProviderError {
    pub fn safe_summary(&self) -> String {
        match self {
            Self::Transport(_) => "transport failure".to_owned(),
            Self::Http { status, .. } => format!("HTTP {status}"),
            Self::InvalidResponse(_) => "invalid JSON response".to_owned(),
        }
    }
}

fn truncate(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}
