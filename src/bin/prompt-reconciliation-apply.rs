use std::{collections::BTreeSet, fs, path::PathBuf};

use ai_agent_coordinator::{
    prompt_reconciliation::ReconciliationPlan,
    prompt_reconciliation_adapters::{
        ApplyAuthorization, ApplyReport, GithubEvidenceClient, GithubEvidenceConfig,
        LinearReconciliationClient, LinearReconciliationConfig, ResolvedGithubEvidence,
    },
};
use anyhow::Context as _;
use clap::Parser;
use serde::Serialize;

#[derive(Debug, Parser)]
#[command(
    name = "prompt-reconciliation-apply",
    author,
    version,
    about = "Apply an exact reviewed prompt-reconciliation plan through guarded adapters"
)]
struct Args {
    /// Exact JSON plan produced by the reconciliation planner.
    #[arg(long)]
    plan: PathBuf,

    /// Exact reviewed account fingerprint from the plan.
    #[arg(long)]
    account: String,

    /// Lowercase SHA-256 digest of the exact plan bytes.
    #[arg(long)]
    digest: String,

    /// Exact confirmation phrase: APPLY PROMPT RECONCILIATION.
    #[arg(long)]
    confirmation: String,

    /// Exercise all reads and policy checks but perform no Linear mutation.
    #[arg(long, default_value_t = false)]
    dry_run: bool,

    /// Resolve every supported GitHub commit and pull-request evidence link before Linear work.
    #[arg(long, default_value_t = false)]
    validate_github_evidence: bool,

    /// Write the bounded report to a file instead of stdout.
    #[arg(long)]
    output: Option<PathBuf>,
}

#[derive(Debug, Serialize)]
struct CommandReport {
    github_evidence: Vec<ResolvedGithubEvidence>,
    linear: ApplyReport,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args = Args::parse();
    let bytes = fs::read(&args.plan)
        .with_context(|| format!("failed to read plan from {}", args.plan.display()))?;
    let plan: ReconciliationPlan = serde_json::from_slice(&bytes)
        .with_context(|| format!("failed to parse plan from {}", args.plan.display()))?;
    let authorization = ApplyAuthorization::verify(
        &bytes,
        &plan,
        &args.account,
        &args.digest,
        &args.confirmation,
        !args.dry_run,
    )?;

    let github_evidence = if args.validate_github_evidence {
        resolve_github_evidence(&plan).await?
    } else {
        Vec::new()
    };

    let linear = LinearReconciliationClient::new(LinearReconciliationConfig::from_env(
        args.dry_run,
    )?)?
    .apply_plan(&bytes, &plan, &authorization)
    .await?;
    let report = CommandReport {
        github_evidence,
        linear,
    };
    let output = serde_json::to_string_pretty(&report).context("failed to serialize apply report")?;
    if let Some(path) = args.output {
        fs::write(&path, format!("{output}\n"))
            .with_context(|| format!("failed to write report to {}", path.display()))?;
    } else {
        println!("{output}");
    }
    Ok(())
}

async fn resolve_github_evidence(
    plan: &ReconciliationPlan,
) -> anyhow::Result<Vec<ResolvedGithubEvidence>> {
    let client = GithubEvidenceClient::new(GithubEvidenceConfig::from_env()?)?;
    let links = plan
        .prompts
        .iter()
        .flat_map(|prompt| prompt.evidence_links.iter())
        .map(String::as_str)
        .collect::<BTreeSet<_>>();
    let mut resolved = Vec::with_capacity(links.len());
    for link in links {
        resolved.push(client.resolve_link(link).await?);
    }
    resolved.sort_by(|left, right| left.canonical_url.cmp(&right.canonical_url));
    Ok(resolved)
}
