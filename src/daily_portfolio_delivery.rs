//! Fenced, compare-and-set state transitions for daily portfolio briefing delivery.
//!
//! This module is intentionally transport- and storage-agnostic. PostgreSQL
//! adapters persist these records under DEN-2334; destination adapters must
//! supply a confirmed [`DestinationReceipt`] before a run becomes delivered.

use std::{collections::BTreeMap, fmt};

use chrono::NaiveDate;

const MAX_IDENTIFIER_BYTES: usize = 256;
const MAX_ERROR_BYTES: usize = 512;
const MAX_LEASE_TTL_MS: u64 = 60 * 60 * 1_000;
const SCHEDULED_PREFIX: &str = "daily-portfolio:scheduled:";
const RECOVERY_PREFIX: &str = "daily-portfolio:recovery:";
const MANUAL_PREFIX: &str = "daily-portfolio:manual:";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RunMode {
    Scheduled,
    Recovery,
    Manual,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeliveryStatus {
    Planned,
    Delivering,
    Ambiguous,
    Failed,
    Delivered,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanSpec {
    pub run_key: String,
    pub scheduled_run_key: String,
    pub mode: RunMode,
    pub source_digest: String,
    pub plan_digest: String,
    pub delivery_digest: String,
    pub destination: String,
    pub idempotency_key: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeaseToken {
    pub run_key: String,
    pub owner: String,
    pub fence: u64,
    pub expires_at_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DestinationReceipt {
    pub receipt_id: String,
    pub destination: String,
    pub body_digest: String,
    pub delivered_at_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScheduledBaseline {
    pub scheduled_run_key: String,
    pub plan_digest: String,
    pub delivery_digest: String,
    pub receipt_id: String,
    pub delivered_at_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunRecord {
    spec: PlanSpec,
    status: DeliveryStatus,
    generation: u64,
    attempts: u64,
    receipt: Option<DestinationReceipt>,
    last_error: Option<String>,
}

impl RunRecord {
    pub fn spec(&self) -> &PlanSpec {
        &self.spec
    }

    pub fn status(&self) -> DeliveryStatus {
        self.status
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    pub fn attempts(&self) -> u64 {
        self.attempts
    }

    pub fn receipt(&self) -> Option<&DestinationReceipt> {
        self.receipt.as_ref()
    }

    pub fn last_error(&self) -> Option<&str> {
        self.last_error.as_deref()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlanOutcome {
    Planned,
    AlreadyPlanned,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MutationOutcome {
    Applied,
    AlreadyApplied,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeliveryStateError {
    InvalidIdentifier,
    InvalidDigest,
    InvalidErrorSummary,
    InvalidLeaseTtl,
    InvalidRunIdentity,
    RunNotFound,
    RunConflict,
    LeaseHeld,
    LeaseUnavailable,
    LeaseExpired,
    RecoveryRequired,
    StaleFence,
    GenerationConflict,
    InvalidTransition,
    ReceiptConflict,
    BaselineConflict,
    CounterOverflow,
}

impl fmt::Display for DeliveryStateError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::InvalidIdentifier => {
                "identifier is empty, oversized, contains unsafe bytes, or resembles a credential"
            }
            Self::InvalidDigest => "digest must be a lowercase SHA-256 value",
            Self::InvalidErrorSummary => {
                "error summary is empty, oversized, or contains control characters"
            }
            Self::InvalidLeaseTtl => "lease TTL is zero or exceeds one hour",
            Self::InvalidRunIdentity => {
                "run mode, run key, scheduled run key, or idempotency key disagree"
            }
            Self::RunNotFound => "delivery run does not exist",
            Self::RunConflict => "run key already names a different immutable plan",
            Self::LeaseHeld => "an unexpired delivery lease is already held",
            Self::LeaseUnavailable => "no delivery lease exists for this run",
            Self::LeaseExpired => "the delivery lease expired before the requested transition",
            Self::RecoveryRequired => {
                "an expired in-flight delivery must be recovered to ambiguous before reacquisition"
            }
            Self::StaleFence => "the lease owner or fencing token is stale",
            Self::GenerationConflict => "the expected run generation does not match",
            Self::InvalidTransition => "the requested delivery-state transition is not allowed",
            Self::ReceiptConflict => "the destination receipt conflicts with the planned delivery",
            Self::BaselineConflict => {
                "the scheduled baseline already records a different delivery for this date"
            }
            Self::CounterOverflow => "a monotonic delivery-state counter overflowed",
        })
    }
}

impl std::error::Error for DeliveryStateError {}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DeliveryState {
    next_fence: u64,
    runs: BTreeMap<String, RunRecord>,
    leases: BTreeMap<String, LeaseToken>,
    scheduled_baseline: Option<ScheduledBaseline>,
}

impl DeliveryState {
    pub fn plan(&mut self, spec: PlanSpec) -> Result<PlanOutcome, DeliveryStateError> {
        validate_plan(&spec)?;
        if let Some(existing) = self.runs.get(&spec.run_key) {
            if existing.spec == spec {
                return Ok(PlanOutcome::AlreadyPlanned);
            }
            return Err(DeliveryStateError::RunConflict);
        }
        self.runs.insert(
            spec.run_key.clone(),
            RunRecord {
                spec,
                status: DeliveryStatus::Planned,
                generation: 0,
                attempts: 0,
                receipt: None,
                last_error: None,
            },
        );
        Ok(PlanOutcome::Planned)
    }

    pub fn run(&self, run_key: &str) -> Option<&RunRecord> {
        self.runs.get(run_key)
    }

    pub fn scheduled_baseline(&self) -> Option<&ScheduledBaseline> {
        self.scheduled_baseline.as_ref()
    }

    pub fn lease(&self, run_key: &str) -> Option<&LeaseToken> {
        self.leases.get(run_key)
    }

    pub fn acquire(
        &mut self,
        run_key: &str,
        owner: &str,
        now_ms: u64,
        ttl_ms: u64,
    ) -> Result<LeaseToken, DeliveryStateError> {
        validate_identifier(run_key)?;
        validate_identifier(owner)?;
        validate_ttl(ttl_ms)?;
        let record = self
            .runs
            .get(run_key)
            .ok_or(DeliveryStateError::RunNotFound)?;
        if record.status == DeliveryStatus::Delivered {
            return Err(DeliveryStateError::InvalidTransition);
        }
        if self
            .leases
            .get(run_key)
            .is_some_and(|lease| lease.expires_at_ms > now_ms)
        {
            return Err(DeliveryStateError::LeaseHeld);
        }
        if record.status == DeliveryStatus::Delivering {
            return Err(DeliveryStateError::RecoveryRequired);
        }
        self.next_fence = self
            .next_fence
            .checked_add(1)
            .ok_or(DeliveryStateError::CounterOverflow)?;
        let expires_at_ms = now_ms
            .checked_add(ttl_ms)
            .ok_or(DeliveryStateError::CounterOverflow)?;
        let token = LeaseToken {
            run_key: run_key.to_owned(),
            owner: owner.to_owned(),
            fence: self.next_fence,
            expires_at_ms,
        };
        self.leases.insert(run_key.to_owned(), token.clone());
        Ok(token)
    }

    pub fn renew(
        &mut self,
        token: &LeaseToken,
        now_ms: u64,
        ttl_ms: u64,
    ) -> Result<LeaseToken, DeliveryStateError> {
        validate_ttl(ttl_ms)?;
        self.require_active(token, now_ms)?;
        let expires_at_ms = now_ms
            .checked_add(ttl_ms)
            .ok_or(DeliveryStateError::CounterOverflow)?;
        let renewed = LeaseToken {
            run_key: token.run_key.clone(),
            owner: token.owner.clone(),
            fence: token.fence,
            expires_at_ms,
        };
        self.leases.insert(token.run_key.clone(), renewed.clone());
        Ok(renewed)
    }

    pub fn release(&mut self, token: &LeaseToken, now_ms: u64) -> Result<(), DeliveryStateError> {
        self.require_active(token, now_ms)?;
        let record = self
            .runs
            .get(&token.run_key)
            .ok_or(DeliveryStateError::RunNotFound)?;
        if record.status == DeliveryStatus::Delivering {
            return Err(DeliveryStateError::InvalidTransition);
        }
        self.leases.remove(&token.run_key);
        Ok(())
    }

    pub fn begin_delivery(
        &mut self,
        token: &LeaseToken,
        now_ms: u64,
        expected_generation: u64,
    ) -> Result<MutationOutcome, DeliveryStateError> {
        self.require_active(token, now_ms)?;
        let record = self
            .runs
            .get_mut(&token.run_key)
            .ok_or(DeliveryStateError::RunNotFound)?;
        require_generation(record, expected_generation)?;
        match record.status {
            DeliveryStatus::Planned | DeliveryStatus::Failed => {
                record.attempts = record
                    .attempts
                    .checked_add(1)
                    .ok_or(DeliveryStateError::CounterOverflow)?;
                record.generation = record
                    .generation
                    .checked_add(1)
                    .ok_or(DeliveryStateError::CounterOverflow)?;
                record.status = DeliveryStatus::Delivering;
                record.last_error = None;
                Ok(MutationOutcome::Applied)
            }
            DeliveryStatus::Delivering => Ok(MutationOutcome::AlreadyApplied),
            DeliveryStatus::Ambiguous | DeliveryStatus::Delivered => {
                Err(DeliveryStateError::InvalidTransition)
            }
        }
    }

    pub fn mark_failed(
        &mut self,
        token: &LeaseToken,
        now_ms: u64,
        expected_generation: u64,
        error_summary: &str,
    ) -> Result<MutationOutcome, DeliveryStateError> {
        validate_error_summary(error_summary)?;
        self.require_active(token, now_ms)?;
        {
            let record = self
                .runs
                .get_mut(&token.run_key)
                .ok_or(DeliveryStateError::RunNotFound)?;
            require_generation(record, expected_generation)?;
            if record.status != DeliveryStatus::Delivering {
                return Err(DeliveryStateError::InvalidTransition);
            }
            record.generation = record
                .generation
                .checked_add(1)
                .ok_or(DeliveryStateError::CounterOverflow)?;
            record.status = DeliveryStatus::Failed;
            record.last_error = Some(error_summary.to_owned());
        }
        self.leases.remove(&token.run_key);
        Ok(MutationOutcome::Applied)
    }

    pub fn mark_ambiguous(
        &mut self,
        token: &LeaseToken,
        now_ms: u64,
        expected_generation: u64,
        error_summary: &str,
    ) -> Result<MutationOutcome, DeliveryStateError> {
        validate_error_summary(error_summary)?;
        self.require_active(token, now_ms)?;
        {
            let record = self
                .runs
                .get_mut(&token.run_key)
                .ok_or(DeliveryStateError::RunNotFound)?;
            require_generation(record, expected_generation)?;
            if record.status != DeliveryStatus::Delivering {
                return Err(DeliveryStateError::InvalidTransition);
            }
            record.generation = record
                .generation
                .checked_add(1)
                .ok_or(DeliveryStateError::CounterOverflow)?;
            record.status = DeliveryStatus::Ambiguous;
            record.last_error = Some(error_summary.to_owned());
        }
        self.leases.remove(&token.run_key);
        Ok(MutationOutcome::Applied)
    }

    pub fn recover_expired_delivery(
        &mut self,
        run_key: &str,
        now_ms: u64,
    ) -> Result<MutationOutcome, DeliveryStateError> {
        validate_identifier(run_key)?;
        if self
            .leases
            .get(run_key)
            .is_some_and(|lease| lease.expires_at_ms > now_ms)
        {
            return Err(DeliveryStateError::LeaseHeld);
        }
        let record = self
            .runs
            .get_mut(run_key)
            .ok_or(DeliveryStateError::RunNotFound)?;
        if record.status != DeliveryStatus::Delivering {
            return Ok(MutationOutcome::AlreadyApplied);
        }
        record.generation = record
            .generation
            .checked_add(1)
            .ok_or(DeliveryStateError::CounterOverflow)?;
        record.status = DeliveryStatus::Ambiguous;
        record.last_error =
            Some("delivery lease expired before a receipt was committed".to_owned());
        self.leases.remove(run_key);
        Ok(MutationOutcome::Applied)
    }

    pub fn record_receipt(
        &mut self,
        token: &LeaseToken,
        now_ms: u64,
        expected_generation: u64,
        receipt: DestinationReceipt,
    ) -> Result<MutationOutcome, DeliveryStateError> {
        validate_receipt(&receipt)?;
        validate_identifier(&token.run_key)?;
        validate_identifier(&token.owner)?;

        let record = self
            .runs
            .get(&token.run_key)
            .ok_or(DeliveryStateError::RunNotFound)?;
        if record.status == DeliveryStatus::Delivered {
            if record.receipt.as_ref() != Some(&receipt) {
                return Err(DeliveryStateError::ReceiptConflict);
            }
            let committed_generation = expected_generation
                .checked_add(1)
                .ok_or(DeliveryStateError::CounterOverflow)?;
            if record.generation != committed_generation {
                return Err(DeliveryStateError::GenerationConflict);
            }
            return Ok(MutationOutcome::AlreadyApplied);
        }

        self.require_active(token, now_ms)?;
        let record = self
            .runs
            .get(&token.run_key)
            .ok_or(DeliveryStateError::RunNotFound)?;
        require_generation(record, expected_generation)?;
        if !matches!(
            record.status,
            DeliveryStatus::Delivering | DeliveryStatus::Ambiguous
        ) {
            return Err(DeliveryStateError::InvalidTransition);
        }
        if receipt.destination != record.spec.destination
            || receipt.body_digest != record.spec.delivery_digest
        {
            return Err(DeliveryStateError::ReceiptConflict);
        }

        let replace_baseline = self.baseline_replacement(record, &receipt)?;
        {
            let record = self
                .runs
                .get_mut(&token.run_key)
                .ok_or(DeliveryStateError::RunNotFound)?;
            record.generation = record
                .generation
                .checked_add(1)
                .ok_or(DeliveryStateError::CounterOverflow)?;
            record.status = DeliveryStatus::Delivered;
            record.receipt = Some(receipt.clone());
            record.last_error = None;
        }
        self.leases.remove(&token.run_key);
        if replace_baseline {
            let record = self
                .runs
                .get(&token.run_key)
                .ok_or(DeliveryStateError::RunNotFound)?;
            self.scheduled_baseline = Some(ScheduledBaseline {
                scheduled_run_key: record.spec.scheduled_run_key.clone(),
                plan_digest: record.spec.plan_digest.clone(),
                delivery_digest: record.spec.delivery_digest.clone(),
                receipt_id: receipt.receipt_id,
                delivered_at_ms: receipt.delivered_at_ms,
            });
        }
        Ok(MutationOutcome::Applied)
    }

    fn baseline_replacement(
        &self,
        record: &RunRecord,
        receipt: &DestinationReceipt,
    ) -> Result<bool, DeliveryStateError> {
        if record.spec.mode == RunMode::Manual {
            return Ok(false);
        }
        let candidate_date = scheduled_date(&record.spec.scheduled_run_key)?;
        let Some(current) = self.scheduled_baseline.as_ref() else {
            return Ok(true);
        };
        let current_date = scheduled_date(&current.scheduled_run_key)?;
        if candidate_date > current_date {
            return Ok(true);
        }
        if candidate_date < current_date {
            return Ok(false);
        }
        if current.plan_digest == record.spec.plan_digest
            && current.delivery_digest == record.spec.delivery_digest
            && current.receipt_id == receipt.receipt_id
        {
            return Ok(false);
        }
        Err(DeliveryStateError::BaselineConflict)
    }

    fn require_active(&self, token: &LeaseToken, now_ms: u64) -> Result<(), DeliveryStateError> {
        let current = self
            .leases
            .get(&token.run_key)
            .ok_or(DeliveryStateError::LeaseUnavailable)?;
        if current.owner != token.owner || current.fence != token.fence {
            return Err(DeliveryStateError::StaleFence);
        }
        if current.expires_at_ms <= now_ms || token.expires_at_ms <= now_ms {
            return Err(DeliveryStateError::LeaseExpired);
        }
        Ok(())
    }
}

fn require_generation(
    record: &RunRecord,
    expected_generation: u64,
) -> Result<(), DeliveryStateError> {
    if record.generation == expected_generation {
        Ok(())
    } else {
        Err(DeliveryStateError::GenerationConflict)
    }
}

fn validate_plan(spec: &PlanSpec) -> Result<(), DeliveryStateError> {
    validate_identifier(&spec.run_key)?;
    validate_identifier(&spec.scheduled_run_key)?;
    validate_identifier(&spec.destination)?;
    validate_identifier(&spec.idempotency_key)?;
    validate_digest(&spec.source_digest)?;
    validate_digest(&spec.plan_digest)?;
    validate_digest(&spec.delivery_digest)?;
    let _ = scheduled_date(&spec.scheduled_run_key)?;
    if spec.idempotency_key != spec.run_key {
        return Err(DeliveryStateError::InvalidRunIdentity);
    }
    let identity_matches = match spec.mode {
        RunMode::Scheduled => spec.run_key == spec.scheduled_run_key,
        RunMode::Recovery => {
            has_identity_suffix(&spec.run_key, RECOVERY_PREFIX)
                && spec.run_key != spec.scheduled_run_key
        }
        RunMode::Manual => {
            has_identity_suffix(&spec.run_key, MANUAL_PREFIX)
                && spec.run_key != spec.scheduled_run_key
        }
    };
    if identity_matches {
        Ok(())
    } else {
        Err(DeliveryStateError::InvalidRunIdentity)
    }
}

fn has_identity_suffix(value: &str, prefix: &str) -> bool {
    value.strip_prefix(prefix).is_some_and(|suffix| {
        suffix
            .bytes()
            .any(|byte| byte.is_ascii_alphanumeric())
    })
}

fn scheduled_date(value: &str) -> Result<NaiveDate, DeliveryStateError> {
    let suffix = value
        .strip_prefix(SCHEDULED_PREFIX)
        .ok_or(DeliveryStateError::InvalidRunIdentity)?;
    if suffix.len() != 10 {
        return Err(DeliveryStateError::InvalidRunIdentity);
    }
    NaiveDate::parse_from_str(suffix, "%Y-%m-%d")
        .map_err(|_| DeliveryStateError::InvalidRunIdentity)
}

fn validate_ttl(ttl_ms: u64) -> Result<(), DeliveryStateError> {
    if ttl_ms == 0 || ttl_ms > MAX_LEASE_TTL_MS {
        Err(DeliveryStateError::InvalidLeaseTtl)
    } else {
        Ok(())
    }
}

fn validate_digest(value: &str) -> Result<(), DeliveryStateError> {
    if value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
    {
        Ok(())
    } else {
        Err(DeliveryStateError::InvalidDigest)
    }
}

fn validate_identifier(value: &str) -> Result<(), DeliveryStateError> {
    let lower = value.to_ascii_lowercase();
    let credential_shaped = [
        "ghp_",
        "gho_",
        "ghu_",
        "ghs_",
        "ghr_",
        "github_pat_",
        "sk-",
        "xoxb-",
        "xoxa-",
        "xoxp-",
        "xoxr-",
        "xoxs-",
    ]
    .iter()
    .any(|prefix| lower.starts_with(prefix))
        || lower.contains("api_key=")
        || lower.contains("apikey=")
        || lower.contains("access_token=")
        || lower.contains("auth_token=")
        || lower.contains("bearer=")
        || lower.contains("token=")
        || lower.contains("password=")
        || lower.contains("secret=");
    let valid = !value.is_empty()
        && value.len() <= MAX_IDENTIFIER_BYTES
        && !credential_shaped
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':' | b'/')
        });
    if valid {
        Ok(())
    } else {
        Err(DeliveryStateError::InvalidIdentifier)
    }
}

fn validate_error_summary(value: &str) -> Result<(), DeliveryStateError> {
    if !value.is_empty() && value.len() <= MAX_ERROR_BYTES && !value.chars().any(char::is_control) {
        Ok(())
    } else {
        Err(DeliveryStateError::InvalidErrorSummary)
    }
}

fn validate_receipt(receipt: &DestinationReceipt) -> Result<(), DeliveryStateError> {
    validate_identifier(&receipt.receipt_id)?;
    validate_identifier(&receipt.destination)?;
    validate_digest(&receipt.body_digest)?;
    if receipt.delivered_at_ms == 0 {
        return Err(DeliveryStateError::ReceiptConflict);
    }
    Ok(())
}
