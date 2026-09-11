use std::fs;
use std::path::Path;

use serde_json::json;
use toml::Value;

use crate::model::{CommandReport, Finding};

const CLI_CONTRACT: &str = ".cli-flags.toml";
const MAX_INPUT_BYTES: u64 = 2 * 1024 * 1024;

pub(crate) fn audit_cli_secret_flag_hygiene(root: &Path, report: &mut CommandReport) {
    let path = root.join(CLI_CONTRACT);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return,
        Err(error) => {
            report.push(
                Finding::error(
                    "cli-secret-contract-unreadable",
                    format!(".cli-flags.toml metadata could not be inspected: {error}"),
                )
                .with_target(CLI_CONTRACT),
            );
            return;
        }
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() || metadata.len() > MAX_INPUT_BYTES {
        report.push(
            Finding::error(
                "cli-secret-contract-unsafe",
                ".cli-flags.toml must be a bounded regular file before secret/argv hygiene can be trusted",
            )
            .with_target(CLI_CONTRACT),
        );
        return;
    }
    let text = match fs::read_to_string(&path) {
        Ok(text) => text,
        Err(error) => {
            report.push(
                Finding::error(
                    "cli-secret-contract-unreadable",
                    format!(".cli-flags.toml could not be read as UTF-8: {error}"),
                )
                .with_target(CLI_CONTRACT),
            );
            return;
        }
    };
    let document = match toml::from_str::<Value>(&text) {
        Ok(document) => document,
        Err(error) => {
            report.push(
                Finding::error(
                    "cli-secret-contract-invalid",
                    format!(".cli-flags.toml could not be parsed for secret/argv hygiene: {error}"),
                )
                .with_target(CLI_CONTRACT),
            );
            return;
        }
    };
    let mut public_flag_count = 0_u64;
    let mut secret_flag_count = 0_u64;
    inspect_value(
        &document,
        &mut Vec::new(),
        report,
        &mut public_flag_count,
        &mut secret_flag_count,
    );
    report.insert_metadata("cliPublicFlagCount", json!(public_flag_count));
    report.insert_metadata("cliSecretClassPublicFlagCount", json!(secret_flag_count));
}

fn inspect_value(
    value: &Value,
    path: &mut Vec<String>,
    report: &mut CommandReport,
    public_flag_count: &mut u64,
    secret_flag_count: &mut u64,
) {
    match value {
        Value::Table(table) => {
            if path.len() >= 2 && path[path.len() - 2] == "flags" {
                *public_flag_count += 1;
                let flag_name = path.last().map(String::as_str).unwrap_or_default();
                let env_key = table.get("env").and_then(Value::as_str);
                if secret_like(flag_name) || env_key.is_some_and(secret_like) {
                    *secret_flag_count += 1;
                    let mut finding = Finding::error(
                        "cli-secret-public-flag",
                        "credential-class configuration must remain environment/secret-store only and must not be exposed as a public CLI flag",
                    )
                    .with_target(CLI_CONTRACT)
                    .with_detail("flagPath", json!(path.join(".")))
                    .with_detail("hasDefault", json!(table.contains_key("default")));
                    if let Some(env_key) = env_key {
                        finding = finding.with_detail("envKey", json!(env_key));
                    }
                    report.push(finding);
                }
            }
            for (key, child) in table {
                path.push(key.clone());
                inspect_value(child, path, report, public_flag_count, secret_flag_count);
                path.pop();
            }
        }
        Value::Array(values) => {
            for child in values {
                inspect_value(child, path, report, public_flag_count, secret_flag_count);
            }
        }
        _ => {}
    }
}

