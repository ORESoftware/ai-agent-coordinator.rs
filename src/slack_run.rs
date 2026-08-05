use std::collections::{BTreeMap, BTreeSet};

use serde::Deserialize;
use serde_json::Value;

pub const TASK_TYPE: &str = "slack_agent_run";

const SCHEMA_VERSION: u32 = 1;
const MAX_IDENTIFIER_BYTES: usize = 255;
const MAX_PROMPT_BYTES: usize = 100_000;
const MAX_CONTEXT_MESSAGES: usize = 20;
const MAX_CONTEXT_MESSAGE_BYTES: usize = 4_000;
const MAX_CONTEXT_TOTAL_BYTES: usize = 32_000;
const MAX_OBSERVABLE_EVENT_BYTES: usize = 65_536;
const MAX_OBSERVABLE_PAYLOAD_BYTES: usize = 32_768;
const MAX_OBSERVABLE_METADATA_ENTRIES: usize = 32;
const MAX_OBSERVABLE_METADATA_VALUE_BYTES: usize = 512;
const EXPECTED_BROADCAST_TARGETS: [&str; 5] = [
    "ai_agent_bridge_workflow",
    "ai_agent_coordinator_job",
    "github_branch_pr_checks",
    "linear_run_queue",
    "slack_run_thread",
];

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SlackAgentRunPayload {
    pub schema_version: u32,
    pub run_id: String,
    pub bridge_workflow_id: String,
    pub provider: Provider,
    pub action: String,
    pub prompt: String,
    pub origin: Origin,
    pub context: ContextEnvelope,
    pub routing: Routing,
    #[serde(default)]
    pub observable_event: Option<Value>,
    pub broadcast_targets: Vec<String>,
}

