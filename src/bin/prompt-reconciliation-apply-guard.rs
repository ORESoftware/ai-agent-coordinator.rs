//! Guarded authorization and atomic local receipts for prompt reconciliation apply mode.
//!
//! This binary is intentionally connector-neutral. It validates an exact reviewed
//! plan, account fingerprint, confirmation phrase, and opt-in environment gate,
//! then records an idempotent local receipt after the caller's remote mutation
//! succeeds. GitHub and Linear credentials are never accepted as CLI arguments.

use std::{
    collections::BTreeMap,
    env,
    fmt::{self, Write as _},
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    process,
    time::{SystemTime, UNIX_EPOCH},
};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

const APPLY_ENV: &str = "PROMPT_RECONCILIATION_APPLY_ENABLED";
const CONFIRMATION_PHRASE: &str = "APPLY PROMPT RECONCILIATION";
const RECEIPT_SCHEMA_VERSION: u32 = 1;

#[derive(Debug)]
enum GuardError {
    Usage(&'static str),
    Policy(&'static str),
    Io(&'static str),
    InvalidPlan(&'static str),
    ReceiptConflict,
    ReceiptLocked,
}

impl fmt::Display for GuardError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Usage(message)
            | Self::Policy(message)
            | Self::Io(message)
            | Self::InvalidPlan(message) => formatter.write_str(message),
            Self::ReceiptConflict => formatter.write_str(
                "an existing operation receipt conflicts with the requested canonical result",
            ),
            Self::ReceiptLocked => formatter.write_str(
                "the receipt ledger is locked by another local apply process",
            ),
        }
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
struct AppliedReceipt {
    operation_id: String,
    mutation_key: String,
    canonical_issue_id: String,
    applied_at_unix_ms: u64,
}

#[derive(Debug, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ReceiptLedger {
    schema_version: u32,
    generation: u64,
    records: BTreeMap<String, AppliedReceipt>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum ReceiptOutcome {
    Recorded,
    AlreadyRecorded,
}

#[derive(Debug, Serialize)]
#[serde(deny_unknown_fields)]
struct ApplyGuardReport<'a> {
    authorized: bool,
    account_fingerprint: &'a str,
    plan_digest: &'a str,
    operation_id: &'a str,
    canonical_issue_id: &'a str,
    receipt_outcome: ReceiptOutcome,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("prompt reconciliation apply guard refused: {error}");
        process::exit(2);
    }
}

fn run() -> Result<(), GuardError> {
    let cli = parse_cli(env::args().skip(1))?;
    let plan_bytes = fs::read(&cli.plan).map_err(|_| GuardError::Io("could not read plan file"))?;
    let plan: Value = serde_json::from_slice(&plan_bytes)
        .map_err(|_| GuardError::InvalidPlan("plan file is not valid JSON"))?;
    let expected_account = extract_account_fingerprint(&plan).ok_or(GuardError::InvalidPlan(
        "plan does not contain an account fingerprint",
    ))?;
    let computed_digest = sha256_hex(&plan_bytes);

    authorize(
        env::var(APPLY_ENV).as_deref() == Ok("true"),
        expected_account,
        &computed_digest,
        &cli.account,
        &cli.digest,
        &cli.confirmation,
    )?;

    validate_identifier(&cli.operation_id, "operation id")?;
    validate_identifier(&cli.mutation_key, "mutation key")?;
    validate_identifier(&cli.canonical_issue_id, "canonical issue id")?;

    let receipt = AppliedReceipt {
        operation_id: cli.operation_id.clone(),
        mutation_key: cli.mutation_key.clone(),
        canonical_issue_id: cli.canonical_issue_id.clone(),
        applied_at_unix_ms: unix_time_ms()?,
    };
    let outcome = record_receipt(&cli.receipt_file, receipt)?;
    let report = ApplyGuardReport {
        authorized: true,
        account_fingerprint: expected_account,
        plan_digest: &computed_digest,
        operation_id: &cli.operation_id,
        canonical_issue_id: &cli.canonical_issue_id,
        receipt_outcome: outcome,
    };
    let json = serde_json::to_string_pretty(&report)
        .map_err(|_| GuardError::Io("could not serialize apply report"))?;
    println!("{json}");
    Ok(())
}

fn parse_cli(arguments: impl Iterator<Item = String>) -> Result<Cli, GuardError> {
    let mut values = BTreeMap::new();
    let mut arguments = arguments.peekable();
    while let Some(flag) = arguments.next() {
        if !flag.starts_with("--") {
            return Err(GuardError::Usage("all arguments must use named --flags"));
        }
        let value = arguments
            .next()
            .ok_or(GuardError::Usage("every flag requires a value"))?;
        if value.starts_with("--") {
            return Err(GuardError::Usage("every flag requires a value"));
        }
        if values.insert(flag, value).is_some() {
            return Err(GuardError::Usage("duplicate flags are refused"));
        }
    }

    let mut required = |name: &'static str| {
        values
            .remove(name)
            .ok_or(GuardError::Usage("a required apply guard flag is missing"))
    };
    let cli = Cli {
        plan: PathBuf::from(required("--plan")?),
        account: required("--account")?,
        digest: required("--digest")?,
        confirmation: required("--confirmation")?,
        receipt_file: PathBuf::from(required("--receipt-file")?),
        operation_id: required("--operation-id")?,
        mutation_key: required("--mutation-key")?,
        canonical_issue_id: required("--canonical-issue-id")?,
    };
    if !values.is_empty() {
        return Err(GuardError::Usage("unknown flags are refused"));
    }
    Ok(cli)
}