fn secret_like(value: &str) -> bool {
    let words = value
        .split(|ch: char| !ch.is_ascii_alphanumeric())
        .filter(|word| !word.is_empty())
        .map(str::to_ascii_lowercase)
        .collect::<Vec<_>>();

    for (index, word) in words.iter().enumerate() {
        if matches!(
            word.as_str(),
            "secret" | "password" | "passwd" | "credential" | "credentials"
        ) && !descriptor_suffix(&words, index + 1)
        {
            return true;
        }
    }
    if words.len() == 1 && matches!(words[0].as_str(), "token" | "apikey" | "hmac") {
        return true;
    }
    for (index, pair) in words.windows(2).enumerate() {
        let credential_pair = matches!(
            (pair[0].as_str(), pair[1].as_str()),
            ("api", "key")
                | ("auth", "token")
                | ("access", "token")
                | ("refresh", "token")
                | ("bearer", "token")
                | ("session", "token")
                | ("private", "key")
                | ("hmac", "key")
                | ("signing", "key")
                | ("client", "secret")
        );
        if credential_pair && !descriptor_suffix(&words, index + 2) {
            return true;
        }
    }
    false
}

fn descriptor_suffix(words: &[String], start: usize) -> bool {
    start < words.len()
        && words[start..]
            .iter()
            .all(|word| is_credential_descriptor(word))
}

fn is_credential_descriptor(word: &str) -> bool {
    matches!(
        word,
        "ttl"
            | "sec"
            | "secs"
            | "second"
            | "seconds"
            | "duration"
            | "lifetime"
            | "expiry"
            | "expires"
            | "expiration"
            | "age"
            | "version"
            | "kid"
            | "id"
            | "name"
            | "algorithm"
            | "alg"
            | "format"
            | "length"
            | "bytes"
            | "bits"
            | "enabled"
            | "mode"
            | "policy"
            | "count"
            | "limit"
    )
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::audit_cli_secret_flag_hygiene;
    use crate::model::CommandReport;

    fn run(config: &str) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        fs::write(root.path().join(".cli-flags.toml"), config).expect("flags contract");
        let mut report = CommandReport::new("cli secret hygiene test");
        audit_cli_secret_flag_hygiene(root.path(), &mut report);
        report.finalize()
    }

    fn has(report: &CommandReport, code: &str) -> bool {
        report.findings.iter().any(|finding| finding.code == code)
    }

    #[test]
    fn auth_token_must_not_be_a_public_flag() {
        let report = run(
            "[flags.auth-token]\nenv = \"FANWAAVE_AUTH_TOKEN\"\ntype = \"string\"\ndefault = \"do-not-echo-this\"\n",
        );
        assert!(has(&report, "cli-secret-public-flag"));
        assert!(!format!("{report:?}").contains("do-not-echo-this"));
    }

    #[test]
    fn token_bucket_policy_is_not_a_credential_false_positive() {
        let report = run(
            "[flags.token-bucket-policy]\nenv = \"TOKEN_BUCKET_POLICY\"\ntype = \"string\"\n",
        );
        assert!(!has(&report, "cli-secret-public-flag"));
    }

    #[test]
    fn credential_metadata_is_not_treated_as_the_credential_value() {
        let report = run(
            "[flags.access-token-ttl-secs]\nenv = \"AUTH_ACCESS_TOKEN_TTL_SECS\"\ntype = \"integer\"\n\
             [flags.refresh-token-ttl-secs]\nenv = \"AUTH_REFRESH_TOKEN_TTL_SECS\"\ntype = \"integer\"\n\
             [flags.password-policy]\nenv = \"PASSWORD_POLICY\"\ntype = \"string\"\n\
             [flags.api-key-version]\nenv = \"PROVIDER_API_KEY_VERSION\"\ntype = \"string\"\n",
        );
        assert!(!has(&report, "cli-secret-public-flag"));
    }

    #[test]
    fn env_ignore_keeps_secret_environment_only() {
        let report = run(
            "[env]\nignore = [\"FANWAAVE_AUTH_TOKEN\", \"DATABASE_PASSWORD\"]\n[flags.api-base]\nenv = \"FANWAAVE_API_BASE_URL\"\ntype = \"string\"\n",
        );
        assert!(!has(&report, "cli-secret-public-flag"));
    }

    #[test]
    fn command_scoped_api_key_is_rejected() {
        let report = run(
            "[commands.push]\nhelp = \"send\"\n[commands.push.flags.api-key]\nenv = \"PROVIDER_API_KEY\"\ntype = \"string\"\n",
        );
        assert!(has(&report, "cli-secret-public-flag"));
    }
}
