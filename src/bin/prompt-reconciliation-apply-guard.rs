//! Explicit apply authorization and crash-safe local receipts for prompt reconciliation.

use std::{
    collections::BTreeMap,
    env, fmt,
    fs::{self, File, OpenOptions},
    io::Write as _,
    path::{Path, PathBuf},
    process,
    time::{SystemTime, UNIX_EPOCH},
};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

const APPLY_ENV: &str = "PROMPT_RECONCILIATION_APPLY_ENABLED";
const CONFIRMATION: &str = "APPLY PROMPT RECONCILIATION";
const SCHEMA_VERSION: u32 = 1;
const MAX_IDENTIFIER_BYTES: usize = 256;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ErrorKind {
    Usage,
    Policy,
    InvalidPlan,
    ReceiptLocked,
    ReceiptConflict,
    Persistence,
}

impl ErrorKind {
    const fn code(self) -> &'static str {
        match self {
            Self::Usage => "usage",
            Self::Policy => "policy",
            Self::InvalidPlan => "invalid_plan",
            Self::ReceiptLocked => "receipt_locked",
            Self::ReceiptConflict => "receipt_conflict",
            Self::Persistence => "persistence",
        }
    }

    const fn exit_code(self) -> i32 {
        match self {
            Self::Usage => 64,
            Self::InvalidPlan => 65,
            Self::ReceiptConflict => 73,
            Self::Persistence => 74,
            Self::ReceiptLocked => 75,
            Self::Policy => 77,
        }
    }
}

#[derive(Debug)]
struct GuardError {
    kind: ErrorKind,
    message: &'static str,
}

impl GuardError {
    const fn new(kind: ErrorKind, message: &'static str) -> Self {
        Self { kind, message }
    }
}

impl fmt::Display for GuardError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.message)
    }
}

impl std::error::Error for GuardError {}

