use std::collections::BTreeSet;

use serde::Deserialize;
use serde_json::Value;

pub const TASK_TYPE: &str = "slack_agent_run";

const SCHEMA_VERSION: u32 = 1;
const MAX_IDENTIFIER_BYTES: usize = 255;
const MAX_PROMPT_BYTES: usize = 100_000;
const MAX_CONTEXT_MESSAGES: usize = 20;
const MAX_CONTEXT_MESSAGE_BYTES: usize = 4_000;
const MAX_CONTEXT_TOTAL_BYTES: usize = 32_000;
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
        validate_identifier(
            "routing.linear_run_project_id",
            &self.linear_run_project_id,
        )?;
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

fn validate_broadcast_targets(targets: &[String]) -> Result<(), String> {
    let actual = targets.iter().map(String::as_str).collect::<BTreeSet<_>>();
    let expected = EXPECTED_BROADCAST_TARGETS.into_iter().collect::<BTreeSet<_>>();
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
    value
        .strip_prefix("ores-")
        .is_some_and(|suffix| suffix.len() == 24 && suffix.bytes().all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase()))
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
        !part.is_empty()
            && part.len() <= 20
            && part.bytes().all(|byte| byte.is_ascii_digit())
    });
    let fraction = parts.next().is_some_and(|part| {
        !part.is_empty()
            && part.len() <= 12
            && part.bytes().all(|byte| byte.is_ascii_digit())
    });
    seconds && fraction && parts.next().is_none()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

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
}
