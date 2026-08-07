#![forbid(unsafe_code)]

mod generated;

pub use generated::{
    IdempotencyPolicy, OperationId, OperationMetadata, RetryClass, ALL_OPERATIONS,
    CONTRACT_DIGEST,
};

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

pub const DEFAULT_TIMEOUT_MS: u64 = 30_000;
pub const MAX_ATTEMPTS: u32 = 3;
pub const MAX_ERROR_BODY_BYTES: usize = 65_536;
pub const REDACTED: &str = "[REDACTED]";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RequestPlan {
    pub operation: OperationId,
    pub method: &'static str,
    pub path: String,
    pub content_type: Option<&'static str>,
    pub timeout_ms: u64,
    pub retry_class: RetryClass,
    pub auth_required: bool,
    pub request_id_required: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub struct SyncCursor {
    pub epoch: u64,
    pub sequence: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ClientPlanError {
    MissingPathParameter(String),
    UnexpectedPathParameter(String),
    MissingIdempotencyKey,
    ForbiddenIdempotencyKey,
    InvalidIdempotencyKey,
    InvalidQueryValue(String),
    CursorMovedBackwards,
}

impl fmt::Display for ClientPlanError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingPathParameter(name) => write!(formatter, "missing path parameter: {name}"),
            Self::UnexpectedPathParameter(name) => {
                write!(formatter, "unexpected path parameter: {name}")
            }
            Self::MissingIdempotencyKey => formatter.write_str("Idempotency-Key is required"),
            Self::ForbiddenIdempotencyKey => formatter.write_str("Idempotency-Key is forbidden"),
            Self::InvalidIdempotencyKey => formatter.write_str("Idempotency-Key is invalid"),
            Self::InvalidQueryValue(name) => write!(formatter, "invalid query value: {name}"),
            Self::CursorMovedBackwards => formatter.write_str("sync cursor moved backwards"),
        }
    }
}

impl std::error::Error for ClientPlanError {}

pub trait AccessTokenProvider {
    type Error;

    fn access_token(&self) -> Result<String, Self::Error>;
    fn refresh_access_token(&self) -> Result<String, Self::Error>;
}

pub trait Transport {
    type Error;
    type Response;

    fn execute(
        &self,
        plan: &RequestPlan,
        headers: &BTreeMap<String, String>,
        body: Option<&[u8]>,
    ) -> Result<Self::Response, Self::Error>;
}

pub fn build_request_plan(
    operation: OperationId,
    path_parameters: &BTreeMap<String, String>,
    query: &BTreeMap<String, String>,
    headers: &BTreeMap<String, String>,
) -> Result<RequestPlan, ClientPlanError> {
    let metadata = operation.metadata();
    let required = path_parameter_names(metadata.path_template);
    for name in &required {
        if !path_parameters.contains_key(name) {
            return Err(ClientPlanError::MissingPathParameter(name.clone()));
        }
    }
    for name in path_parameters.keys() {
        if !required.contains(name) {
            return Err(ClientPlanError::UnexpectedPathParameter(name.clone()));
        }
    }

    let idempotency_key = header_value(headers, "idempotency-key");
    match metadata.idempotency {
        IdempotencyPolicy::Required if idempotency_key.is_none() => {
            return Err(ClientPlanError::MissingIdempotencyKey)
        }
        IdempotencyPolicy::Forbidden if idempotency_key.is_some() => {
            return Err(ClientPlanError::ForbiddenIdempotencyKey)
        }
        _ => {}
    }
    if let Some(value) = idempotency_key {
        if !valid_idempotency_key(value) {
            return Err(ClientPlanError::InvalidIdempotencyKey);
        }
    }

    let mut path = metadata.path_template.to_owned();
    for (name, value) in path_parameters {
        path = path.replace(&format!("{{{name}}}"), &percent_encode(value));
    }
    if !query.is_empty() {
        path.push('?');
        let mut first = true;
        for (name, value) in query {
            if name.is_empty() {
                return Err(ClientPlanError::InvalidQueryValue(name.clone()));
            }
            if !first {
                path.push('&');
            }
            first = false;
            path.push_str(&percent_encode(name));
            path.push('=');
            path.push_str(&percent_encode(value));
        }
    }

    Ok(RequestPlan {
        operation,
        method: metadata.method,
        path,
        content_type: metadata.content_type,
        timeout_ms: DEFAULT_TIMEOUT_MS,
        retry_class: metadata.retry_class,
        auth_required: true,
        request_id_required: true,
    })
}

pub fn should_retry(
    retry_class: RetryClass,
    status: u16,
    attempt: u32,
    response_body_started: bool,
) -> bool {
    if retry_class == RetryClass::Never || attempt >= MAX_ATTEMPTS || response_body_started {
        return false;
    }
    matches!(status, 408 | 425 | 429 | 500 | 502 | 503 | 504)
}

pub fn retry_delay_upper_bound_ms(attempt: u32) -> Option<u64> {
    if attempt == 0 || attempt >= MAX_ATTEMPTS {
        return None;
    }
    Some((100_u64.saturating_mul(1_u64 << (attempt - 1))).min(2_000))
}