fn authorize(
    apply_enabled: bool,
    expected_account: &str,
    expected_digest: &str,
    supplied_account: &str,
    supplied_digest: &str,
    supplied_confirmation: &str,
) -> Result<(), GuardError> {
    if !apply_enabled {
        return Err(GuardError::Policy(
            "apply mode requires PROMPT_RECONCILIATION_APPLY_ENABLED=true",
        ));
    }
    if supplied_account != expected_account {
        return Err(GuardError::Policy(
            "account confirmation does not match the reviewed plan",
        ));
    }
    if !is_lower_hex_digest(supplied_digest) || supplied_digest != expected_digest {
        return Err(GuardError::Policy(
            "digest confirmation does not match the exact reviewed plan bytes",
        ));
    }
    if supplied_confirmation != CONFIRMATION_PHRASE {
        return Err(GuardError::Policy(
            "the exact apply confirmation phrase is required",
        ));
    }
    Ok(())
}

fn extract_account_fingerprint(plan: &Value) -> Option<&str> {
    plan.get("account_fingerprint")
        .and_then(Value::as_str)
        .or_else(|| {
            plan.get("plan")
                .and_then(|value| value.get("account_fingerprint"))
                .and_then(Value::as_str)
        })
        .filter(|value| !value.trim().is_empty() && value.len() <= 256)
}

fn validate_identifier(value: &str, _label: &str) -> Result<(), GuardError> {
    let valid = !value.is_empty()
        && value.len() <= 256
        && value.bytes().all(|byte| {
            byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':' | b'/')
        });
    if valid {
        Ok(())
    } else {
        Err(GuardError::Policy(
            "operation, mutation, and issue identifiers must be bounded safe ASCII",
        ))
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut output = String::with_capacity(64);
    for byte in digest {
        let _ = write!(&mut output, "{byte:02x}");
    }
    output
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
        .map_err(|_| GuardError::Io("system clock is before the Unix epoch"))?;
    u64::try_from(duration.as_millis())
        .map_err(|_| GuardError::Io("system time cannot be represented safely"))
}