#[derive(Debug)]
struct Cli {
    plan: PathBuf,
    account: String,
    digest: String,
    confirmation: String,
    receipt_file: PathBuf,
    operation_id: String,
    mutation_key: String,
    canonical_issue_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    operation_id: String,
    mutation_key: String,
    canonical_issue_id: String,
    applied_at_unix_ms: u64,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Ledger {
    schema_version: u32,
    generation: u64,
    records: BTreeMap<String, Receipt>,
}

impl Default for Ledger {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            generation: 0,
            records: BTreeMap::new(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum ReceiptOutcome {
    Recorded,
    AlreadyRecorded,
}

#[derive(Debug, Serialize)]
struct Report<'a> {
    authorized: bool,
    account_fingerprint: &'a str,
    plan_digest: &'a str,
    operation_id: &'a str,
    canonical_issue_id: &'a str,
    receipt_outcome: ReceiptOutcome,
}

fn main() {
    if let Err(error) = run() {
        eprintln!(
            "prompt reconciliation apply refused [{}]: {error}",
            error.kind.code()
        );
        process::exit(error.kind.exit_code());
    }
}

fn run() -> Result<(), GuardError> {
    let cli = parse_cli(env::args().skip(1))?;
    let bytes = fs::read(&cli.plan)
        .map_err(|_| GuardError::new(ErrorKind::InvalidPlan, "could not read reviewed plan"))?;
    let plan: Value = serde_json::from_slice(&bytes)
        .map_err(|_| GuardError::new(ErrorKind::InvalidPlan, "reviewed plan is not valid JSON"))?;
    let expected_account = account_fingerprint(&plan).ok_or_else(|| {
        GuardError::new(
            ErrorKind::InvalidPlan,
            "reviewed plan has no bounded account fingerprint",
        )
    })?;
    let expected_digest = sha256_hex(&bytes);

    authorize(
        env::var(APPLY_ENV).as_deref() == Ok("true"),
        expected_account,
        &expected_digest,
        &cli.account,
        &cli.digest,
        &cli.confirmation,
    )?;
    validate_identifier(&cli.operation_id)?;
    validate_identifier(&cli.mutation_key)?;
    validate_identifier(&cli.canonical_issue_id)?;

    let outcome = record_receipt(
        &cli.receipt_file,
        Receipt {
            operation_id: cli.operation_id.clone(),
            mutation_key: cli.mutation_key.clone(),
            canonical_issue_id: cli.canonical_issue_id.clone(),
            applied_at_unix_ms: unix_time_ms()?,
        },
    )?;
    let report = Report {
        authorized: true,
        account_fingerprint: expected_account,
        plan_digest: &expected_digest,
        operation_id: &cli.operation_id,
        canonical_issue_id: &cli.canonical_issue_id,
        receipt_outcome: outcome,
    };
    let json = serde_json::to_string_pretty(&report)
        .map_err(|_| GuardError::new(ErrorKind::Persistence, "could not serialize apply report"))?;
    println!("{json}");
    Ok(())
}

fn parse_cli(arguments: impl Iterator<Item = String>) -> Result<Cli, GuardError> {
    let mut values = BTreeMap::new();
    let mut arguments = arguments.peekable();
    while let Some(flag) = arguments.next() {
        if !flag.starts_with("--") {
            return Err(GuardError::new(
                ErrorKind::Usage,
                "all arguments must use named flags",
            ));
        }
        let value = arguments
            .next()
            .ok_or_else(|| GuardError::new(ErrorKind::Usage, "every flag requires a value"))?;
        if value.starts_with("--") || values.insert(flag, value).is_some() {
            return Err(GuardError::new(
                ErrorKind::Usage,
                "missing values and duplicate flags are refused",
            ));
        }
    }

    fn take(
        values: &mut BTreeMap<String, String>,
        name: &'static str,
    ) -> Result<String, GuardError> {
        values
            .remove(name)
            .ok_or_else(|| GuardError::new(ErrorKind::Usage, "a required apply flag is missing"))
    }

    let cli = Cli {
        plan: PathBuf::from(take(&mut values, "--plan")?),
        account: take(&mut values, "--account")?,
        digest: take(&mut values, "--digest")?,
        confirmation: take(&mut values, "--confirmation")?,
        receipt_file: PathBuf::from(take(&mut values, "--receipt-file")?),
        operation_id: take(&mut values, "--operation-id")?,
        mutation_key: take(&mut values, "--mutation-key")?,
        canonical_issue_id: take(&mut values, "--canonical-issue-id")?,
    };
    if !values.is_empty() {
        return Err(GuardError::new(
            ErrorKind::Usage,
            "unknown apply flags are refused",
        ));
    }
    Ok(cli)
}

fn authorize(
    enabled: bool,
    expected_account: &str,
    expected_digest: &str,
    supplied_account: &str,
    supplied_digest: &str,
    supplied_confirmation: &str,
) -> Result<(), GuardError> {
    if !enabled {
        return Err(GuardError::new(
            ErrorKind::Policy,
            "apply requires PROMPT_RECONCILIATION_APPLY_ENABLED=true",
        ));
    }
    if supplied_account != expected_account {
        return Err(GuardError::new(
            ErrorKind::Policy,
            "account confirmation does not match the reviewed plan",
        ));
    }
    if !is_lower_hex_digest(supplied_digest) || supplied_digest != expected_digest {
        return Err(GuardError::new(
            ErrorKind::Policy,
            "digest confirmation does not match the exact reviewed plan bytes",
        ));
    }
    if supplied_confirmation != CONFIRMATION {
        return Err(GuardError::new(
            ErrorKind::Policy,
            "the exact apply confirmation phrase is required",
        ));
    }
    Ok(())
}

fn account_fingerprint(plan: &Value) -> Option<&str> {
    plan.get("account_fingerprint")
        .and_then(Value::as_str)
        .or_else(|| {
            plan.get("plan")
                .and_then(|value| value.get("account_fingerprint"))
                .and_then(Value::as_str)
        })
        .filter(|value| !value.is_empty() && value.len() <= MAX_IDENTIFIER_BYTES)
}

fn validate_identifier(value: &str) -> Result<(), GuardError> {
    let valid = !value.is_empty()
        && value.len() <= MAX_IDENTIFIER_BYTES
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':' | b'/')
        });
    if valid {
        Ok(())
    } else {
        Err(GuardError::new(
            ErrorKind::Policy,
            "persisted identifiers must be bounded safe ASCII",
        ))
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

fn unix_time_ms() -> Result<u64, GuardError> {
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| GuardError::new(ErrorKind::Persistence, "system clock precedes Unix epoch"))?;
    u64::try_from(duration.as_millis()).map_err(|_| {
        GuardError::new(
            ErrorKind::Persistence,
            "system timestamp cannot be represented safely",
        )
    })
}

fn record_receipt(path: &Path, receipt: Receipt) -> Result<ReceiptOutcome, GuardError> {
    let _lock = ReceiptLock::acquire(path)?;
    let mut ledger = load_ledger(path)?;
    if let Some(existing) = ledger.records.get(&receipt.operation_id) {
        if existing.mutation_key == receipt.mutation_key
            && existing.canonical_issue_id == receipt.canonical_issue_id
        {
            return Ok(ReceiptOutcome::AlreadyRecorded);
        }
        return Err(GuardError::new(
            ErrorKind::ReceiptConflict,
            "an existing operation receipt has a different canonical result",
        ));
    }
    ledger.generation = ledger
        .generation
        .checked_add(1)
        .ok_or_else(|| GuardError::new(ErrorKind::Persistence, "receipt generation overflowed"))?;
    ledger.records.insert(receipt.operation_id.clone(), receipt);
    persist_ledger(path, &ledger)?;
    Ok(ReceiptOutcome::Recorded)
}

