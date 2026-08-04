use chrono::{Duration, TimeZone, Utc};

use super::*;
use crate::prompt_intake::{
    PromptClassification, PromptDecision, PromptIntakeCounts, PromptIntakeReport,
    PromptIntakeWindow, ProjectResolution, ProjectResolutionState,
};

fn report(decisions: Vec<PromptDecision>) -> PromptIntakeReport {
    let now = Utc.with_ymd_and_hms(2026, 8, 4, 5, 0, 0).unwrap();
    PromptIntakeReport {
        schema_version: 1,
        mode: "dry_run",
        generated_at: now.to_owned(),
        account_fingerprint: "account-fingerprint".to_owned(),
        window: PromptIntakeWindow {
            start: now.to_owned() - Duration::hours(240),
            end: now,
            hours: 240,
        },
        counts: PromptIntakeCounts {
            input_records: decisions.len(),
            within_window: decisions.len(),
            outside_window: 0,
            actionable: decisions.iter().filter(|item| item.actionable).count(),
            excluded: decisions.iter().filter(|item| !item.actionable).count(),
            needs_review: decisions.iter().filter(|item| item.needs_review).count(),
            duplicate_groups: 0,
            refinement_groups: 0,
        },
        decisions,
        duplicate_groups: Vec::new(),
        refinement_groups: Vec::new(),
    }
}

fn decision(key: &str, repository: Option<&str>) -> PromptDecision {
    let repositories = repository.into_iter().map(str::to_owned).collect::<Vec<_>>();
    PromptDecision {
        source_identity: format!("source-{key}"),
        content_fingerprint: format!("content-{key}"),
        mutation_key: key.to_owned(),
        created_at: Utc.with_ymd_and_hms(2026, 8, 4, 4, 0, 0).unwrap(),
        title_summary: "Reconcile recent ChatGPT work".to_owned(),
        prompt_summary: "Create canonical Linear work and GitHub evidence".to_owned(),
        classification: if repository.is_some() {
            PromptClassification::RepositoryWork
        } else {
            PromptClassification::OperationalProgram
        },
        actionable: true,
        exclusion_reason: None,
        project_resolution: ProjectResolution {
            state: ProjectResolutionState::Resolved,
            repositories,
            linear_projects: vec!["github.com/ORESoftware/ai-agent-coordinator.rs".to_owned()],
        },
        github_evidence_queries: Vec::new(),
        linear_search_terms: Vec::new(),
        needs_review: false,
        scope_signature: format!("scope-{key}"),
    }
}

fn repository(state: LandingState, complete: bool) -> RepositoryLandingEvidence {
    RepositoryLandingEvidence {
        repository: "ORESoftware/ai-agent-coordinator.rs".to_owned(),
        complete,
        state,
        links: if state == LandingState::NoMatch {
            Vec::new()
        } else {
            vec!["https://github.com/ORESoftware/ai-agent-coordinator.rs/pull/31".to_owned()]
        },
    }
}

fn candidate(key: &str, status: LinearIssueStatus) -> LinearIssueCandidate {
    LinearIssueCandidate {
        issue_id: "DEN-834".to_owned(),
        url: "https://linear.app/denman/issue/DEN-834/example".to_owned(),
        project: "github.com/ORESoftware/ai-agent-coordinator.rs".to_owned(),
        status,
        scope_signature: Some(format!("scope-{key}")),
        mutation_keys: vec![key.to_owned()],
        repositories: vec!["ORESoftware/ai-agent-coordinator.rs".to_owned()],
    }
}

#[test]
fn identical_inputs_produce_byte_stable_json() {
    let report = report(vec![decision("m1", Some("ORESoftware/ai-agent-coordinator.rs"))]);
    let evidence = ReconciliationEvidence {
        prompts: vec![PromptEvidence {
            mutation_key: "m1".to_owned(),
            repositories: vec![repository(LandingState::NonDefaultOnly, true)],
            linear_candidates: vec![candidate("m1", LinearIssueStatus::InProgress)],
            residual_operational_work: false,
        }],
        receipts: Vec::new(),
    };
    let first = serde_json::to_vec(&build_reconciliation_plan(&report, &evidence).unwrap())
        .unwrap();
    let second = serde_json::to_vec(&build_reconciliation_plan(&report, &evidence).unwrap())
        .unwrap();
    assert_eq!(first, second);
}

