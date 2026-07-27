use regex::{Captures, Regex};
use serde::Serialize;
use serde_json::Value;

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
                    regex: Regex::new(
                        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                    )?,
                },
                SecretPattern {
                    name: "named_secret",
                    regex: Regex::new(
                        r#"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)\s*[:=]\s*["']?[^\s,"']{8,}"#,
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
                for value in values.values_mut() {
                    self.visit(value, redact, report);
                }
            }
            Value::Null | Value::Bool(_) | Value::Number(_) => {}
        }
    }
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
        assert!(!value.to_string().contains("ghp_"));
    }
}