fn load_ledger(path: &Path) -> Result<Ledger, GuardError> {
    if !path.exists() {
        return Ok(Ledger::default());
    }
    let bytes = fs::read(path)
        .map_err(|_| GuardError::new(ErrorKind::Persistence, "could not read receipt ledger"))?;
    let ledger: Ledger = serde_json::from_slice(&bytes)
        .map_err(|_| GuardError::new(ErrorKind::Persistence, "receipt ledger is malformed"))?;
    if ledger.schema_version != SCHEMA_VERSION {
        return Err(GuardError::new(
            ErrorKind::Persistence,
            "receipt ledger schema is unsupported",
        ));
    }
    Ok(ledger)
}

fn persist_ledger(path: &Path, ledger: &Ledger) -> Result<(), GuardError> {
    ensure_parent(path)?;
    let mut bytes = serde_json::to_vec_pretty(ledger).map_err(|_| {
        GuardError::new(ErrorKind::Persistence, "could not serialize receipt ledger")
    })?;
    bytes.push(b'\n');

    let temporary = temporary_path(path, ledger.generation);
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|_| {
            GuardError::new(
                ErrorKind::Persistence,
                "could not create temporary receipt ledger",
            )
        })?;
    let result = (|| {
        file.write_all(&bytes).map_err(|_| {
            GuardError::new(
                ErrorKind::Persistence,
                "could not write temporary receipt ledger",
            )
        })?;
        file.sync_all().map_err(|_| {
            GuardError::new(
                ErrorKind::Persistence,
                "could not sync temporary receipt ledger",
            )
        })?;
        fs::rename(&temporary, path).map_err(|_| {
            GuardError::new(
                ErrorKind::Persistence,
                "could not atomically replace receipt ledger",
            )
        })?;
        sync_parent(path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result
}

fn ensure_parent(path: &Path) -> Result<(), GuardError> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent).map_err(|_| {
            GuardError::new(ErrorKind::Persistence, "could not create receipt directory")
        })?;
    }
    Ok(())
}

fn sync_parent(path: &Path) -> Result<(), GuardError> {
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    File::open(parent)
        .and_then(|directory| directory.sync_all())
        .map_err(|_| GuardError::new(ErrorKind::Persistence, "could not sync receipt directory"))
}

fn temporary_path(path: &Path, generation: u64) -> PathBuf {
    let mut value = path.as_os_str().to_owned();
    value.push(format!(".tmp-{}-{generation}", process::id()));
    PathBuf::from(value)
}

struct ReceiptLock {
    path: PathBuf,
    file: File,
}

impl ReceiptLock {
    fn acquire(receipt_path: &Path) -> Result<Self, GuardError> {
        ensure_parent(receipt_path)?;
        let mut value = receipt_path.as_os_str().to_owned();
        value.push(".lock");
        let path = PathBuf::from(value);
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|_| {
                GuardError::new(
                    ErrorKind::ReceiptLocked,
                    "receipt ledger is locked by another local apply process",
                )
            })?;
        writeln!(file, "{}", process::id()).map_err(|_| {
            GuardError::new(ErrorKind::Persistence, "could not initialize receipt lock")
        })?;
        file.sync_all()
            .map_err(|_| GuardError::new(ErrorKind::Persistence, "could not sync receipt lock"))?;
        Ok(Self { path, file })
    }
}