#[test]
fn applied_receipt_suppresses_all_duplicate_mutations() {
    let report = report(vec![decision("m1", Some("ORESoftware/ai-agent-coordinator.rs"))]);
    let evidence = ReconciliationEvidence {
        prompts: Vec::new(),
        receipts: vec![MutationReceipt {
            mutation_key: "m1".to_owned(),
            operation_id: "operation-1".to_owned(),
            outcome: ReceiptOutcome::Applied,
            canonical_issue_id: Some("DEN-834".to_owned()),
        }],
    };
    let plan = build_reconciliation_plan(&report, &evidence).unwrap();
    assert_eq!(plan.prompts[0].action, PlanAction::AlreadyApplied);
    assert!(plan.prompts[0].mutation.is_none());
    assert_eq!(plan.counts.already_applied, 1);
}

#[test]
fn existing_exact_candidate_is_amended_before_create() {
    let report = report(vec![decision("m1", Some("ORESoftware/ai-agent-coordinator.rs"))]);
    let evidence = ReconciliationEvidence {
        prompts: vec![PromptEvidence {
            mutation_key: "m1".to_owned(),
            repositories: vec![repository(LandingState::NoMatch, true)],
            linear_candidates: vec![candidate("m1", LinearIssueStatus::Todo)],
            residual_operational_work: false,
        }],
        receipts: Vec::new(),
    };
    let plan = build_reconciliation_plan(&report, &evidence).unwrap();
    assert_eq!(plan.prompts[0].action, PlanAction::AmendIssue);
    let mutation = plan.prompts[0].mutation.as_ref().unwrap();
    assert_eq!(mutation.kind, LinearMutationKind::Amend);
    assert_eq!(mutation.issue_id.as_deref(), Some("DEN-834"));
    assert_eq!(plan.counts.create, 0);
}

#[test]
fn default_branch_landing_without_candidate_does_not_create_noise() {
    let report = report(vec![decision("m1", Some("ORESoftware/ai-agent-coordinator.rs"))]);
    let evidence = ReconciliationEvidence {
        prompts: vec![PromptEvidence {
            mutation_key: "m1".to_owned(),
            repositories: vec![repository(LandingState::DefaultBranch, true)],
            linear_candidates: Vec::new(),
            residual_operational_work: false,
        }],
        receipts: Vec::new(),
    };
    let plan = build_reconciliation_plan(&report, &evidence).unwrap();
    assert_eq!(plan.prompts[0].action, PlanAction::AlreadyLanded);
    assert!(plan.prompts[0].mutation.is_none());
}

#[test]
fn default_branch_landing_preserves_residual_operational_work() {
    let report = report(vec![decision("m1", Some("ORESoftware/ai-agent-coordinator.rs"))]);
    let evidence = ReconciliationEvidence {
        prompts: vec![PromptEvidence {
            mutation_key: "m1".to_owned(),
            repositories: vec![repository(LandingState::DefaultBranch, true)],
            linear_candidates: Vec::new(),
            residual_operational_work: true,
        }],
        receipts: Vec::new(),
    };
    let plan = build_reconciliation_plan(&report, &evidence).unwrap();
    assert_eq!(plan.prompts[0].action, PlanAction::CreateIssue);
    assert!(plan.prompts[0]
        .reasons
        .contains(&PlanReason::ResidualOperationalWork));
}