fn record_receipt(path: &Path, receipt: AppliedReceipt) -> Result<ReceiptOutcome, GuardError> {
    let _lock = ReceiptLock::acquire(path)?;
    let mut ledger = load_ledger(path)?;
    if let Some(existing) = ledger.records.get(&receipt.operation_id) {
        if existing.mutation_key == receipt.mutation_key
            && existing.canonical_issue_id == receipt.canonical_issue_id
        {
            return Ok(ReceiptOutcome::AlreadyRecorded);
        }
        return Err(GuardError::ReceiptConflict);
    }

    ledger.generation = ledger
        .generation
        .checked_add(1)
        .ok_or(GuardError::Io("receipt generation overflowed"))?;
    ledger
        .records
        .insert(receipt.operation_id.clone(), receipt);
    persist_ledger(path, &ledger)?;
    Ok(ReceiptOutcome::Recorded)
}

fn load_ledger(path: &Path) -> Result<ReceiptLedger, GuardError> {
    if !path.exists() {
        return Ok(ReceiptLedger {
            schema_version: RECEIPT_SCHEMA_VERSION,
            ..ReceiptLedger::default()
        });
    }
    let bytes = fs::read(path).map_err(|_| GuardError::Io("could not read receipt ledger"))?;
    let ledger: ReceiptLedger = serde_json::from_slice(&bytes)
        .map_err(|_| GuardError::Io("receipt ledger is malformed"))?;
    if ledger.schema_version != RECEIPT_SCHEMA_VERSION {
        return Err(GuardError::Io("receipt ledger schema is unsupported"));
    }
    Ok(ledger)
}