impl Drop for ReceiptLock {
    fn drop(&mut self) {
        let _ = self.file.sync_all();
        let _ = fs::remove_file(&self.path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_PATH: AtomicU64 = AtomicU64::new(0);

    fn temp_path(label: &str) -> PathBuf {
        let sequence = NEXT_PATH.fetch_add(1, Ordering::Relaxed);
        env::temp_dir().join(format!(
            "prompt-reconciliation-guard-{}-{sequence}-{label}",
            process::id()
        ))
    }

    fn receipt(operation: &str, issue: &str) -> Receipt {
        Receipt {
            operation_id: operation.to_owned(),
            mutation_key: "mutation-1".to_owned(),
            canonical_issue_id: issue.to_owned(),
            applied_at_unix_ms: 1,
        }
    }

    #[test]
    fn error_kinds_have_stable_codes_and_nonzero_exit_statuses() {
        let kinds = [
            ErrorKind::Usage,
            ErrorKind::Policy,
            ErrorKind::InvalidPlan,
            ErrorKind::ReceiptLocked,
            ErrorKind::ReceiptConflict,
            ErrorKind::Persistence,
        ];
        let codes = kinds.map(ErrorKind::code);
        let exits = kinds.map(ErrorKind::exit_code);
        assert_eq!(codes.len(), 6);
        assert!(codes.into_iter().all(|code| !code.is_empty()));
        assert!(exits.into_iter().all(|code| code > 0));
        assert_eq!(ErrorKind::Policy.exit_code(), 77);
    }

    #[test]
    fn authorization_requires_all_exact_confirmations() {
        let digest = "a".repeat(64);
        assert!(authorize(
            true,
            "account-1",
            &digest,
            "account-1",
            &digest,
            CONFIRMATION
        )
        .is_ok());
        assert!(authorize(
            false,
            "account-1",
            &digest,
            "account-1",
            &digest,
            CONFIRMATION
        )
        .is_err());
        assert!(authorize(
            true,
            "account-1",
            &digest,
            "account-2",
            &digest,
            CONFIRMATION
        )
        .is_err());
        assert!(authorize(
            true,
            "account-1",
            &digest,
            "account-1",
            &"b".repeat(64),
            CONFIRMATION
        )
        .is_err());
        assert!(authorize(true, "account-1", &digest, "account-1", &digest, "apply").is_err());
    }

    #[test]
    fn digest_is_deterministic_and_byte_exact() {
        let first = sha256_hex(br#"{"plan":1}"#);
        assert_eq!(first, sha256_hex(br#"{"plan":1}"#));
        assert_ne!(first, sha256_hex(br#"{"plan":2}"#));
        assert_eq!(first.len(), 64);
        assert!(is_lower_hex_digest(&first));
        assert!(!is_lower_hex_digest(&first.to_uppercase()));
    }

    #[test]
    fn account_fingerprint_supports_preview_and_plan_shapes() {
        let direct = serde_json::json!({"account_fingerprint": "account-1"});
        let nested = serde_json::json!({"plan": {"account_fingerprint": "account-2"}});
        assert_eq!(account_fingerprint(&direct), Some("account-1"));
        assert_eq!(account_fingerprint(&nested), Some("account-2"));
    }

    #[test]
    fn receipt_rerun_is_a_noop() {
        let path = temp_path("rerun.json");
        assert_eq!(
            record_receipt(&path, receipt("operation-1", "DEN-1")).ok(),
            Some(ReceiptOutcome::Recorded)
        );
        assert_eq!(
            record_receipt(&path, receipt("operation-1", "DEN-1")).ok(),
            Some(ReceiptOutcome::AlreadyRecorded)
        );
        let ledger = load_ledger(&path);
        assert!(matches!(
            ledger,
            Ok(Ledger {
                generation: 1,
                ref records,
                ..
            }) if records.len() == 1
        ));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn conflicting_receipt_fails_closed() {
        let path = temp_path("conflict.json");
        assert!(record_receipt(&path, receipt("operation-1", "DEN-1")).is_ok());
        let result = record_receipt(&path, receipt("operation-1", "DEN-2"));
        assert!(matches!(
            result,
            Err(GuardError {
                kind: ErrorKind::ReceiptConflict,
                ..
            })
        ));
        let _ = fs::remove_file(path);
    }

    #[test]
    fn unknown_receipt_fields_are_refused() {
        let path = temp_path("unknown.json");
        assert!(fs::write(
            &path,
            r#"{"schema_version":1,"generation":0,"records":{},"unexpected":true}"#
        )
        .is_ok());
        assert!(load_ledger(&path).is_err());
        let _ = fs::remove_file(path);
    }

    #[test]
    fn existing_lock_refuses_concurrent_local_apply() {
        let path = temp_path("locked.json");
        let first = ReceiptLock::acquire(&path);
        assert!(first.is_ok());
        let second = ReceiptLock::acquire(&path);
        assert!(matches!(
            second,
            Err(GuardError {
                kind: ErrorKind::ReceiptLocked,
                ..
            })
        ));
        drop(first);
        assert!(ReceiptLock::acquire(&path).is_ok());
    }

    #[test]
    fn identifiers_are_bounded_safe_ascii() {
        assert!(validate_identifier("DEN-1610:operation/1").is_ok());
        assert!(validate_identifier("contains whitespace").is_err());
        assert!(validate_identifier(&"x".repeat(MAX_IDENTIFIER_BYTES + 1)).is_err());
    }
}