pub fn redact_headers(headers: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    let redacted: BTreeSet<&str> = [
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-provider-token",
    ]
    .into_iter()
    .collect();
    headers
        .iter()
        .map(|(name, value)| {
            let output = if redacted.contains(name.to_ascii_lowercase().as_str()) {
                REDACTED.to_owned()
            } else {
                value.clone()
            };
            (name.clone(), output)
        })
        .collect()
}

pub fn advance_sync_cursor(
    current: SyncCursor,
    through_cursor: SyncCursor,
    _has_more: bool,
) -> Result<SyncCursor, ClientPlanError> {
    if through_cursor < current {
        return Err(ClientPlanError::CursorMovedBackwards);
    }
    Ok(through_cursor)
}

fn path_parameter_names(template: &str) -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    let mut remaining = template;
    while let Some(start) = remaining.find('{') {
        let after = &remaining[start + 1..];
        let Some(end) = after.find('}') else {
            break;
        };
        names.insert(after[..end].to_owned());
        remaining = &after[end + 1..];
    }
    names
}

fn header_value<'a>(headers: &'a BTreeMap<String, String>, wanted: &str) -> Option<&'a str> {
    headers
        .iter()
        .find(|(name, _)| name.eq_ignore_ascii_case(wanted))
        .map(|(_, value)| value.as_str())
}

fn valid_idempotency_key(value: &str) -> bool {
    (16..=200).contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._:-".contains(&byte))
}

fn percent_encode(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            output.push(char::from(byte));
        } else {
            output.push_str(&format!("%{byte:02X}"));
        }
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn import_requires_valid_idempotency_key() {
        let missing = build_request_plan(
            OperationId::CreateImport,
            &BTreeMap::new(),
            &BTreeMap::new(),
            &BTreeMap::new(),
        );
        assert_eq!(missing, Err(ClientPlanError::MissingIdempotencyKey));

        let headers = BTreeMap::from([(
            "Idempotency-Key".to_owned(),
            "import-20260731-0001".to_owned(),
        )]);
        let plan = build_request_plan(
            OperationId::CreateImport,
            &BTreeMap::new(),
            &BTreeMap::new(),
            &headers,
        )
        .expect("valid plan");
        assert_eq!(plan.method, "POST");
        assert_eq!(plan.path, "/v1/imports");
        assert_eq!(plan.retry_class, RetryClass::IdempotentWrite);
    }

    #[test]
    fn asset_path_is_encoded_and_get_forbids_idempotency() {
        let path = BTreeMap::from([("asset_id".to_owned(), "a/b".to_owned())]);
        let plan = build_request_plan(
            OperationId::GetAsset,
            &path,
            &BTreeMap::new(),
            &BTreeMap::new(),
        )
        .expect("valid plan");
        assert_eq!(plan.path, "/v1/assets/a%2Fb");

        let headers = BTreeMap::from([(
            "Idempotency-Key".to_owned(),
            "unexpected-20260731".to_owned(),
        )]);
        assert_eq!(
            build_request_plan(OperationId::GetAsset, &path, &BTreeMap::new(), &headers),
            Err(ClientPlanError::ForbiddenIdempotencyKey)
        );
    }

    #[test]
    fn query_is_deterministic() {
        let query = BTreeMap::from([
            ("sequence".to_owned(), "88".to_owned()),
            ("epoch".to_owned(), "4".to_owned()),
            ("limit".to_owned(), "250".to_owned()),
        ]);
        let plan = build_request_plan(
            OperationId::ListSyncEvents,
            &BTreeMap::new(),
            &query,
            &BTreeMap::new(),
        )
        .expect("valid plan");
        assert_eq!(plan.path, "/v1/sync/events?epoch=4&limit=250&sequence=88");
    }

    #[test]
    fn retries_are_bounded_and_stop_after_body_start() {
        assert!(should_retry(RetryClass::SafeRead, 503, 1, false));
        assert!(!should_retry(RetryClass::SafeRead, 503, 3, false));
        assert!(!should_retry(RetryClass::SafeRead, 503, 1, true));
        assert!(!should_retry(RetryClass::Never, 503, 1, false));
        assert_eq!(retry_delay_upper_bound_ms(1), Some(100));
        assert_eq!(retry_delay_upper_bound_ms(2), Some(200));
        assert_eq!(retry_delay_upper_bound_ms(3), None);
    }

    #[test]
    fn sensitive_headers_are_redacted_case_insensitively() {
        let input = BTreeMap::from([
            ("Authorization".to_owned(), "Bearer secret".to_owned()),
            ("X-Request-Id".to_owned(), "req-1".to_owned()),
        ]);
        let output = redact_headers(&input);
        assert_eq!(output["Authorization"], REDACTED);
        assert_eq!(output["X-Request-Id"], "req-1");
    }

    #[test]
    fn sync_cursor_advances_on_final_page_and_never_moves_backwards() {
        let current = SyncCursor {
            epoch: 4,
            sequence: 88,
        };
        let next = SyncCursor {
            epoch: 4,
            sequence: 95,
        };
        assert_eq!(advance_sync_cursor(current, next, false), Ok(next));
        assert_eq!(
            advance_sync_cursor(next, current, true),
            Err(ClientPlanError::CursorMovedBackwards)
        );
    }
}
