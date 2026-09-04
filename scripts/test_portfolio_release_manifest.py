#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("portfolio_release_manifest.py")
SPEC = importlib.util.spec_from_file_location("portfolio_release_manifest", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "portfolio-release-manifest-2026-09-04.json"
)
HEAD = "1" * 40
EVIDENCE = "2" * 64
ARTIFACT = "3" * 64
PROVENANCE = "4" * 64


class PortfolioReleaseManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = MODULE.load(FIXTURE)

    def report(self, mode: str = "planning") -> object:
        return MODULE.validate(self.manifest, mode=mode)

    def assert_error(self, report: object, fragment: str) -> None:
        errors = getattr(report, "errors")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected {fragment!r} in {errors!r}",
        )

    def make_complete(self) -> None:
        coverage = self.manifest["coverage"]
        coverage["catalog_complete"] = True
        coverage["expected_repository_count"] = len(self.manifest["repositories"])
        coverage["observed_repository_count"] = len(self.manifest["repositories"])
        coverage["unresolved_repository_count"] = 0

        for repository in self.manifest["repositories"]:
            repository["status"] = "verified"
            repository["head_sha"] = HEAD
            repository["archived"] = False
            repository["superseded"] = False

        for capability in self.manifest["capabilities"]:
            capability["maturity"] = 4
            capability["status"] = "verified"
            capability["blockers"] = []

        for artifact in self.manifest["artifacts"]:
            artifact["status"] = "verified"
            artifact["sha256"] = ARTIFACT
            artifact["provenance_sha256"] = PROVENANCE

        for lane in self.manifest["lanes"]:
            lane["status"] = "green"
            lane["head_sha"] = HEAD
            lane["evidence_sha256"] = EVIDENCE
            lane["independent"] = True
            lane["blockers"] = []

        self.manifest["blockers"] = []
        decision = self.manifest["decision"]
        decision["value"] = "go"
        decision["rationale"] = (
            "Synthetic closure fixture with complete immutable evidence and approval."
        )
        decision["approved_by"] = ["release-reviewer"]
        decision["decided_at"] = "2026-09-04T13:00:00-04:00"

    def test_checked_in_planning_manifest_is_valid_but_not_closable(self) -> None:
        report = self.report("planning")
        self.assertTrue(report.valid, report.errors)
        self.assertFalse(report.closure_ready)
        self.assertEqual(report.repository_count, 5)
        self.assertEqual(report.capability_count, 5)
        self.assertEqual(report.artifact_count, 5)
        self.assertEqual(report.lane_count, 8)
        self.assertRegex(report.manifest_sha256 or "", r"^[0-9a-f]{64}$")
        self.assertTrue(report.warnings)

    def test_checked_in_manifest_fails_closure_mode(self) -> None:
        report = self.report("closure")
        self.assertFalse(report.valid)
        self.assertFalse(report.closure_ready)
        self.assert_error(report, "closure: catalog_complete must be true")
        self.assert_error(report, "closure: repository coverage must be complete")
        self.assert_error(report, "closure: decision must be approved go")

    def test_synthetic_complete_manifest_closes(self) -> None:
        self.make_complete()
        report = self.report("closure")
        self.assertTrue(report.valid, report.errors)
        self.assertTrue(report.closure_ready)
        self.assertEqual(report.errors, ())

    def test_go_is_rejected_before_closure(self) -> None:
        self.manifest["decision"]["value"] = "go"
        self.manifest["decision"]["approved_by"] = ["reviewer"]
        report = self.report("planning")
        self.assertFalse(report.valid)
        self.assert_error(report, "decision.value cannot be go before closure")

    def test_coverage_observed_count_matches_inventory(self) -> None:
        self.manifest["coverage"]["observed_repository_count"] = 4
        report = self.report()
        self.assert_error(report, "must equal repositories length")

    def test_coverage_equation_is_enforced(self) -> None:
        self.manifest["coverage"]["unresolved_repository_count"] = 2
        report = self.report()
        self.assert_error(report, "observed_repository_count + unresolved")

    def test_duplicate_repository_id_fails(self) -> None:
        self.manifest["repositories"][1]["id"] = self.manifest["repositories"][0]["id"]
        report = self.report()
        self.assert_error(report, "duplicate repository id")

    def test_duplicate_repository_url_fails(self) -> None:
        self.manifest["repositories"][1]["github_url"] = self.manifest[
            "repositories"
        ][0]["github_url"]
        report = self.report()
        self.assert_error(report, "duplicate repository URL")

    def test_verified_repository_requires_exact_head(self) -> None:
        self.manifest["repositories"][1]["status"] = "verified"
        report = self.report()
        self.assert_error(report, "head_sha is required when status is verified")

    def test_malformed_repository_head_fails(self) -> None:
        self.manifest["repositories"][0]["head_sha"] = "not-a-head"
        report = self.report()
        self.assert_error(report, "lowercase Git SHA")

    def test_repository_url_must_be_exact(self) -> None:
        self.manifest["repositories"][0]["github_url"] += "?token=redacted"
        report = self.report()
        self.assert_error(report, "exact GitHub repository URL")

    def test_repository_branch_format_is_bounded(self) -> None:
        self.manifest["repositories"][0]["default_branch"] = "bad branch"
        report = self.report()
        self.assert_error(report, "default_branch has an invalid format")

    def test_repository_linear_anchor_is_validated(self) -> None:
        self.manifest["repositories"][0]["linear_anchor"] = "not-linear"
        report = self.report()
        self.assert_error(report, "must be a DEN-N identifier")

    def test_unknown_capability_repository_fails(self) -> None:
        self.manifest["capabilities"][0]["implementation_repositories"] = [
            "missing-repository"
        ]
        report = self.report()
        self.assert_error(report, "references unknown repository")

    def test_unknown_capability_lane_fails(self) -> None:
        self.manifest["capabilities"][0]["independent_lanes"] = ["missing-lane"]
        report = self.report()
        self.assert_error(report, "references unknown lane")

    def test_duplicate_capability_id_fails(self) -> None:
        self.manifest["capabilities"][1]["id"] = self.manifest["capabilities"][0]["id"]
        report = self.report()
        self.assert_error(report, "duplicate capability id")

    def test_boolean_maturity_is_not_an_integer(self) -> None:
        self.manifest["capabilities"][0]["maturity"] = True
        report = self.report()
        self.assert_error(report, "maturity must be an integer")

    def test_capability_below_maturity_four_blocks_closure(self) -> None:
        self.make_complete()
        self.manifest["capabilities"][0]["maturity"] = 3
        self.manifest["decision"]["value"] = "hold"
        self.manifest["decision"]["approved_by"] = []
        report = self.report("closure")
        self.assert_error(report, "lacks independent maturity-four evidence")

    def test_green_lane_requires_head_evidence_and_no_blockers(self) -> None:
        lane = self.manifest["lanes"][0]
        lane["status"] = "green"
        report = self.report()
        self.assert_error(report, "green lane requires head, evidence, and zero blockers")

    def test_duplicate_lane_id_fails(self) -> None:
        self.manifest["lanes"][1]["id"] = self.manifest["lanes"][0]["id"]
        report = self.report()
        self.assert_error(report, "duplicate lane id")

    def test_non_independent_lane_blocks_closure(self) -> None:
        self.make_complete()
        self.manifest["lanes"][0]["independent"] = False
        self.manifest["decision"]["value"] = "hold"
        self.manifest["decision"]["approved_by"] = []
        report = self.report("closure")
        self.assert_error(report, "required lane kind is not certified: contract")

    def test_destructive_lane_kind_requires_destructive_execution(self) -> None:
        self.make_complete()
        security = next(
            lane for lane in self.manifest["lanes"] if lane["kind"] == "security"
        )
        security["destructive"] = False
        self.manifest["decision"]["value"] = "hold"
        self.manifest["decision"]["approved_by"] = []
        report = self.report("closure")
        self.assert_error(report, "required lane kind is not certified: security")

    def test_missing_required_lane_kind_blocks_closure(self) -> None:
        self.make_complete()
        self.manifest["lanes"] = [
            lane for lane in self.manifest["lanes"] if lane["kind"] != "rollback"
        ]
        for capability in self.manifest["capabilities"]:
            capability["independent_lanes"] = [
                lane_id
                for lane_id in capability["independent_lanes"]
                if lane_id != "rollback-certification"
            ] or ["recovery-chaos"]
        self.manifest["decision"]["value"] = "hold"
        self.manifest["decision"]["approved_by"] = []
        report = self.report("closure")
        self.assert_error(report, "missing required lane kinds: rollback")

    def test_verified_artifact_requires_content_and_provenance(self) -> None:
        artifact = self.manifest["artifacts"][0]
        artifact["status"] = "verified"
        report = self.report()
        self.assert_error(report, "verified artifact requires content and provenance")

    def test_unknown_artifact_repository_fails(self) -> None:
        self.manifest["artifacts"][0]["repository_id"] = "missing-repository"
        report = self.report()
        self.assert_error(report, "references unknown repository")

    def test_duplicate_artifact_id_fails(self) -> None:
        self.manifest["artifacts"][1]["id"] = self.manifest["artifacts"][0]["id"]
        report = self.report()
        self.assert_error(report, "duplicate artifact id")

    def test_archived_repository_blocks_closure(self) -> None:
        self.make_complete()
        self.manifest["repositories"][0]["archived"] = True
        self.manifest["decision"]["value"] = "hold"
        self.manifest["decision"]["approved_by"] = []
        report = self.report("closure")
        self.assert_error(report, "repository 0 is not releasable")

    def test_superseded_repository_blocks_closure(self) -> None:
        self.make_complete()
        self.manifest["repositories"][0]["superseded"] = True
        self.manifest["decision"]["value"] = "hold"
        self.manifest["decision"]["approved_by"] = []
        report = self.report("closure")
        self.assert_error(report, "repository 0 is not releasable")

    def test_manifest_blocker_blocks_closure(self) -> None:
        self.make_complete()
        self.manifest["blockers"] = ["residual-blocker"]
        self.manifest["decision"]["value"] = "hold"
        self.manifest["decision"]["approved_by"] = []
        report = self.report("closure")
        self.assert_error(report, "manifest blockers must be empty")

    def test_go_requires_named_approval(self) -> None:
        self.make_complete()
        self.manifest["decision"]["value"] = "hold"
        self.manifest["decision"]["approved_by"] = []
        report = self.report("closure")
        self.assert_error(report, "decision must be approved go")

    def test_safety_flags_fail_closed(self) -> None:
        self.manifest["safety"]["deployment_authorized"] = True
        report = self.report()
        self.assert_error(report, "deployment_authorized must be false")

    def test_read_only_is_required(self) -> None:
        self.manifest["safety"]["read_only"] = False
        report = self.report()
        self.assert_error(report, "safety.read_only must be true")

    def test_raw_sensitive_field_is_rejected(self) -> None:
        self.manifest["decision"]["raw_prompt"] = "redacted"
        report = self.report()
        self.assert_error(report, "prohibited sensitive field")
        self.assert_error(report, "keys mismatch")

    def test_unknown_root_field_fails(self) -> None:
        self.manifest["unexpected"] = True
        report = self.report()
        self.assert_error(report, "keys mismatch")

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.json"
            path.write_text('{"schema_version":"one","schema_version":"two"}\n')
            with self.assertRaisesRegex(MODULE.ManifestError, "duplicate JSON key"):
                MODULE.load(path)

    def test_secret_like_content_is_rejected_during_load(self) -> None:
        value = copy.deepcopy(self.manifest)
        value["safety"]["notes"][0] = "fake token=abcdefghijklmnop"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "secret.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ManifestError, "prohibited"):
                MODULE.load(path)

    def test_email_address_is_rejected_during_load(self) -> None:
        value = copy.deepcopy(self.manifest)
        value["safety"]["notes"][0] = "Contact person@example.invalid"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "email.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ManifestError, "email address"):
                MODULE.load(path)

    def test_digest_is_key_order_invariant(self) -> None:
        first = MODULE.digest(self.manifest)
        reordered = dict(reversed(list(self.manifest.items())))
        self.assertEqual(first, MODULE.digest(reordered))

    def test_mode_is_strict(self) -> None:
        with self.assertRaisesRegex(MODULE.ManifestError, "mode must be"):
            MODULE.validate(self.manifest, mode="apply")


if __name__ == "__main__":
    unittest.main()
