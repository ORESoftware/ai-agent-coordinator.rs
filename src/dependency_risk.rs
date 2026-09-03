//! Side-effect-free dependency-update risk classification for DEN-3449.
//!
//! This module consumes evidence already collected by another boundary. It has
//! no filesystem, network, subprocess, package-manager, clock, or environment
//! access. Callers must collect and validate provenance separately, then pass a
//! complete evidence value here.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const DEPENDENCY_RISK_SCHEMA_VERSION: &str = "1.0.0";
pub const DEPENDENCY_RISK_CLASSIFIER_VERSION: &str = "1.0.0";

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Ecosystem {
    Cargo,
    NpmPnpm,
    GoModules,
    DartPub,
    MavenGradle,
    GitSubmodule,
    ZedPackage,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DependencyRelationship {
    Direct,
    Transitive,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DependencyScope {
    Runtime,
    Build,
    Dev,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SemverDelta {
    None,
    Patch,
    Minor,
    Major,
    Unknown,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RepositoryCriticality {
    Low,
    Medium,
    High,
    Critical,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CrossBoundaryImpact {
    Interfaces,
    LibCore,
    Clients,
    Cli,
    ZedPackages,
    Submodules,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct DependencyEdge {
    pub from: String,
    pub to: String,
    pub requirement: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct DependencyRiskInput {
    pub repository: Option<String>,
    pub ecosystem: Option<Ecosystem>,
    pub dependency: Option<String>,
    pub relationship: Option<DependencyRelationship>,
    pub scope: Option<DependencyScope>,
    pub semver_delta: Option<SemverDelta>,

    pub before_manifest_sha256: Option<String>,
    pub after_manifest_sha256: Option<String>,
    pub before_lockfile_sha256: Option<String>,
    pub after_lockfile_sha256: Option<String>,

    pub edge_additions: Option<Vec<DependencyEdge>>,
    pub edge_removals: Option<Vec<DependencyEdge>>,
    pub mutable_git_refs: Option<Vec<String>>,
    pub introduced_registries: Option<Vec<String>>,
    pub checksum_loss: Option<bool>,
    pub lifecycle_hooks: Option<Vec<String>>,
    pub native_build_scripts: Option<Vec<String>>,
    pub binary_downloads: Option<Vec<String>>,

    pub advisory_snapshot_sha256: Option<String>,
    pub advisory_snapshot_age_seconds: Option<u64>,
    pub max_advisory_age_seconds: Option<u64>,
    pub known_advisories: Option<Vec<String>>,
    pub repository_criticality: Option<RepositoryCriticality>,
    pub cross_boundary_impact: Option<Vec<CrossBoundaryImpact>>,

    pub policy_sha256: Option<String>,
    pub candidate_head_sha: Option<String>,
    pub evidence_head_sha: Option<String>,
    pub clean_checkout_receipt_identity: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DependencyRiskLevel {
    Low,
    Medium,
    High,
    Blocked,
}

/// Ordered by fail-closed severity and then by progressively lower risk.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DependencyRiskReason {
    MissingRequiredEvidence,
    InvalidEvidence,
    InvalidDigestEvidence,
    EvidenceFromAnotherHead,
    StaleAdvisoryEvidence,
    MutableGitReference,
    NewRegistry,
    ChecksumRemoved,
    AmbiguousVersionDelta,
    KnownAdvisory,
    LifecycleHook,
    NativeBuildScript,
    BinaryDownload,
    MajorVersionDelta,
    HighCriticalityRepository,
    DependencyGraphChanged,
    CrossBoundaryGraphChange,
    DirectRuntimeDependency,
    MediumCriticalityRepository,
    MinorVersionDelta,
    BaselineLowRisk,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DependencyRiskEnvelope {
    pub schema_version: &'static str,
    pub classifier_version: &'static str,
    pub policy_sha256: Option<String>,
    pub advisory_snapshot_sha256: Option<String>,
    pub candidate_head_sha: Option<String>,
    pub clean_checkout_receipt_identity: Option<String>,
    pub normalized_input_sha256: String,
    pub classification: DependencyRiskLevel,
    pub reasons: Vec<DependencyRiskReason>,
    pub requires_test_org_canary: bool,
    pub auto_merge_authorized: bool,
}

fn trim_option(value: &mut Option<String>) {
    if let Some(current) = value {
        *current = current.trim().to_owned();
    }
}

fn normalize_strings(values: &mut Option<Vec<String>>) {
    if let Some(values) = values {
        for value in values.iter_mut() {
            *value = value.trim().to_owned();
        }
        values.sort();
        values.dedup();
    }
}

fn normalize_edges(values: &mut Option<Vec<DependencyEdge>>) {
    if let Some(values) = values {
        for edge in values.iter_mut() {
            edge.from = edge.from.trim().to_owned();
            edge.to = edge.to.trim().to_owned();
            trim_option(&mut edge.requirement);
        }
        values.sort();
        values.dedup();
    }
}

fn normalize_impacts(values: &mut Option<Vec<CrossBoundaryImpact>>) {
    if let Some(values) = values {
        values.sort();
        values.dedup();
    }
}

#[must_use]
pub fn normalize_dependency_risk_input(input: &DependencyRiskInput) -> DependencyRiskInput {
    let mut normalized = input.clone();
    for value in [
        &mut normalized.repository,
        &mut normalized.dependency,
        &mut normalized.before_manifest_sha256,
        &mut normalized.after_manifest_sha256,
        &mut normalized.before_lockfile_sha256,
        &mut normalized.after_lockfile_sha256,
        &mut normalized.advisory_snapshot_sha256,
        &mut normalized.policy_sha256,
        &mut normalized.candidate_head_sha,
        &mut normalized.evidence_head_sha,
        &mut normalized.clean_checkout_receipt_identity,
    ] {
        trim_option(value);
    }
    normalize_edges(&mut normalized.edge_additions);
    normalize_edges(&mut normalized.edge_removals);
    normalize_strings(&mut normalized.mutable_git_refs);
    normalize_strings(&mut normalized.introduced_registries);
    normalize_strings(&mut normalized.lifecycle_hooks);
    normalize_strings(&mut normalized.native_build_scripts);
    normalize_strings(&mut normalized.binary_downloads);
    normalize_strings(&mut normalized.known_advisories);
    normalize_impacts(&mut normalized.cross_boundary_impact);
    normalized
}

fn is_lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_repository(value: &str) -> bool {
    let Some((owner, repository)) = value.split_once('/') else {
        return false;
    };
    !owner.is_empty()
        && !repository.is_empty()
        && !repository.contains('/')
        && owner
            .bytes()
            .chain(repository.bytes())
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
}

fn valid_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && !value.chars().any(char::is_control)
        && !value.contains(char::is_whitespace)
}

fn invalid_string_list(values: &Option<Vec<String>>) -> bool {
    values
        .as_ref()
        .is_some_and(|values| values.iter().any(|value| value.is_empty() || value.len() > 512))
}

fn invalid_edges(values: &Option<Vec<DependencyEdge>>) -> bool {
    values.as_ref().is_some_and(|values| {
        values.iter().any(|edge| {
            !valid_name(&edge.from)
                || !valid_name(&edge.to)
                || edge
                    .requirement
                    .as_ref()
                    .is_some_and(|requirement| requirement.is_empty() || requirement.len() > 256)
        })
    })
}

fn canonical_input_sha256(input: &DependencyRiskInput) -> String {
    let bytes = serde_json::to_vec(input).expect("dependency risk input is JSON serializable");
    hex::encode(Sha256::digest(bytes))
}

fn required_evidence_missing(input: &DependencyRiskInput) -> bool {
    input.repository.is_none()
        || input.ecosystem.is_none()
        || input.dependency.is_none()
        || input.relationship.is_none()
        || input.scope.is_none()
        || input.semver_delta.is_none()
        || input.before_manifest_sha256.is_none()
        || input.after_manifest_sha256.is_none()
        || input.before_lockfile_sha256.is_none()
        || input.after_lockfile_sha256.is_none()
        || input.edge_additions.is_none()
        || input.edge_removals.is_none()
        || input.mutable_git_refs.is_none()
        || input.introduced_registries.is_none()
        || input.checksum_loss.is_none()
        || input.lifecycle_hooks.is_none()
        || input.native_build_scripts.is_none()
        || input.binary_downloads.is_none()
        || input.advisory_snapshot_sha256.is_none()
        || input.advisory_snapshot_age_seconds.is_none()
        || input.max_advisory_age_seconds.is_none()
        || input.known_advisories.is_none()
        || input.repository_criticality.is_none()
        || input.cross_boundary_impact.is_none()
        || input.policy_sha256.is_none()
        || input.candidate_head_sha.is_none()
        || input.evidence_head_sha.is_none()
        || input.clean_checkout_receipt_identity.is_none()
}

fn invalid_digests(input: &DependencyRiskInput) -> bool {
    [
        input.before_manifest_sha256.as_deref(),
        input.after_manifest_sha256.as_deref(),
        input.before_lockfile_sha256.as_deref(),
        input.after_lockfile_sha256.as_deref(),
        input.advisory_snapshot_sha256.as_deref(),
        input.policy_sha256.as_deref(),
        input.clean_checkout_receipt_identity.as_deref(),
    ]
    .into_iter()
    .flatten()
    .any(|value| !is_lower_hex(value, 64))
        || [
            input.candidate_head_sha.as_deref(),
            input.evidence_head_sha.as_deref(),
        ]
        .into_iter()
        .flatten()
        .any(|value| !is_lower_hex(value, 40))
}

fn invalid_semantic_evidence(input: &DependencyRiskInput) -> bool {
    input
        .repository
        .as_deref()
        .is_some_and(|value| !valid_repository(value))
        || input
            .dependency
            .as_deref()
            .is_some_and(|value| !valid_name(value))
        || invalid_edges(&input.edge_additions)
        || invalid_edges(&input.edge_removals)
        || invalid_string_list(&input.mutable_git_refs)
        || invalid_string_list(&input.introduced_registries)
        || invalid_string_list(&input.lifecycle_hooks)
        || invalid_string_list(&input.native_build_scripts)
        || invalid_string_list(&input.binary_downloads)
        || invalid_string_list(&input.known_advisories)
        || input.max_advisory_age_seconds == Some(0)
}

fn has_values<T>(values: &Option<Vec<T>>) -> bool {
    values.as_ref().is_some_and(|values| !values.is_empty())
}

/// Classify already-collected evidence without performing an effect.
#[must_use]
pub fn classify_dependency_update(input: &DependencyRiskInput) -> DependencyRiskEnvelope {
    let normalized = normalize_dependency_risk_input(input);
    let mut reasons = BTreeSet::new();
    let mut blocked = false;
    let mut high = false;
    let mut medium = false;

    if required_evidence_missing(&normalized) {
        reasons.insert(DependencyRiskReason::MissingRequiredEvidence);
        blocked = true;
    }
    if invalid_semantic_evidence(&normalized) {
        reasons.insert(DependencyRiskReason::InvalidEvidence);
        blocked = true;
    }
    if invalid_digests(&normalized) {
        reasons.insert(DependencyRiskReason::InvalidDigestEvidence);
        blocked = true;
    }
    if normalized.candidate_head_sha.is_some()
        && normalized.evidence_head_sha.is_some()
        && normalized.candidate_head_sha != normalized.evidence_head_sha
    {
        reasons.insert(DependencyRiskReason::EvidenceFromAnotherHead);
        blocked = true;
    }
    if let (Some(age), Some(maximum)) = (
        normalized.advisory_snapshot_age_seconds,
        normalized.max_advisory_age_seconds,
    ) {
        if age > maximum {
            reasons.insert(DependencyRiskReason::StaleAdvisoryEvidence);
            blocked = true;
        }
    }
    if has_values(&normalized.mutable_git_refs) {
        reasons.insert(DependencyRiskReason::MutableGitReference);
        blocked = true;
    }
    if has_values(&normalized.introduced_registries) {
        reasons.insert(DependencyRiskReason::NewRegistry);
        blocked = true;
    }
    if normalized.checksum_loss == Some(true) {
        reasons.insert(DependencyRiskReason::ChecksumRemoved);
        blocked = true;
    }
    if normalized.semver_delta == Some(SemverDelta::Unknown) {
        reasons.insert(DependencyRiskReason::AmbiguousVersionDelta);
        blocked = true;
    }

    if has_values(&normalized.known_advisories) {
        reasons.insert(DependencyRiskReason::KnownAdvisory);
        high = true;
    }
    if has_values(&normalized.lifecycle_hooks) {
        reasons.insert(DependencyRiskReason::LifecycleHook);
        high = true;
    }
    if has_values(&normalized.native_build_scripts) {
        reasons.insert(DependencyRiskReason::NativeBuildScript);
        high = true;
    }
    if has_values(&normalized.binary_downloads) {
        reasons.insert(DependencyRiskReason::BinaryDownload);
        high = true;
    }
    if normalized.semver_delta == Some(SemverDelta::Major) {
        reasons.insert(DependencyRiskReason::MajorVersionDelta);
        high = true;
    }
    if matches!(
        normalized.repository_criticality,
        Some(RepositoryCriticality::High | RepositoryCriticality::Critical)
    ) {
        reasons.insert(DependencyRiskReason::HighCriticalityRepository);
        high = true;
    }

    let graph_changed = has_values(&normalized.edge_additions) || has_values(&normalized.edge_removals);
    if graph_changed {
        reasons.insert(DependencyRiskReason::DependencyGraphChanged);
        medium = true;
    }
    let cross_boundary = has_values(&normalized.cross_boundary_impact);
    if cross_boundary {
        reasons.insert(DependencyRiskReason::CrossBoundaryGraphChange);
        medium = true;
    }
    if normalized.relationship == Some(DependencyRelationship::Direct)
        && normalized.scope == Some(DependencyScope::Runtime)
    {
        reasons.insert(DependencyRiskReason::DirectRuntimeDependency);
        medium = true;
    }
    if normalized.repository_criticality == Some(RepositoryCriticality::Medium) {
        reasons.insert(DependencyRiskReason::MediumCriticalityRepository);
        medium = true;
    }
    if normalized.semver_delta == Some(SemverDelta::Minor) {
        reasons.insert(DependencyRiskReason::MinorVersionDelta);
        medium = true;
    }

    let classification = if blocked {
        DependencyRiskLevel::Blocked
    } else if high {
        DependencyRiskLevel::High
    } else if medium {
        DependencyRiskLevel::Medium
    } else {
        reasons.insert(DependencyRiskReason::BaselineLowRisk);
        DependencyRiskLevel::Low
    };

    DependencyRiskEnvelope {
        schema_version: DEPENDENCY_RISK_SCHEMA_VERSION,
        classifier_version: DEPENDENCY_RISK_CLASSIFIER_VERSION,
        policy_sha256: normalized.policy_sha256.clone(),
        advisory_snapshot_sha256: normalized.advisory_snapshot_sha256.clone(),
        candidate_head_sha: normalized.candidate_head_sha.clone(),
        clean_checkout_receipt_identity: normalized.clean_checkout_receipt_identity.clone(),
        normalized_input_sha256: canonical_input_sha256(&normalized),
        classification,
        reasons: reasons.into_iter().collect(),
        requires_test_org_canary: high || medium || cross_boundary,
        auto_merge_authorized: false,
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use serde::Deserialize;

    use super::*;

    const SHA_A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const SHA_B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const SHA_C: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
    const SHA_D: &str = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd";
    const SHA_E: &str = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
    const HEAD_A: &str = "0123456789abcdef0123456789abcdef01234567";
    const HEAD_B: &str = "89abcdef0123456789abcdef0123456789abcdef";

    fn base_input(ecosystem: Ecosystem) -> DependencyRiskInput {
        DependencyRiskInput {
            repository: Some("example-test/dependency-fixture".to_owned()),
            ecosystem: Some(ecosystem),
            dependency: Some("example-dependency".to_owned()),
            relationship: Some(DependencyRelationship::Transitive),
            scope: Some(DependencyScope::Dev),
            semver_delta: Some(SemverDelta::Patch),
            before_manifest_sha256: Some(SHA_A.to_owned()),
            after_manifest_sha256: Some(SHA_B.to_owned()),
            before_lockfile_sha256: Some(SHA_C.to_owned()),
            after_lockfile_sha256: Some(SHA_D.to_owned()),
            edge_additions: Some(Vec::new()),
            edge_removals: Some(Vec::new()),
            mutable_git_refs: Some(Vec::new()),
            introduced_registries: Some(Vec::new()),
            checksum_loss: Some(false),
            lifecycle_hooks: Some(Vec::new()),
            native_build_scripts: Some(Vec::new()),
            binary_downloads: Some(Vec::new()),
            advisory_snapshot_sha256: Some(SHA_E.to_owned()),
            advisory_snapshot_age_seconds: Some(60),
            max_advisory_age_seconds: Some(86_400),
            known_advisories: Some(Vec::new()),
            repository_criticality: Some(RepositoryCriticality::Low),
            cross_boundary_impact: Some(Vec::new()),
            policy_sha256: Some(SHA_A.to_owned()),
            candidate_head_sha: Some(HEAD_A.to_owned()),
            evidence_head_sha: Some(HEAD_A.to_owned()),
            clean_checkout_receipt_identity: Some(SHA_B.to_owned()),
        }
    }

    #[test]
    fn normalized_input_order_is_byte_identical() {
        let mut left = base_input(Ecosystem::Cargo);
        left.edge_additions = Some(vec![
            DependencyEdge {
                from: "z".to_owned(),
                to: "a".to_owned(),
                requirement: Some("^1".to_owned()),
            },
            DependencyEdge {
                from: "a".to_owned(),
                to: "b".to_owned(),
                requirement: Some("^2".to_owned()),
            },
        ]);
        left.cross_boundary_impact = Some(vec![
            CrossBoundaryImpact::Clients,
            CrossBoundaryImpact::Interfaces,
        ]);
        left.known_advisories = Some(vec!["ADV-2".to_owned(), "ADV-1".to_owned()]);

        let mut right = left.clone();
        right.edge_additions.as_mut().expect("edges").reverse();
        right
            .cross_boundary_impact
            .as_mut()
            .expect("impacts")
            .reverse();
        right.known_advisories.as_mut().expect("advisories").reverse();

        let left = serde_json::to_vec(&classify_dependency_update(&left)).expect("left JSON");
        let right = serde_json::to_vec(&classify_dependency_update(&right)).expect("right JSON");
        assert_eq!(left, right);
    }

    #[test]
    fn missing_stale_mutable_and_wrong_head_evidence_blocks() {
        let mut missing = base_input(Ecosystem::NpmPnpm);
        missing.policy_sha256 = None;
        assert_eq!(
            classify_dependency_update(&missing).classification,
            DependencyRiskLevel::Blocked
        );

        let mut stale = base_input(Ecosystem::GoModules);
        stale.advisory_snapshot_age_seconds = Some(86_401);
        assert!(classify_dependency_update(&stale)
            .reasons
            .contains(&DependencyRiskReason::StaleAdvisoryEvidence));

        let mut mutable = base_input(Ecosystem::DartPub);
        mutable.mutable_git_refs = Some(vec!["refs/heads/main".to_owned()]);
        assert!(classify_dependency_update(&mutable)
            .reasons
            .contains(&DependencyRiskReason::MutableGitReference));

        let mut wrong_head = base_input(Ecosystem::MavenGradle);
        wrong_head.evidence_head_sha = Some(HEAD_B.to_owned());
        assert!(classify_dependency_update(&wrong_head)
            .reasons
            .contains(&DependencyRiskReason::EvidenceFromAnotherHead));
    }

    #[test]
    fn malicious_supply_chain_signals_are_never_low() {
        let mut cases = Vec::new();

        let mut new_registry = base_input(Ecosystem::NpmPnpm);
        new_registry.introduced_registries = Some(vec!["https://registry.invalid".to_owned()]);
        cases.push(new_registry);

        let mut checksum_loss = base_input(Ecosystem::Cargo);
        checksum_loss.checksum_loss = Some(true);
        cases.push(checksum_loss);

        let mut hook = base_input(Ecosystem::NpmPnpm);
        hook.lifecycle_hooks = Some(vec!["postinstall".to_owned()]);
        cases.push(hook);

        let mut native = base_input(Ecosystem::Cargo);
        native.native_build_scripts = Some(vec!["build.rs".to_owned()]);
        cases.push(native);

        let mut binary = base_input(Ecosystem::MavenGradle);
        binary.binary_downloads = Some(vec!["toolchain-archive".to_owned()]);
        cases.push(binary);

        let mut ambiguous = base_input(Ecosystem::GitSubmodule);
        ambiguous.semver_delta = Some(SemverDelta::Unknown);
        cases.push(ambiguous);

        for input in cases {
            assert_ne!(
                classify_dependency_update(&input).classification,
                DependencyRiskLevel::Low
            );
        }
    }

    #[test]
    fn canary_and_auto_merge_rules_are_explicit() {
        let low = classify_dependency_update(&base_input(Ecosystem::GoModules));
        assert_eq!(low.classification, DependencyRiskLevel::Low);
        assert!(!low.requires_test_org_canary);
        assert!(!low.auto_merge_authorized);

        let mut medium = base_input(Ecosystem::DartPub);
        medium.cross_boundary_impact = Some(vec![CrossBoundaryImpact::Clients]);
        let medium = classify_dependency_update(&medium);
        assert_eq!(medium.classification, DependencyRiskLevel::Medium);
        assert!(medium.requires_test_org_canary);
        assert!(!medium.auto_merge_authorized);

        let mut high = base_input(Ecosystem::Cargo);
        high.semver_delta = Some(SemverDelta::Major);
        let high = classify_dependency_update(&high);
        assert_eq!(high.classification, DependencyRiskLevel::High);
        assert!(high.requires_test_org_canary);
        assert!(!high.auto_merge_authorized);
    }

    #[derive(Debug, Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct Fixture {
        input: DependencyRiskInput,
        expected_classification: DependencyRiskLevelFixture,
        expected_canary: bool,
    }

    #[derive(Clone, Copy, Debug, Deserialize)]
    #[serde(rename_all = "snake_case")]
    enum DependencyRiskLevelFixture {
        Low,
        Medium,
        High,
    }

    impl From<DependencyRiskLevelFixture> for DependencyRiskLevel {
        fn from(value: DependencyRiskLevelFixture) -> Self {
            match value {
                DependencyRiskLevelFixture::Low => Self::Low,
                DependencyRiskLevelFixture::Medium => Self::Medium,
                DependencyRiskLevelFixture::High => Self::High,
            }
        }
    }

    #[test]
    fn seven_ecosystems_and_test_orgs_have_dry_fixture_receipts() {
        let fixtures: Vec<Fixture> = serde_json::from_str(include_str!(
            "../tests/fixtures/dependency-risk/test-org-canaries.json"
        ))
        .expect("fixture matrix JSON");
        assert_eq!(fixtures.len(), 7);

        let mut ecosystems = BTreeSet::new();
        let mut organizations = BTreeSet::new();
        for fixture in fixtures {
            let repository = fixture.input.repository.as_deref().expect("repository");
            let (organization, _) = repository.split_once('/').expect("org/repo");
            assert!(organization.ends_with("-test"));
            organizations.insert(organization.to_owned());
            ecosystems.insert(fixture.input.ecosystem.expect("ecosystem"));

            let envelope = classify_dependency_update(&fixture.input);
            assert_eq!(
                envelope.classification,
                DependencyRiskLevel::from(fixture.expected_classification),
                "{repository}"
            );
            assert_eq!(envelope.requires_test_org_canary, fixture.expected_canary);
            assert!(!envelope.auto_merge_authorized);
        }
        assert_eq!(ecosystems.len(), 7);
        assert!(organizations.len() >= 4);
    }
}