fn persist_ledger(path: &Path, ledger: &ReceiptLedger) -> Result<(), GuardError> {
    if let Some(parent) = path.parent().filter(|parent| !parent.as_os_str().is_empty()) {
        fs::create_dir_all(parent)
            .map_err(|_| GuardError::Io("could not create receipt directory"))?;
    }
    let bytes = serde_json::to_vec_pretty(ledger)
        .map_err(|_| GuardError::Io("could not serialize receipt ledger"))?;
    let temporary = temporary_path(path, ledger.generation);
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|_| GuardError::Io("could not create temporary receipt ledger"))?;
    let result = (|| {
        file.write_all(&bytes)
            .map_err(|_| GuardError::Io("could not write temporary receipt ledger"))?;
        file.write_all(b"\n")
            .map_err(|_| GuardError::Io("could not finish temporary receipt ledger"))?;
        file.sync_all()
            .map_err(|_| GuardError::Io("could not sync temporary receipt ledger"))?;
        fs::rename(&temporary, path)
            .map_err(|_| GuardError::Io("could not atomically replace receipt ledger"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result
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
        if let Some(parent) = receipt_path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent)
                .map_err(|_| GuardError::Io("could not create receipt directory"))?;
        }
        let mut lock_path = receipt_path.as_os_str().to_owned();
        lock_path.push(".lock");
        let lock_path = PathBuf::from(lock_path);
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&lock_path)
            .map_err(|_| GuardError::ReceiptLocked)?;
        writeln!(file, "{}", process::id())
            .map_err(|_| GuardError::Io("could not initialize receipt lock"))?;
        file.sync_all()
            .map_err(|_| GuardError::Io("could not sync receipt lock"))?;
        Ok(Self {
            path: lock_path,
            file,
        })
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

    static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

    fn temp_path(name: &str) -> PathBuf {
        let sequence = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
        env::temp_dir().join(format!(
            "prompt-reconciliation-apply-guard-{}-{sequence}-{name}",
            process::id()
        ))
    }

    fn receipt(operation: &str, issue: &str) -> AppliedReceipt {
        AppliedReceipt {
            operation_id: operation.to_owned(),
            mutation_key: "mutation-1".to_owned(),
            canonical_issue_id: issue.to_owned(),
            applied_at_unix_ms: 1,
        }
    }

    #[test]
    fn authorization_requires_every_exact_confirmation() {
        let digest = "a".repeat(64);
        assert!(authorize(
            true,
            "account-1",
            &digest,
            "account-1",
            &digest,
            CONFIRMATION_PHRASE
        )
        .is_ok());
        assert!(authorize(
            false,
            "account-1",
            &digest,
            "account-1",
            &digest,
            CONFIRMATION_PHRASE
        )
        .is_err());
        assert!(authorize(
            true,
            "account-1",
            &digest,
            "account-2",
            &digest,
            CONFIRMATION_PHRASE
        )
        .is_err());
        assert!(authorize(
            true,
            "account-1",
            &digest,
            "account-1",
            &"b".repeat(64),
            CONFIRMATION_PHRASE
        )
        .is_err());
        assert!(authorize(
            true,
            "account-1",
            &digest,
            "account-1",
            &digest,
            "apply"
        )
        .is_err());
    }

    #[test]
    fn digest_is_byte_exact_and_lowercase() {
        assert_eq!(
            sha256_hex(br#"{"plan":1}"#),
            "e9779b64937d0c7ee6f3cf2fc51b838b2ce12e28a9fbb8cbbe6a5b7b876e1a7f"
        );
        assert!(is_lower_hex_digest(&"0".repeat(64)));
        assert!(!is_lower_hex_digest(&"A".repeat(64)));
        assert!(!is_lower_hex_digest("abc"));
    }

    #[test]
    fn account_fingerprint_supports_preview_and_plan_shapes() {
        let direct = serde_json::json!({"account_fingerprint": "account-1"});
        let nested = serde_json::json!({"plan": {"account_fingerprint": "account-2"}});
        assert_eq!(extract_account_fingerprint(&direct), Some("account-1"));
        assert_eq!(extract_account_fingerprint(&nested), Some("account-2"));
    }

    #[test]
    fn receipt_rerun_is_a_noop() {
        let path = temp_path("rerun.json");
        assert_eq!(
            record_receipt(&path, receipt("operation-1", "DEN-1")).unwrap(),
            ReceiptOutcome::Recorded
        );
        assert_eq!(
            record_receipt(&path, receipt("operation-1", "DEN-1")).unwrap(),
            ReceiptOutcome::AlreadyRecorded
        );
        let ledger = load_ledger(&path).unwrap();
        assert_eq!(ledger.generation, 1);
        assert_eq!(ledger.records.len(), 1);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn conflicting_receipt_fails_closed() {
        let path = temp_path("conflict.json");
        record_receipt(&path, receipt("operation-1", "DEN-1")).unwrap();
        assert!(matches!(
            record_receipt(&path, receipt("operation-1", "DEN-2")),
            Err(GuardError::ReceiptConflict)
        ));
        let ledger = load_ledger(&path).unwrap();
        assert_eq!(ledger.records["operation-1"].canonical_issue_id, "DEN-1");
        let _ = fs::remove_file(path);
    }

    #[test]
    fn unknown_receipt_fields_are_refused() {
        let path = temp_path("unknown.json");
        fs::write(
            &path,
            r#"{"schema_version":1,"generation":0,"records":{},"unexpected":true}"#,
        )
        .unwrap();
        assert!(load_ledger(&path).is_err());
        let _ = fs::remove_file(path);
    }

    #[test]
    fn existing_lock_refuses_concurrent_local_apply() {
        let path = temp_path("locked.json");
        let lock = ReceiptLock::acquire(&path).unwrap();
        assert!(matches!(
            ReceiptLock::acquire(&path),
            Err(GuardError::ReceiptLocked)
        ));
        drop(lock);
        assert!(ReceiptLock::acquire(&path).is_ok());
    }

    #[test]
    fn identifiers_are_bounded_and_safe() {
        assert!(validate_identifier("DEN-1610:operation/1", "operation").is_ok());
        assert!(validate_identifier("contains whitespace", "operation").is_err());
        assert!(validate_identifier(&"x".repeat(257), "operation").is_err());
    }
}
