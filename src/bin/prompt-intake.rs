use std::{fs, path::PathBuf};

use ai_agent_coordinator::prompt_intake::{
    build_dry_run_report, ProjectCatalog, PromptExport,
};
use anyhow::{bail, Context};
use chrono::{DateTime, Utc};
use clap::{Parser, ValueEnum};

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
#[value(rename_all = "kebab-case")]
enum Mode {
    DryRun,
    Apply,
}

#[derive(Debug, Parser)]
#[command(
    name = "prompt-intake",
    author,
    version,
    about = "Build a deterministic, mutation-free ChatGPT prompt reconciliation plan"
)]
struct Args {
    /// Authorized JSON export containing user-visible prompt records.
    #[arg(long)]
    input: PathBuf,

    /// Optional repository-to-Linear-project catalog in JSON format.
    #[arg(long)]
    catalog: Option<PathBuf>,

    /// Write the report to this path instead of stdout.
    #[arg(long)]
    output: Option<PathBuf>,

    /// Rolling window ending at --now or the current UTC time.
    #[arg(long, default_value_t = 240)]
    window_hours: i64,

    /// RFC3339 timestamp used as the deterministic end of the window.
    #[arg(long)]
    now: Option<String>,

    /// Apply is intentionally blocked until connector-backed mutation is implemented.
    #[arg(long, value_enum, default_value = "dry-run")]
    mode: Mode,
}

fn main() -> anyhow::Result<()> {
    let args = Args::parse();
    if args.mode == Mode::Apply {
        bail!(
            "apply mode is not implemented; refusing Linear or GitHub mutation. Run with --mode dry-run"
        );
    }

    let export: PromptExport = read_json(&args.input, "prompt export")?;
    let catalog = match args.catalog.as_ref() {
        Some(path) => read_json(path, "project catalog")?,
        None => ProjectCatalog::default(),
    };
    let now = match args.now.as_deref() {
        Some(value) => DateTime::parse_from_rfc3339(value)
            .with_context(|| format!("invalid --now RFC3339 timestamp: {value}"))?
            .with_timezone(&Utc),
        None => Utc::now(),
    };
    let report = build_dry_run_report(&export, &catalog, now, args.window_hours)?;
    let output = serde_json::to_string_pretty(&report).context("failed to serialize report")?;

    if let Some(path) = args.output {
        fs::write(&path, format!("{output}\n"))
            .with_context(|| format!("failed to write report to {}", path.display()))?;
    } else {
        println!("{output}");
    }
    Ok(())
}

fn read_json<T>(path: &PathBuf, label: &str) -> anyhow::Result<T>
where
    T: serde::de::DeserializeOwned,
{
    let bytes = fs::read(path)
        .with_context(|| format!("failed to read {label} from {}", path.display()))?;
    serde_json::from_slice(&bytes)
        .with_context(|| format!("failed to parse {label} JSON from {}", path.display()))
}
