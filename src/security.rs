use regex::{Captures, Regex};
use serde::Serialize;
use serde_json::Value;

const SENSITIVE_JSON_KEY_CATEGORY: &str = "sensitive_json_key";
const SENSITIVE_JSON_KEY_EXACT: &[&str] = &[
    "authorization",
    "cookie",
    "password",
    "passwd",
    "privatekey",
    "secret",
    "setcookie",
    "token",
];
const SENSITIVE_JSON_KEY_SUFFIXES: &[&str] = &[
    "apikey",
    "accesstoken",
    "authtoken",
    "bearertoken",
    "clientsecret",
    "privatekey",
    "refreshtoken",
    "webhooksecret",
];

#[derive(Clone)]
pub struct SecretScanner {
    patterns: Vec<SecretPattern>,
}

#[derive(Clone)]
struct SecretPattern {
    name: &'static str,
    regex: Regex,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct SecretScanReport {
    pub matches: usize,
    pub categories: Vec<String>,
}

impl SecretScanner {
    pub fn new() -> Result<Self, regex::Error> {
        Ok(Self {
            patterns: vec![
                SecretPattern {
                    name: "github_token",
                    regex: Regex::new(r"gh[pousr]_[A-Za-z0-9_]{20,255}")?,
                },
                SecretPattern {
                    name: "openai_style_key",
                    regex: Regex::new(r"sk-[A-Za-z0-9_-]{16,255}")?,
                },
                SecretPattern {
                    name: "aws_access_key",
                    regex: Regex::new(r"AKIA[0-9A-Z]{16}")?,
                },
                SecretPattern {
                    name: "private_key",
                    regex: Regex::new(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")?,
                },
                SecretPattern {
                    name: "named_secret",
                    regex: Regex::new(
                        r#"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|secret)\s*[:=]\s*["']?[^\s,"']{8,}"#,
                    )?,
                },
            ],
        })
    }

    pub fn scan_and_redact(&self, value: &mut Value, redact: bool) -> SecretScanReport {
        let mut report = SecretScanReport::default();
        self.visit(value, redact, &mut report);
        report.categories.sort();
        report.categories.dedup();
        report
    }

    fn visit(&self, value: &mut Value, redact: bool, report: &mut SecretScanReport) {
        match value {
            Value::String(text) => {
                let mut current = text.clone();
                for pattern in &self.patterns {
                    let count = pattern.regex.find_iter(&current).count();
                    if count == 0 {
                        continue;
                    }
                    report.matches += count;
                    report.categories.push(pattern.name.to_owned());
                    if redact {
                        current = pattern
                            .regex
                            .replace_all(&current, |_captures: &Captures<'_>| {
                                format!("[REDACTED:{}]", pattern.name)
                            })
                            .into_owned();
                    }
                }
                if redact {
                    *text = current;
                }
            }
            Value::Array(values) => {
                for value in values {
                    self.visit(value, redact, report);
                }
            }
            Value::Object(values) => {
                for (key, value) in values.iter_mut() {
                    if is_sensitive_json_key(key) && !value.is_null() {
                        report.matches += 1;
                        report
                            .categories
                            .push(SENSITIVE_JSON_KEY_CATEGORY.to_owned());
                        if redact {
                            *value = Value::String(format!(
                                "[REDACTED:{SENSITIVE_JSON_KEY_CATEGORY}]"
                            ));
                        }
                        continue;
                    }
                    self.visit(value, redact, report);
                }
            }
            Value::Null | Value::Bool(_) | Value::Number(_) => {}
        }
    }
}

fn is_sensitive_json_key(key: &str) -> bool {
    let normalized = key
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect::<String>();

    SENSITIVE_JSON_KEY_EXACT.contains(&normalized.as_str())
        || SENSITIVE_JSON_KEY_SUFFIXES
            .iter()
            .any(|suffix| normalized.ends_with(suffix))
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::SecretScanner;

    #[test]
    fn redacts_known_secret_shapes() {
        let scanner = SecretScanner::new().unwrap();
        let mut value = json!({
            "message": "token=ghp_abcdefghijklmnopqrstuvwxyz1234567890"
        });
        let report = scanner.scan_and_redact(&mut value, true);
        assert_eq!(report.matches, 2);
        assert_eq!(
            report.categories,
            vec!["github_token".to_owned(), "named_secret".to_owned()]
        );
        assert!(!value.to_string().contains("ghp_"));
    }

    #[test]
    fn redacts_generic_named_token_assignments() {
        let scanner = SecretScanner::new().unwrap();
        let mut value = json!({
            "message": "token=abcdefghijk123456"
        });
        let report = scanner.scan_and_redact(&mut value, true);
        assert_eq!(report.matches, 1);
        assert_eq!(report.categories, vec!["named_secret".to_owned()]);
        assert!(!value.to_string().contains("abcdefghijk123456"));
    }

    #[test]
    fn redacts_values_under_sensitive_json_keys_even_without_token_shapes() {
        let scanner = SecretScanner::new().unwrap();
        let mut value = json!({
            "client_secret": "short",
            "nested": {
                "authorization": 7,
                "cookie": true,
                "token_count": 42
            }
        });

        let report = scanner.scan_and_redact(&mut value, true);

        assert_eq!(report.matches, 3);
        assert_eq!(report.categories, vec!["sensitive_json_key".to_owned()]);
        assert_eq!(value["client_secret"], "[REDACTED:sensitive_json_key]");
        assert_eq!(
            value["nested"]["authorization"],
            "[REDACTED:sensitive_json_key]"
        );
        assert_eq!(value["nested"]["cookie"], "[REDACTED:sensitive_json_key]");
        assert_eq!(value["nested"]["token_count"], 42);
    }
}