#[test]
fn no_candidate_for_unlanded_scope_proposes_create() {
    let report = report(vec![decision("m1", Some("ORESoftware/ai-agent-coordinator.rs"))]);
    let evidence = ReconciliationEvidence {
        prompts: vec![PromptEvidence {
            mutation_key: "m1".to_owned(),
            repositories: vec![repository(LandingState::NonDefaultOnly, true)],
            linear_candidates: Vec::new(),
            residual_operational_work: false,
        }],
        receipts: Vec::new(),
    };
    let plan = build_reconciliation_plan(&report, &evidence).unwrap();
    assert_eq!(plan.prompts[0].action, PlanAction::CreateIssue);
    assert_eq!(plan.prompts[0].mutation.as_ref().unwrap().kind, LinearMutationKind::Create);
}

#[test]
fn equally_strong_candidates_fail_closed_for_review() {
    let report = report(vec![decision("m1", Some("ORESoftware/ai-agent-coordinator.rs"))]);
    let mut second = candidate("m1", LinearIssueStatus::InProgress);
    second.issue_id = "DEN-1609".to_owned();
    second.url = "https://linear.app/denman/issue/DEN-1609/example".to_owned();
    let evidence = ReconciliationEvidence {
        prompts: vec![PromptEvidence {
            mutation_key: "m1".to_owned(),
            repositories: vec![repository(LandingState::NoMatch, true)],
            linear_candidates: vec![candidate("m1", LinearIssueStatus::InProgress), second],
            residual_operational_work: false,
        }],
        receipts: Vec::new(),
    };
    let plan = build_reconciliation_plan(&report, &evidence).unwrap();
    assert_eq!(plan.prompts[0].action, PlanAction::Review);
    assert!(plan.prompts[0]
        .reasons
        .contains(&PlanReason::AmbiguousLinearCandidates));
}

#[test]
fn incomplete_github_evidence_fails_closed() {
    let report = report(vec![decision("m1", Some("ORESoftware/ai-agent-coordinator.rs"))]);
    let evidence = ReconciliationEvidence {
        prompts: vec![PromptEvidence {
            mutation_key: "m1".to_owned(),
            repositories: vec![repository(LandingState::NoMatch, false)],
            linear_candidates: vec![candidate("m1", LinearIssueStatus::Todo)],
            residual_operational_work: false,
        }],
        receipts: Vec::new(),
    };
    let plan = build_reconciliation_plan(&report, &evidence).unwrap();
    assert_eq!(plan.prompts[0].action, PlanAction::Review);
    assert!(plan.prompts[0]
        .reasons
        .contains(&PlanReason::IncompleteRepositoryEvidence));
}

#[test]
fn unsafe_urls_are_rejected_before_planning() {
    let report = report(vec![decision("m1", Some("ORESoftware/ai-agent-coordinator.rs"))]);
    let mut unsafe_repository = repository(LandingState::DefaultBranch, true);
    unsafe_repository.links = vec![
        "https://github.com/ORESoftware/ai-agent-coordinator.rs/pull/31?token=secret"
            .to_owned(),
    ];
    let evidence = ReconciliationEvidence {
        prompts: vec![PromptEvidence {
            mutation_key: "m1".to_owned(),
            repositories: vec![unsafe_repository],
            linear_candidates: Vec::new(),
            residual_operational_work: false,
        }],
        receipts: Vec::new(),
    };
    assert!(matches!(
        build_reconciliation_plan(&report, &evidence),
        Err(ReconciliationError::UnsafeEvidenceUrl(_))
    ));
}

#[test]
fn non_repository_operational_work_can_be_created_without_github_evidence() {
    let report = report(vec![decision("m1", None)]);
    let evidence = ReconciliationEvidence {
        prompts: vec![PromptEvidence {
            mutation_key: "m1".to_owned(),
            repositories: Vec::new(),
            linear_candidates: Vec::new(),
            residual_operational_work: true,
        }],
        receipts: Vec::new(),
    };
    let plan = build_reconciliation_plan(&report, &evidence).unwrap();
    assert_eq!(plan.prompts[0].action, PlanAction::CreateIssue);
}
