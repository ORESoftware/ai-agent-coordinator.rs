use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use proc_macro2::Span;
use regex::Regex;
use serde::Serialize;
use syn::spanned::Spanned;
use syn::visit::Visit;
use syn::{Expr, ExprCall, Lit, Macro, Path as SynPath};

const SCHEMA: &str = "ores.code-config-audit.v1";
const MAX_FILES: usize = 4_096;
const MAX_BYTES: u64 = 64 * 1024 * 1024;
const MAX_FILE_BYTES: u64 = 2 * 1024 * 1024;

const SKIP_DIRS: &[&str] = &[
    ".git",
    ".dart_tool",
    ".idea",
    ".next",
    ".turbo",
    ".zed",
    "build",
    "coverage",
    "dist",
    "generated",
    "node_modules",
    "target",
    "vendor",
    "zed_modules",
];

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "camelCase")]
struct Event {
    kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    value: Option<String>,
    line: u32,
    column: u32,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct FileReceipt {
    path: String,
    syntax_valid: bool,
    regex_hits: Vec<Event>,
    ast_events: Vec<Event>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Receipt {
    schema_version: &'static str,
    language: &'static str,
    parser: &'static str,
    parser_version: &'static str,
    files: Vec<FileReceipt>,
}

fn main() {
    match run() {
        Ok(receipt) => match serde_json::to_string(&receipt) {
            Ok(json) => println!("{json}"),
            Err(_) => fail("receipt serialization failed"),
        },
        Err(message) => fail(&message),
    }
}

fn run() -> Result<Receipt, String> {
    let root = parse_args()?;
    let root = fs::canonicalize(root).map_err(|_| "root could not be resolved".to_owned())?;
    if !root.is_dir() {
        return Err("root must be a directory".to_owned());
    }

    let files = inventory(&root)?;
    let mut receipts = Vec::with_capacity(files.len());
    for file in files {
        receipts.push(inspect_file(&root, &file)?);
    }
    receipts.sort_by(|left, right| left.path.cmp(&right.path));

    Ok(Receipt {
        schema_version: SCHEMA,
        language: "rust",
        parser: "syn",
        parser_version: "3.0.5",
        files: receipts,
    })
}

fn parse_args() -> Result<PathBuf, String> {
    let mut args = std::env::args().skip(1);
    let mut root = None;
    let mut format = None;
    while let Some(arg) = args.next() {
        let value = args
            .next()
            .ok_or_else(|| "every option requires one value".to_owned())?;
        match arg.as_str() {
            "--root" => root = Some(PathBuf::from(value)),
            "--format" => format = Some(value),
            _ => return Err("unknown adapter option".to_owned()),
        }
    }
    if format.as_deref() != Some("json") {
        return Err("only --format json is supported".to_owned());
    }
    root.ok_or_else(|| "--root is required".to_owned())
}

fn inventory(root: &Path) -> Result<Vec<PathBuf>, String> {
    fn walk(
        root: &Path,
        dir: &Path,
        files: &mut Vec<PathBuf>,
        bytes: &mut u64,
    ) -> Result<(), String> {
        let mut entries = fs::read_dir(dir)
            .map_err(|_| "source directory could not be read".to_owned())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| "source directory entry could not be read".to_owned())?;
        entries.sort_by_key(|entry| entry.file_name());

        for entry in entries {
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path)
                .map_err(|_| "source metadata could not be read".to_owned())?;
            if metadata.file_type().is_symlink() {
                continue;
            }
            if metadata.is_dir() {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if path != root && SKIP_DIRS.contains(&name.as_ref()) {
                    continue;
                }
                walk(root, &path, files, bytes)?;
                continue;
            }
            if !metadata.is_file()
                || path.extension().and_then(|value| value.to_str()) != Some("rs")
            {
                continue;
            }
            if metadata.len() > MAX_FILE_BYTES {
                return Err("source file exceeds bound".to_owned());
            }
            *bytes = bytes.saturating_add(metadata.len());
            if *bytes > MAX_BYTES || files.len() >= MAX_FILES {
                return Err("source inventory exceeds bound".to_owned());
            }
            files.push(path);
        }
        Ok(())
    }

    let mut files = Vec::new();
    let mut bytes = 0;
    walk(root, root, &mut files, &mut bytes)?;
    files.sort();
    Ok(files)
}

fn inspect_file(root: &Path, file: &Path) -> Result<FileReceipt, String> {
    let source = fs::read_to_string(file).map_err(|_| "source file is not UTF-8".to_owned())?;
    let relative = relative(root, file)?;
    let regex_hits = regex_lane(&source)?;
    match syn::parse_file(&source) {
        Ok(parsed) => {
            let mut visitor = ConfigVisitor::new();
            visitor.visit_file(&parsed);
            Ok(FileReceipt {
                path: relative,
                syntax_valid: true,
                regex_hits,
                ast_events: visitor.finish(),
            })
        }
        Err(_) => Ok(FileReceipt {
            path: relative,
            syntax_valid: false,
            regex_hits,
            ast_events: Vec::new(),
        }),
    }
}

