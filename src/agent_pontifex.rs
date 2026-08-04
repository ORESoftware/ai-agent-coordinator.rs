//! Public compatibility boundary between the community coordinator and the
//! Agent Pontifex protocol/SDK.
//!
//! The coordinator keeps its database and validation authority local. This
//! module only describes portable capabilities and verifies the serialized job
//! envelope that public workers consume.

use crate::jobs::Job;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::fmt;

pub const PROTOCOL_SCHEMA_VERSION: u16 = 1;
pub const COORDINATOR_PROTOCOL_ID: &str = "agent-pontifex.coordinator.v1";
pub const PROTOCOL_SOURCE_REPOSITORY: &str = "ORESoftware/ai-agent-bridge.rs";
pub const PROTOCOL_SOURCE_PATH: &str = "sdk/agent-pontifex-protocol/src/lib.rs";
pub const PROTOCOL_SOURCE_REVISION: &str = "a5c4ece6fc4fbff204de45576d59430cdf41977f";

const JOB_KEYS: &[&str] = &[
    "attempts",
    "available_at",
    "budget_usd",
    "claimed_by",
    "created_at",
    "id",
    "last_error",
    "lease_expires_at",
    "max_attempts",
    "org",
    "payload",
    "priority",
    "repo",
    "result",
    "status",
    "task_type",
    "updated_at",
];

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct CompatibilityDescriptor {
    pub schema_version: u16,
    pub protocol: String,
    pub service: String,
    pub implementation: String,
    pub protocol_source: ProtocolSource,
    pub capabilities: Vec<String>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub extensions: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ProtocolSource {
    pub repository: String,
    pub path: String,
    pub revision: String,
}

pub fn descriptor() -> CompatibilityDescriptor {
    CompatibilityDescriptor {
        schema_version: PROTOCOL_SCHEMA_VERSION,
        protocol: COORDINATOR_PROTOCOL_ID.to_string(),
        service: "ai-agent-coordinator".to_string(),
        implementation: "agent-pontifex".to_string(),
        protocol_source: ProtocolSource {
            repository: PROTOCOL_SOURCE_REPOSITORY.to_string(),
            path: PROTOCOL_SOURCE_PATH.to_string(),
            revision: PROTOCOL_SOURCE_REVISION.to_string(),
        },
        capabilities: vec![
            "coordinator.jobs.cancel".to_string(),
            "coordinator.jobs.claim".to_string(),
            "coordinator.jobs.complete".to_string(),
            "coordinator.jobs.create".to_string(),
            "coordinator.jobs.heartbeat".to_string(),
            "coordinator.model-gateway.openai-compatible".to_string(),
            "coordinator.webhooks.github".to_string(),
        ],
        extensions: BTreeMap::new(),
    }
}

impl CompatibilityDescriptor {
    pub fn validate(&self) -> Result<(), ContractError> {
        if self.schema_version != PROTOCOL_SCHEMA_VERSION {
            return Err(ContractError::new("unsupported schema version"));
        }
        if self.protocol != COORDINATOR_PROTOCOL_ID {
            return Err(ContractError::new("unexpected coordinator protocol"));
        }
        if self.service != "ai-agent-coordinator" || self.implementation != "agent-pontifex" {
            return Err(ContractError::new(
                "unexpected community implementation identity",
            ));
        }
        if self.protocol_source.repository != PROTOCOL_SOURCE_REPOSITORY
            || self.protocol_source.path != PROTOCOL_SOURCE_PATH
            || self.protocol_source.revision != PROTOCOL_SOURCE_REVISION
        {
            return Err(ContractError::new("protocol source pin drifted"));
        }
        if !is_lower_hex_commit(&self.protocol_source.revision) {
            return Err(ContractError::new(
                "protocol source revision is not immutable",
            ));
        }

        let mut seen = BTreeSet::new();
        for capability in &self.capabilities {
            validate_identifier(capability, "capability")?;
            if !seen.insert(capability.as_str()) {
                return Err(ContractError::new("duplicate capability"));
            }
        }
        let mut sorted = self.capabilities.clone();
        sorted.sort();
        if sorted != self.capabilities {
            return Err(ContractError::new(
                "capabilities are not deterministically sorted",
            ));
        }
        if self
            .extensions
            .keys()
            .any(|key| key.starts_with("fiducia."))
        {
            return Err(ContractError::new(
                "the community descriptor cannot claim Fiducia extensions",
            ));
        }
        for extension in self.extensions.keys() {
            validate_identifier(extension, "extension")?;
            if !extension.contains('.') {
                return Err(ContractError::new("extension is not vendor namespaced"));
            }
        }
        Ok(())
    }
}

pub fn job_to_protocol_value(job: &Job) -> Result<Value, ContractError> {
    let value = serde_json::to_value(job)
        .map_err(|error| ContractError::new(format!("unable to serialize job: {error}")))?;
    validate_job_value(&value)?;
    Ok(value)
}

pub fn validate_job_value(value: &Value) -> Result<(), ContractError> {
    let object = value
        .as_object()
        .ok_or_else(|| ContractError::new("job envelope must be an object"))?;
    let actual: BTreeSet<&str> = object.keys().map(String::as_str).collect();
    let expected: BTreeSet<&str> = JOB_KEYS.iter().copied().collect();
    if actual != expected {
        return Err(ContractError::new("job envelope keys drifted"));
    }

    for key in [
        "id",
        "org",
        "repo",
        "task_type",
        "created_at",
        "updated_at",
        "available_at",
    ] {
        require_non_empty_string(object.get(key), key)?;
    }
    for key in ["priority", "attempts", "max_attempts"] {
        if object.get(key).and_then(Value::as_i64).is_none() {
            return Err(ContractError::new(format!("job {key} must be an integer")));
        }
    }
    let status = object
        .get("status")
        .and_then(Value::as_str)
        .ok_or_else(|| ContractError::new("job status must be a string"))?;
    if !matches!(
        status,
        "queued" | "running" | "succeeded" | "failed" | "cancelled"
    ) {
        return Err(ContractError::new("job status is outside the public enum"));
    }
    for key in ["claimed_by", "lease_expires_at", "last_error"] {
        let candidate = object
            .get(key)
            .ok_or_else(|| ContractError::new(format!("job {key} is missing")))?;
        if !candidate.is_null() && !candidate.is_string() {
            return Err(ContractError::new(format!(
                "job {key} must be a string or null"
            )));
        }
    }
    let budget = object
        .get("budget_usd")
        .ok_or_else(|| ContractError::new("job budget_usd is missing"))?;
    if !budget.is_null() && budget.as_f64().is_none() {
        return Err(ContractError::new("job budget_usd must be numeric or null"));
    }
    Ok(())
}

fn require_non_empty_string(value: Option<&Value>, key: &str) -> Result<(), ContractError> {
    let value = value
        .and_then(Value::as_str)
        .ok_or_else(|| ContractError::new(format!("job {key} must be a string")))?;
    if value.trim().is_empty() {
        return Err(ContractError::new(format!("job {key} must not be empty")));
    }
    Ok(())
}

fn validate_identifier(value: &str, field: &str) -> Result<(), ContractError> {
    if value.is_empty()
        || value.len() > 128
        || !value.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || b"-_.".contains(&byte)
        })
    {
        return Err(ContractError::new(format!("invalid {field} identifier")));
    }
    Ok(())
}

