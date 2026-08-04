use sha2::{Digest, Sha256};

use crate::prompt_intake::PromptDecision;

use super::model::*;

pub(super) fn build_mutation(
    decision: &PromptDecision,
    project: &str,
    issue_id: Option<&str>,
    kind: LinearMutationKind,
    evidence_links: &[String],
    residual_operational_work: bool,
) -> LinearMutationPlan {
    let kind_tag = match kind {
        LinearMutationKind::Amend => "amend",
        LinearMutationKind::Create => "create",
    };
    let target = issue_id.unwrap_or(project);
    let operation_id = sha256_hex(
        format!(
            "prompt-reconciliation:v{PLAN_SCHEMA_VERSION}:{}:{kind_tag}:{target}",
            decision.mutation_key
        )
        .as_bytes(),
    );
    let title_seed = if decision.title_summary.trim().is_empty() {
        decision.prompt_summary.trim()
    } else {
        decision.title_summary.trim()
    };
    let title = format!("[Prompt intake] {title_seed}");
    let evidence = if evidence_links.is_empty() {
        "- No resolvable GitHub landing link was supplied.".to_owned()
    } else {
        evidence_links
            .iter()
            .map(|link| format!("- {link}"))
            .collect::<Vec<_>>()
            .join("\n")
    };
    let residual = if residual_operational_work {
        "Residual operational work remains and must stay open after implementation evidence is recorded."
    } else {
        "No separate residual operational work was declared by the evidence collector."
    };
    let body = format!(
        "Prompt-intake reconciliation plan (schema v{PLAN_SCHEMA_VERSION}).\n\n\
         - Source fingerprint: `{}`\n\
         - Scope signature: `{}`\n\
         - Mutation key: `{}`\n\
         - Bounded prompt summary: {}\n\
         - Residual-work policy: {}\n\n\
         Resolvable GitHub evidence:\n{}\n\n\
         This plan is mutation-free until an explicitly authorized apply worker records an applied receipt for operation `{}`.",
        decision.source_identity,
        decision.scope_signature,
        decision.mutation_key,
        decision.prompt_summary,
        residual,
        evidence,
        operation_id,
    );

    LinearMutationPlan {
        operation_id: operation_id.clone(),
        idempotency_key: operation_id,
        kind,
        project: project.to_owned(),
        issue_id: issue_id.map(str::to_owned),
        title,
        body,
    }
}

pub(super) fn plan_without_mutation(
    decision: &PromptDecision,
    action: PlanAction,
    mut reasons: Vec<PlanReason>,
    mut evidence_links: Vec<String>,
) -> PromptReconciliationPlan {
    sort_dedup(&mut reasons);
    evidence_links.sort();
    evidence_links.dedup();
    PromptReconciliationPlan {
        source_identity: decision.source_identity.clone(),
        mutation_key: decision.mutation_key.clone(),
        scope_signature: decision.scope_signature.clone(),
        action,
        reasons,
        mutation: None,
        evidence_links,
    }
}

pub(super) fn count_action(prompts: &[PromptReconciliationPlan], action: PlanAction) -> usize {
    prompts.iter().filter(|item| item.action == action).count()
}

pub(super) fn sort_dedup<T: Ord>(items: &mut Vec<T>) {
    items.sort();
    items.dedup();
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}