fn regex_lane(source: &str) -> Result<Vec<Event>, String> {
    let config = Regex::new(r#"[\"']([^\"'\n]*\.toml)[\"']"#)
        .map_err(|_| "config regex did not compile".to_owned())?;
    let env = Regex::new(
        r"\b(?:std::env::(?:var|var_os)|env::(?:var|var_os)|env!|option_env!)\b",
    )
    .map_err(|_| "env regex did not compile".to_owned())?;
    let toml = Regex::new(r"\btoml::(?:from_str|from_slice|de::from_str)\b")
        .map_err(|_| "TOML regex did not compile".to_owned())?;

    let mut events = BTreeSet::new();
    for captures in config.captures_iter(source) {
        let Some(value) = captures.get(1) else {
            continue;
        };
        let (line, column) = line_column(source, value.start());
        events.insert(Event {
            kind: "config-literal".to_owned(),
            value: Some(value.as_str().to_owned()),
            line,
            column,
        });
    }
    for found in env.find_iter(source) {
        let (line, column) = line_column(source, found.start());
        events.insert(Event {
            kind: "env-api".to_owned(),
            value: Some(found.as_str().to_owned()),
            line,
            column,
        });
    }
    for found in toml.find_iter(source) {
        let (line, column) = line_column(source, found.start());
        events.insert(Event {
            kind: "toml-api".to_owned(),
            value: Some(found.as_str().to_owned()),
            line,
            column,
        });
    }
    Ok(events.into_iter().collect())
}

struct ConfigVisitor {
    events: BTreeSet<Event>,
}

impl ConfigVisitor {
    fn new() -> Self {
        Self {
            events: BTreeSet::new(),
        }
    }

    fn finish(self) -> Vec<Event> {
        self.events.into_iter().collect()
    }

    fn push(&mut self, kind: &str, value: Option<String>, span: Span) {
        let start = span.start();
        self.events.insert(Event {
            kind: kind.to_owned(),
            value,
            line: u32::try_from(start.line).unwrap_or(u32::MAX),
            column: u32::try_from(start.column.saturating_add(1)).unwrap_or(u32::MAX),
        });
    }
}

impl<'ast> Visit<'ast> for ConfigVisitor {
    fn visit_expr_call(&mut self, node: &'ast ExprCall) {
        if let Expr::Path(function) = node.func.as_ref() {
            let function_name = path_name(&function.path);
            let first = first_string_argument(node);
            if matches!(
                function_name.as_str(),
                "std::env::var" | "std::env::var_os" | "env::var" | "env::var_os"
            ) {
                self.push("env-read", first, node.span());
            }
            if matches!(
                function_name.as_str(),
                "std::fs::read_to_string"
                    | "std::fs::read"
                    | "fs::read_to_string"
                    | "fs::read"
            ) && first.as_deref().is_some_and(|value| value.ends_with(".toml"))
            {
                self.push("config-read", first, node.span());
            }
            if matches!(
                function_name.as_str(),
                "toml::from_str" | "toml::from_slice" | "toml::de::from_str"
            ) {
                self.push("toml-parse", None, node.span());
            }
        }
        syn::visit::visit_expr_call(self, node);
    }

    fn visit_macro(&mut self, node: &'ast Macro) {
        let name = path_name(&node.path);
        if matches!(name.as_str(), "include_str" | "include_bytes") {
            if let Ok(value) = syn::parse2::<syn::LitStr>(node.tokens.clone()) {
                let value = value.value();
                if value.ends_with(".toml") {
                    self.push("config-read", Some(value), node.span());
                }
            }
        }
        if matches!(name.as_str(), "env" | "option_env") {
            if let Ok(value) = syn::parse2::<syn::LitStr>(node.tokens.clone()) {
                self.push("env-read", Some(value.value()), node.span());
            }
        }
        syn::visit::visit_macro(self, node);
    }
}

fn first_string_argument(node: &ExprCall) -> Option<String> {
    match node.args.first()? {
        Expr::Lit(literal) => match &literal.lit {
            Lit::Str(value) => Some(value.value()),
            _ => None,
        },
        _ => None,
    }
}

fn path_name(path: &SynPath) -> String {
    path.segments
        .iter()
        .map(|segment| segment.ident.to_string())
        .collect::<Vec<_>>()
        .join("::")
}

fn line_column(source: &str, offset: usize) -> (u32, u32) {
    let prefix = &source[..offset.min(source.len())];
    let line = prefix.bytes().filter(|byte| *byte == b'\n').count() + 1;
    let column = prefix
        .rsplit_once('\n')
        .map_or(prefix.len() + 1, |(_, tail)| tail.len() + 1);
    (
        u32::try_from(line).unwrap_or(u32::MAX),
        u32::try_from(column).unwrap_or(u32::MAX),
    )
}

fn relative(root: &Path, file: &Path) -> Result<String, String> {
    let relative = file
        .strip_prefix(root)
        .map_err(|_| "source escaped root".to_owned())?;
    let value = relative.to_string_lossy().replace('\\', "/");
    if value.is_empty() || value.starts_with("../") || value.starts_with('/') {
        return Err("source escaped root".to_owned());
    }
    Ok(value)
}

fn fail(message: &str) -> ! {
    eprintln!("{message}");
    std::process::exit(2)
}

#[cfg(test)]
mod tests {
    use syn::visit::Visit;

    use super::ConfigVisitor;

    #[test]
    fn repeated_identical_env_reads_keep_distinct_ast_positions() {
        let source = r#"
fn load() {
    let _ = std::env::var("REDIS_URL");
    let _ = std::env::var("REDIS_URL");
}
"#;
        let parsed = syn::parse_file(source).expect("valid Rust fixture");
        let mut visitor = ConfigVisitor::new();
        visitor.visit_file(&parsed);
        let events = visitor
            .finish()
            .into_iter()
            .filter(|event| event.kind == "env-read" && event.value.as_deref() == Some("REDIS_URL"))
            .collect::<Vec<_>>();

        assert_eq!(events.len(), 2);
        assert_eq!(events[0].line, 3);
        assert_eq!(events[1].line, 4);
        assert_ne!((events[0].line, events[0].column), (events[1].line, events[1].column));
    }
}