fn is_lower_hex_commit(value: &str) -> bool {
    value.len() == 40
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContractError {
    message: String,
}

impl ContractError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for ContractError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl Error for ContractError {}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::jobs::{Job, JobStatus};
    use chrono::{TimeZone, Utc};
    use serde_json::json;

    fn sample_job() -> Job {
        let timestamp = Utc.with_ymd_and_hms(2026, 8, 4, 18, 0, 0).single().unwrap();
        Job {
            id: "job-1".to_string(),
            org: "agent-pontifex".to_string(),
            repo: "agent-coordinator.rs".to_string(),
            task_type: "code_change".to_string(),
            payload: json!({"goal": "add protocol negotiation"}),
            priority: 25,
            status: JobStatus::Queued,
            created_at: timestamp,
            updated_at: timestamp,
            available_at: timestamp,
            claimed_by: None,
            lease_expires_at: None,
            attempts: 0,
            max_attempts: 3,
            result: None,
            last_error: None,
            budget_usd: Some(1.5),
        }
    }

    #[test]
    fn community_descriptor_is_neutral_sorted_and_immutably_pinned() {
        let descriptor = descriptor();
        descriptor.validate().unwrap();
        assert!(descriptor.extensions.is_empty());
        assert!(descriptor
            .capabilities
            .windows(2)
            .all(|pair| pair[0] < pair[1]));
    }

    #[test]
    fn local_job_serialization_matches_the_public_envelope() {
        let value = job_to_protocol_value(&sample_job()).unwrap();
        assert_eq!(value["status"], "queued");
        assert_eq!(value["org"], "agent-pontifex");

        let mut drifted = value;
        drifted.as_object_mut().unwrap().remove("lease_expires_at");
        assert!(validate_job_value(&drifted).is_err());
    }
}