impl SlackAgentRunPayload {
    pub fn from_value(value: &Value) -> Result<Self, String> {
        let payload = serde_json::from_value::<Self>(value.clone())
            .map_err(|_| "slack_agent_run payload does not match schema v1".to_owned())?;
        payload.validate()?;
        Ok(payload)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != SCHEMA_VERSION {
            return Err("slack_agent_run schema_version must be 1".to_owned());
        }
        if !valid_run_id(&self.run_id) {
            return Err("slack_agent_run run_id is invalid".to_owned());
        }
        validate_identifier("bridge_workflow_id", &self.bridge_workflow_id)?;
        if !matches!(
            self.action.as_str(),
            "implement" | "investigate" | "review" | "plan" | "triage"
        ) {
            return Err("slack_agent_run action is unsupported".to_owned());
        }
        if self.prompt.trim().is_empty()
            || self.prompt.len() > MAX_PROMPT_BYTES
            || self.prompt.contains('\0')
        {
            return Err("slack_agent_run prompt is invalid".to_owned());
        }
        self.origin.validate()?;
        self.context.validate()?;
        self.routing.validate()?;
        if let Some(event) = &self.observable_event {
            validate_observable_event(event, self)?;
        }
        validate_broadcast_targets(&self.broadcast_targets)
    }
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Provider {
    Claude,
    Chatgpt,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Origin {
    pub workspace_id: String,
    pub channel_id: String,
    pub requester_user_id: String,
}

impl Origin {
    fn validate(&self) -> Result<(), String> {
        validate_identifier("origin.workspace_id", &self.workspace_id)?;
        validate_identifier("origin.channel_id", &self.channel_id)?;
        validate_identifier("origin.requester_user_id", &self.requester_user_id)
    }
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ContextEnvelope {
    pub trust: String,
    pub selection: String,
    pub messages: Vec<ContextMessage>,
}

impl ContextEnvelope {
    fn validate(&self) -> Result<(), String> {
        if self.trust != "untrusted_channel_context" {
            return Err("slack_agent_run context trust marker is invalid".to_owned());
        }
        if self.selection != "latest_non_bot_channel_messages" {
            return Err("slack_agent_run context selection is invalid".to_owned());
        }
        if self.messages.len() > MAX_CONTEXT_MESSAGES {
            return Err("slack_agent_run contains too many context messages".to_owned());
        }
        let mut total_bytes = 0usize;
        let mut previous_timestamp: Option<&str> = None;
        for message in &self.messages {
            message.validate()?;
            total_bytes = total_bytes.saturating_add(message.text.len());
            if total_bytes > MAX_CONTEXT_TOTAL_BYTES {
                return Err("slack_agent_run context exceeds total byte limit".to_owned());
            }
            if previous_timestamp.is_some_and(|previous| previous > message.ts.as_str()) {
                return Err("slack_agent_run context must be chronological".to_owned());
            }
            previous_timestamp = Some(&message.ts);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ContextMessage {
    pub user_id: Option<String>,
    pub ts: String,
    pub text: String,
}

impl ContextMessage {
    fn validate(&self) -> Result<(), String> {
        if let Some(user_id) = &self.user_id {
            validate_identifier("context.messages.user_id", user_id)?;
        }
        if !valid_slack_timestamp(&self.ts) {
            return Err("slack_agent_run context timestamp is invalid".to_owned());
        }
        if self.text.trim().is_empty()
            || self.text.len() > MAX_CONTEXT_MESSAGE_BYTES
            || self.text.contains('\0')
        {
            return Err("slack_agent_run context message is invalid".to_owned());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Routing {
    pub repository: String,
    pub linear_team_id: String,
    pub linear_project_id: String,
    pub linear_run_project_id: String,
    pub linear_issue: Option<String>,
    pub write_policy: WritePolicy,
}

impl Routing {
    fn validate(&self) -> Result<(), String> {
        validate_repository(&self.repository)?;
        validate_identifier("routing.linear_team_id", &self.linear_team_id)?;
        validate_identifier("routing.linear_project_id", &self.linear_project_id)?;
        validate_identifier("routing.linear_run_project_id", &self.linear_run_project_id)?;
        if self
            .linear_issue
            .as_deref()
            .is_some_and(|identifier| !valid_issue_identifier(identifier))
        {
            return Err("slack_agent_run Linear issue identifier is invalid".to_owned());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum WritePolicy {
    ReadOnly,
    LinearOnly,
    DraftPullRequest,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ObservableEvent {
    schema_version: String,
    event_id: String,
    idempotency_key: String,
    occurred_at: String,
    source: ObservableSource,
    correlation: ObservableCorrelation,
    kind: String,
    payload_classification: String,
    redaction_state: String,
    evidence_references: Vec<Value>,
    delivery: ObservableDelivery,
    payload: ObservablePayload,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ObservableSource {
    agent_id: String,
    provider: String,
    model: String,
    instance_id: Option<String>,
    metadata: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ObservableCorrelation {
    correlation_id: String,
    causation_id: Option<String>,
    parent_event_id: Option<String>,
    server_id: Option<String>,
    session_id: Option<String>,
    run_id: Option<String>,
    goal_id: Option<String>,
    task_id: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ObservableDelivery {
    transport: String,
    delivery_id: String,
    attempt: u64,
    ack_requested: bool,
    sequence: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ObservablePayload {
    repository: String,
    linear_project_id: String,
    linear_run_project_id: String,
    linear_issue: Option<String>,
    bridge_workflow_id: String,
    requested_capability: String,
}

fn validate_observable_event(event: &Value, run: &SlackAgentRunPayload) -> Result<(), String> {
    let encoded = serde_json::to_vec(event)
        .map_err(|_| "slack_agent_run observable_event is not serializable".to_owned())?;
    if encoded.len() > MAX_OBSERVABLE_EVENT_BYTES {
        return Err("slack_agent_run observable_event exceeds byte limit".to_owned());
    }
    let payload_size = serde_json::to_vec(
        event
            .get("payload")
            .ok_or_else(|| "slack_agent_run observable_event payload is missing".to_owned())?,
    )
    .map_err(|_| "slack_agent_run observable_event payload is not serializable".to_owned())?
    .len();
    if payload_size > MAX_OBSERVABLE_PAYLOAD_BYTES {
        return Err("slack_agent_run observable_event payload exceeds byte limit".to_owned());
    }
    reject_sensitive_event_keys(event)?;

    let event = serde_json::from_value::<ObservableEvent>(event.clone())
        .map_err(|_| "slack_agent_run observable_event does not match schema v1".to_owned())?;

    if event.schema_version != "1.0"
        || event.kind != "task_created"
        || event.payload_classification != "internal"
        || event.redaction_state != "sanitized"
    {
        return Err("slack_agent_run observable_event policy is invalid".to_owned());
    }
    if !valid_uuid(&event.event_id)
        || !valid_uuid(&event.correlation.correlation_id)
        || event
            .correlation
            .causation_id
            .as_deref()
            .is_some_and(|value| !valid_uuid(value))
        || event
            .correlation
            .parent_event_id
            .as_deref()
            .is_some_and(|value| !valid_uuid(value))
    {
        return Err("slack_agent_run observable_event UUID is invalid".to_owned());
    }
    if chrono::DateTime::parse_from_rfc3339(&event.occurred_at).is_err() {
        return Err("slack_agent_run observable_event occurred_at is invalid".to_owned());
    }
    if event.idempotency_key != format!("slack-task-created:{}", run.run_id) {
        return Err("slack_agent_run observable_event idempotency_key is invalid".to_owned());
    }

    let expected_provider = match run.provider {
        Provider::Claude => "anthropic",
        Provider::Chatgpt => "openai",
    };
    validate_identifier("observable_event.source.agent_id", &event.source.agent_id)?;
    validate_identifier("observable_event.source.model", &event.source.model)?;
    if let Some(instance_id) = &event.source.instance_id {
        validate_identifier("observable_event.source.instance_id", instance_id)?;
    }
    if event.source.agent_id != event.source.model || event.source.provider != expected_provider {
        return Err("slack_agent_run observable_event source is inconsistent".to_owned());
    }
    if event.source.metadata.len() > MAX_OBSERVABLE_METADATA_ENTRIES
        || event.source.metadata.iter().any(|(key, value)| {
            validate_identifier("observable_event.source.metadata key", key).is_err()
                || value.len() > MAX_OBSERVABLE_METADATA_VALUE_BYTES
                || value.contains('\0')
        })
        || event.source.metadata.get("surface").map(String::as_str) != Some("slack_slash_command")
        || event.source.metadata.get("action").map(String::as_str) != Some(run.action.as_str())
        || event
            .source
            .metadata
            .get("write_policy")
            .map(String::as_str)
            != Some(write_policy_name(run.routing.write_policy))
    {
        return Err("slack_agent_run observable_event source metadata is invalid".to_owned());
    }

    for (field, value) in [
        ("server_id", event.correlation.server_id.as_deref()),
        ("session_id", event.correlation.session_id.as_deref()),
        ("run_id", event.correlation.run_id.as_deref()),
        ("goal_id", event.correlation.goal_id.as_deref()),
        ("task_id", event.correlation.task_id.as_deref()),
    ] {
        if let Some(value) = value {
            validate_identifier(&format!("observable_event.correlation.{field}"), value)?;
        }
    }
    if event.correlation.server_id.as_deref() != Some("ai-agent-coordinator")
        || event.correlation.session_id.as_deref() != Some(run.run_id.as_str())
        || event.correlation.run_id.as_deref() != Some(run.run_id.as_str())
        || event.correlation.task_id.as_deref() != Some(run.run_id.as_str())
    {
        return Err("slack_agent_run observable_event correlation is inconsistent".to_owned());
    }

    let _sequence = event.delivery.sequence;
    if !event.evidence_references.is_empty()
        || event.delivery.transport != "http"
        || event.delivery.delivery_id != format!("coordinator-job:{}", run.run_id)
        || event.delivery.attempt != 1
        || !event.delivery.ack_requested
    {
        return Err("slack_agent_run observable_event delivery is invalid".to_owned());
    }

    if event.payload.repository != run.routing.repository
        || event.payload.linear_project_id != run.routing.linear_project_id
        || event.payload.linear_run_project_id != run.routing.linear_run_project_id
        || event.payload.linear_issue != run.routing.linear_issue
        || event.payload.bridge_workflow_id != run.bridge_workflow_id
        || !matches!(
            event.payload.requested_capability.as_str(),
            "read_only" | "linear_write" | "repository_write"
        )
    {
        return Err("slack_agent_run observable_event payload is inconsistent".to_owned());
    }
    Ok(())
}

fn reject_sensitive_event_keys(value: &Value) -> Result<(), String> {
    const FORBIDDEN: [&str; 7] = [
        "chain_of_thought",
        "hidden_reasoning",
        "private_reasoning",
        "raw_reasoning",
        "reasoning_tokens",
        "scratchpad",
        "internal_monologue",
    ];
    const SECRET_FRAGMENTS: [&str; 8] = [
        "authorization",
        "api_key",
        "access_token",
        "refresh_token",
        "password",
        "private_key",
        "cookie",
        "secret",
    ];
    match value {
        Value::Object(object) => {
            for (key, child) in object {
                let normalized = key.to_ascii_lowercase();
                if FORBIDDEN.contains(&normalized.as_str())
                    || SECRET_FRAGMENTS
                        .iter()
                        .any(|fragment| normalized.contains(fragment))
                {
                    return Err(
                        "slack_agent_run observable_event contains a forbidden field".to_owned(),
                    );
                }
                reject_sensitive_event_keys(child)?;
            }
        }
        Value::Array(values) => {
            for child in values {
                reject_sensitive_event_keys(child)?;
            }
        }
        _ => {}
    }
    Ok(())
}

fn write_policy_name(policy: WritePolicy) -> &'static str {
    match policy {
        WritePolicy::ReadOnly => "read_only",
        WritePolicy::LinearOnly => "linear_only",
        WritePolicy::DraftPullRequest => "draft_pull_request",
    }
}

fn valid_uuid(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() == 36
        && [8, 13, 18, 23]
            .into_iter()
            .all(|index| bytes.get(index) == Some(&b'-'))
        && bytes
            .iter()
            .enumerate()
            .all(|(index, byte)| [8, 13, 18, 23].contains(&index) || byte.is_ascii_hexdigit())
}

fn validate_broadcast_targets(targets: &[String]) -> Result<(), String> {
    let actual = targets.iter().map(String::as_str).collect::<BTreeSet<_>>();
    let expected = EXPECTED_BROADCAST_TARGETS
        .into_iter()
        .collect::<BTreeSet<_>>();
    if targets.len() != actual.len() || actual != expected {
        return Err("slack_agent_run broadcast_targets must be the canonical set".to_owned());
    }
    Ok(())
}

fn validate_identifier(field: &str, value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > MAX_IDENTIFIER_BYTES
        || value.chars().any(|character| {
            !character.is_ascii_alphanumeric() && !matches!(character, '-' | '_' | '.' | ':')
        })
    {
        return Err(format!("slack_agent_run {field} is invalid"));
    }
    Ok(())
}

fn valid_run_id(value: &str) -> bool {
    value.strip_prefix("ores-").is_some_and(|suffix| {
        suffix.len() == 24
            && suffix
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    })
}

fn validate_repository(value: &str) -> Result<(), String> {
    if value.len() > MAX_IDENTIFIER_BYTES
        || value.contains("://")
        || value.ends_with(".git")
        || value.matches('/').count() != 1
    {
        return Err("slack_agent_run repository is invalid".to_owned());
    }
    let Some((owner, repository)) = value.split_once('/') else {
        return Err("slack_agent_run repository is invalid".to_owned());
    };
    if owner.is_empty()
        || repository.is_empty()
        || owner.chars().chain(repository.chars()).any(|character| {
            !character.is_ascii_alphanumeric() && !matches!(character, '-' | '_' | '.')
        })
    {
        return Err("slack_agent_run repository is invalid".to_owned());
    }
    Ok(())
}

fn valid_issue_identifier(value: &str) -> bool {
    let Some((team, number)) = value.split_once('-') else {
        return false;
    };
    (2..=10).contains(&team.len())
        && team
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit())
        && !number.is_empty()
        && !number.starts_with('0')
        && number.bytes().all(|byte| byte.is_ascii_digit())
}

fn valid_slack_timestamp(value: &str) -> bool {
    let mut parts = value.split('.');
    let seconds = parts.next().is_some_and(|part| {
        !part.is_empty() && part.len() <= 20 && part.bytes().all(|byte| byte.is_ascii_digit())
    });
    let fraction = parts.next().is_some_and(|part| {
        !part.is_empty() && part.len() <= 12 && part.bytes().all(|byte| byte.is_ascii_digit())
    });
    seconds && fraction && parts.next().is_none()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn valid_observable_event() -> Value {
        json!({
            "schema_version": "1.0",
            "event_id": "01234567-89ab-5def-8123-456789abcdef",
            "idempotency_key": "slack-task-created:ores-00112233445566778899aabb",
            "occurred_at": "2026-08-04T11:20:00Z",
            "source": {
                "agent_id": "gpt-5.6-sol",
                "provider": "openai",
                "model": "gpt-5.6-sol",
                "instance_id": "alex-main-agent",
                "metadata": {
                    "surface": "slack_slash_command",
                    "action": "implement",
                    "write_policy": "draft_pull_request"
                }
            },
            "correlation": {
                "correlation_id": "fedcba98-7654-5abc-9123-456789abcdef",
                "server_id": "ai-agent-coordinator",
                "session_id": "ores-00112233445566778899aabb",
                "run_id": "ores-00112233445566778899aabb",
                "task_id": "ores-00112233445566778899aabb"
            },
            "kind": "task_created",
            "payload_classification": "internal",
            "redaction_state": "sanitized",
            "evidence_references": [],
            "delivery": {
                "transport": "http",
                "delivery_id": "coordinator-job:ores-00112233445566778899aabb",
                "attempt": 1,
                "ack_requested": true
            },
            "payload": {
                "repository": "ORESoftware/ai-agent-coordinator.rs",
                "linear_project_id": "project-uuid",
                "linear_run_project_id": "run-project-uuid",
                "linear_issue": "DEN-1231",
                "bridge_workflow_id": "workflow-123",
                "requested_capability": "repository_write"
            }
        })
    }

    fn valid_payload() -> Value {
        json!({
            "schema_version": 1,
            "run_id": "ores-00112233445566778899aabb",
            "bridge_workflow_id": "workflow-123",
            "provider": "chatgpt",
            "action": "implement",
            "prompt": "Implement DEN-1231 with tests.",
            "origin": {
                "workspace_id": "T012345",
                "channel_id": "C012345",
                "requester_user_id": "U012345"
            },
            "context": {
                "trust": "untrusted_channel_context",
                "selection": "latest_non_bot_channel_messages",
                "messages": [
                    {"user_id": "U1", "ts": "1000.000001", "text": "first"},
                    {"user_id": "U2", "ts": "1001.000001", "text": "second"}
                ]
            },
            "routing": {
                "repository": "ORESoftware/ai-agent-coordinator.rs",
                "linear_team_id": "team-uuid",
                "linear_project_id": "project-uuid",
                "linear_run_project_id": "run-project-uuid",
                "linear_issue": "DEN-1231",
                "write_policy": "draft_pull_request"
            },
            "observable_event": valid_observable_event(),
            "broadcast_targets": [
                "slack_run_thread",
                "ai_agent_coordinator_job",
                "ai_agent_bridge_workflow",
                "linear_run_queue",
                "github_branch_pr_checks"
            ]
        })
    }

    #[test]
    fn accepts_canonical_payload() {
        let payload = SlackAgentRunPayload::from_value(&valid_payload()).unwrap();
        assert_eq!(payload.provider, Provider::Chatgpt);
        assert_eq!(payload.context.messages.len(), 2);
        assert!(payload.observable_event.is_some());
    }

    #[test]
    fn preserves_backward_compatibility_without_observable_event() {
        let mut value = valid_payload();
        value.as_object_mut().unwrap().remove("observable_event");
        assert!(SlackAgentRunPayload::from_value(&value).is_ok());
    }

    #[test]
    fn rejects_unknown_fields() {
        let mut value = valid_payload();
        value["secret"] = json!("must not enter the queue");
        assert!(SlackAgentRunPayload::from_value(&value).is_err());
    }

    #[test]
    fn rejects_noncanonical_broadcast_targets() {
        let mut value = valid_payload();
        value["broadcast_targets"] = json!(["linear_run_queue"]);
        assert!(SlackAgentRunPayload::from_value(&value).is_err());
    }

    #[test]
    fn rejects_oversized_context() {
        let mut value = valid_payload();
        value["context"]["messages"] = Value::Array(
            (0..=MAX_CONTEXT_MESSAGES)
                .map(|index| {
                    json!({
                        "user_id": "U1",
                        "ts": format!("{}.000001", 1000 + index),
                        "text": "message"
                    })
                })
                .collect(),
        );
        assert!(SlackAgentRunPayload::from_value(&value).is_err());
    }

    #[test]
    fn rejects_context_without_untrusted_marker() {
        let mut value = valid_payload();
        value["context"]["trust"] = json!("trusted");
        assert!(SlackAgentRunPayload::from_value(&value).is_err());
    }

    #[test]
    fn rejects_observable_event_with_private_or_unknown_payload_fields() {
        for key in ["prompt", "secret_token"] {
            let mut value = valid_payload();
            value["observable_event"]["payload"][key] = json!("must not enter observability");
            assert!(
                SlackAgentRunPayload::from_value(&value).is_err(),
                "{key} must be rejected"
            );
        }
    }

    #[test]
    fn rejects_observable_event_cross_field_drift() {
        for (path, replacement) in [
            (
                &["observable_event", "correlation", "run_id"][..],
                json!("ores-ffffffffffffffffffffffff"),
            ),
            (
                &["observable_event", "payload", "repository"][..],
                json!("ORESoftware/other.rs"),
            ),
            (
                &["observable_event", "payload", "bridge_workflow_id"][..],
                json!("workflow-other"),
            ),
        ] {
            let mut value = valid_payload();
            let mut current = &mut value;
            for segment in &path[..path.len() - 1] {
                current = &mut current[*segment];
            }
            current[path[path.len() - 1]] = replacement;
            assert!(SlackAgentRunPayload::from_value(&value).is_err());
        }
    }

    #[test]
    fn rejects_observable_event_policy_drift() {
        for (field, replacement) in [
            ("kind", json!("task_progressed")),
            ("payload_classification", json!("restricted")),
            ("redaction_state", json!("raw")),
        ] {
            let mut value = valid_payload();
            value["observable_event"][field] = replacement;
            assert!(SlackAgentRunPayload::from_value(&value).is_err());
        }
    }
}
