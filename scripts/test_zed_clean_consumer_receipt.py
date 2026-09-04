#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("zed_clean_consumer_receipt.py")
SPEC = importlib.util.spec_from_file_location(
    "zed_clean_consumer_receipt", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "zed-clean-consumer-receipt-2026-09-04.json"
)
COMMIT = "a" * 40
MANIFEST = "b" * 64
LOCK = "c" * 64
ARTIFACT = "d" * 64
EVIDENCE = "e" * 64


class ZedCleanConsumerReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.receipt = MODULE.load(FIXTURE)

    def report(self, mode: str = "planning") -> object:
        return MODULE.validate(self.receipt, mode=mode)

    def assert_error(self, report: object, fragment: str) -> None:
        errors = getattr(report, "errors")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected {fragment!r} in {errors!r}",
        )

    def make_complete(self) -> None:
        for package in self.receipt["packages"]:
            package["status"] = "verified"
            package["commit"] = COMMIT
            package["manifest_sha256"] = MANIFEST
            package["lock_sha256"] = LOCK
            package["artifact_sha256"] = ARTIFACT
        for submodule in self.receipt["submodules"]:
            submodule["status"] = "verified"
            submodule["commit"] = COMMIT
        for consumer in self.receipt["consumers"]:
            consumer["status"] = "green"
            consumer["clean_checkout"] = True
            consumer["local_path_dependencies"] = False
            consumer["monorepo_leakage"] = False
            consumer["compiled"] = True
            consumer["executed"] = True
            consumer["evidence_sha256"] = EVIDENCE
        for operation in self.receipt["operations"]:
            operation["status"] = "green"
            operation["evidence_sha256"] = EVIDENCE
            operation["blockers"] = []
        self.receipt["blockers"] = []

    def test_checked_in_planning_receipt_is_valid_but_not_closed(self) -> None:
        report = self.report("planning")
        self.assertTrue(report.valid, report.errors)
        self.assertFalse(report.closure_ready)
        self.assertEqual(report.package_count, 4)
        self.assertEqual(report.submodule_count, 2)
        self.assertEqual(report.consumer_count, 5)
        self.assertEqual(report.operation_count, 6)
        self.assertRegex(report.receipt_sha256 or "", r"^[0-9a-f]{64}$")
        self.assertTrue(report.warnings)

    def test_checked_in_receipt_fails_closure_mode(self) -> None:
        report = self.report("closure")
        self.assertFalse(report.valid)
        self.assertFalse(report.closure_ready)
        self.assert_error(report, "closure: all four packages must be verified")
        self.assert_error(report, "closure: receipt blockers must be empty")

    def test_synthetic_complete_receipt_closes(self) -> None:
        self.make_complete()
        report = self.report("closure")
        self.assertTrue(report.valid, report.errors)
        self.assertTrue(report.closure_ready)
        self.assertEqual(report.errors, ())

    def test_requires_exactly_four_packages(self) -> None:
        self.receipt["packages"].pop()
        for consumer in self.receipt["consumers"]:
            consumer["package_ids"].pop()
        report = self.report()
        self.assert_error(report, "packages must contain exactly four entries")
        self.assert_error(report, "package roles must be exactly")

    def test_duplicate_package_id_fails(self) -> None:
        self.receipt["packages"][1]["id"] = self.receipt["packages"][0]["id"]
        report = self.report()
        self.assert_error(report, "duplicate package id")

    def test_duplicate_package_role_fails(self) -> None:
        self.receipt["packages"][1]["role"] = "clients"
        report = self.report()
        self.assert_error(report, "duplicate package role")
        self.assert_error(report, "package roles must be exactly")

    def test_unsupported_package_role_fails(self) -> None:
        self.receipt["packages"][1]["role"] = "server"
        report = self.report()
        self.assert_error(report, "role is unsupported")

    def test_package_version_must_be_semver(self) -> None:
        self.receipt["packages"][0]["version"] = "latest"
        report = self.report()
        self.assert_error(report, "semantic version")

    def test_package_repository_url_must_be_exact(self) -> None:
        self.receipt["packages"][0]["repository"] += "?token=redacted"
        report = self.report()
        self.assert_error(report, "exact GitHub repository URL")

    def test_package_registry_must_be_zed(self) -> None:
        self.receipt["packages"][0]["registry"] = "npm"
        report = self.report()
        self.assert_error(report, "registry must be zed")

    def test_verified_package_requires_all_immutable_evidence(self) -> None:
        self.receipt["packages"][0]["artifact_sha256"] = None
        report = self.report()
        self.assert_error(report, "verified package requires commit, manifest, lock")

    def test_malformed_package_commit_fails(self) -> None:
        self.receipt["packages"][0]["commit"] = "not-a-commit"
        report = self.report()
        self.assert_error(report, "lowercase Git SHA")

    def test_duplicate_submodule_id_fails(self) -> None:
        self.receipt["submodules"][1]["id"] = self.receipt["submodules"][0]["id"]
        report = self.report()
        self.assert_error(report, "duplicate submodule id")

    def test_submodule_path_traversal_fails(self) -> None:
        self.receipt["submodules"][0]["path"] = "../outside"
        report = self.report()
        self.assert_error(report, "safe relative path")

    def test_absolute_submodule_path_fails(self) -> None:
        self.receipt["submodules"][0]["path"] = "/tmp/escape"
        report = self.report()
        self.assert_error(report, "safe relative path")

    def test_infrastructure_repository_is_forbidden_as_submodule(self) -> None:
        self.receipt["submodules"][0]["repository"] = (
            "https://github.com/zed-pkg/zed-infra"
        )
        report = self.report()
        self.assert_error(report, "must not include an infrastructure repository")

    def test_infrastructure_path_is_forbidden_as_submodule(self) -> None:
        self.receipt["submodules"][0]["path"] = ".vendor/example-infra"
        report = self.report()
        self.assert_error(report, "must not include an infrastructure repository")

    def test_verified_submodule_requires_exact_commit(self) -> None:
        self.receipt["submodules"][0]["status"] = "verified"
        report = self.report()
        self.assert_error(report, "commit is required when status is verified")

    def test_consumer_must_reference_complete_quartet(self) -> None:
        self.receipt["consumers"][0]["package_ids"].pop()
        report = self.report()
        self.assert_error(report, "must contain between 4 and 4 items")
        self.assert_error(report, "must reference the complete quartet")

    def test_consumer_unknown_submodule_fails(self) -> None:
        self.receipt["consumers"][0]["submodule_ids"] = ["missing-submodule"]
        report = self.report()
        self.assert_error(report, "references unknown submodule")

    def test_duplicate_consumer_id_fails(self) -> None:
        self.receipt["consumers"][1]["id"] = self.receipt["consumers"][0]["id"]
        report = self.report()
        self.assert_error(report, "duplicate consumer id")

    def test_green_consumer_rejects_local_path_dependency(self) -> None:
        self.make_complete()
        self.receipt["consumers"][0]["local_path_dependencies"] = True
        report = self.report("planning")
        self.assert_error(report, "without leakage")

    def test_green_consumer_rejects_monorepo_leakage(self) -> None:
        self.make_complete()
        self.receipt["consumers"][0]["monorepo_leakage"] = True
        report = self.report("planning")
        self.assert_error(report, "without leakage")

    def test_green_consumer_requires_compile_and_execution(self) -> None:
        self.make_complete()
        self.receipt["consumers"][0]["executed"] = False
        report = self.report("planning")
        self.assert_error(report, "clean compile-and-execute evidence")

    def test_missing_required_language_blocks_closure(self) -> None:
        self.make_complete()
        self.receipt["consumers"][4]["language"] = "python"
        report = self.report("closure")
        self.assert_error(report, "required languages are missing: native")

    def test_duplicate_operation_id_fails(self) -> None:
        self.receipt["operations"][1]["id"] = self.receipt["operations"][0]["id"]
        report = self.report()
        self.assert_error(report, "duplicate operation id")

    def test_duplicate_operation_kind_fails(self) -> None:
        self.receipt["operations"][1]["kind"] = "install"
        report = self.report()
        self.assert_error(report, "duplicate operation kind")
        self.assert_error(report, "operation kinds must be exactly")

    def test_green_operation_requires_evidence_and_zero_blockers(self) -> None:
        operation = self.receipt["operations"][0]
        operation["status"] = "green"
        report = self.report()
        self.assert_error(report, "green operation requires evidence and zero blockers")

    def test_offline_reuse_requires_offline_execution(self) -> None:
        self.make_complete()
        operation = next(
            item
            for item in self.receipt["operations"]
            if item["kind"] == "offline-reuse"
        )
        operation["offline"] = False
        report = self.report("closure")
        self.assert_error(report, "offline-reuse must execute without network access")

    def test_concurrent_install_requires_real_contention(self) -> None:
        self.make_complete()
        operation = next(
            item
            for item in self.receipt["operations"]
            if item["kind"] == "concurrent-install"
        )
        operation["concurrent"] = False
        report = self.report("closure")
        self.assert_error(report, "must exercise real contention")

    def test_unknown_graph_source_fails(self) -> None:
        self.receipt["graph_edges"][0]["from"] = "missing-node"
        report = self.report()
        self.assert_error(report, "from references unknown node")

    def test_unknown_graph_target_fails(self) -> None:
        self.receipt["graph_edges"][0]["to"] = "missing-node"
        report = self.report()
        self.assert_error(report, "to references unknown node")

    def test_graph_self_edge_fails(self) -> None:
        self.receipt["graph_edges"][0]["to"] = self.receipt["graph_edges"][0]["from"]
        report = self.report()
        self.assert_error(report, "cannot be a self-edge")

    def test_duplicate_graph_edge_fails(self) -> None:
        self.receipt["graph_edges"].append(
            copy.deepcopy(self.receipt["graph_edges"][0])
        )
        report = self.report()
        self.assert_error(report, "duplicate graph edge")

    def test_dependency_cycle_fails(self) -> None:
        self.receipt["graph_edges"].append(
            {
                "from": "quartet-interfaces",
                "to": "quartet-cli",
                "kind": "package-dependency",
            }
        )
        report = self.report()
        self.assert_error(report, "dependency cycle")

    def test_receipt_blockers_prevent_closure(self) -> None:
        self.make_complete()
        self.receipt["blockers"] = ["remaining-blocker"]
        report = self.report("closure")
        self.assert_error(report, "receipt blockers must be empty")

    def test_safety_flags_fail_closed(self) -> None:
        self.receipt["safety"]["merge_authorized"] = True
        report = self.report()
        self.assert_error(report, "safety.merge_authorized must be false")

    def test_raw_sensitive_field_is_rejected(self) -> None:
        self.receipt["safety"]["raw_prompt"] = "redacted"
        report = self.report()
        self.assert_error(report, "prohibited sensitive field")
        self.assert_error(report, "keys mismatch")

    def test_unknown_root_field_fails(self) -> None:
        self.receipt["unexpected"] = True
        report = self.report()
        self.assert_error(report, "keys mismatch")

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.json"
            path.write_text('{"schema_version":"one","schema_version":"two"}\n')
            with self.assertRaisesRegex(MODULE.ReceiptError, "duplicate JSON key"):
                MODULE.load(path)

    def test_secret_like_content_is_rejected_during_load(self) -> None:
        value = copy.deepcopy(self.receipt)
        value["safety"]["notes"][0] = "fake token=abcdefghijklmnop"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "secret.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ReceiptError, "prohibited"):
                MODULE.load(path)

    def test_email_address_is_rejected_during_load(self) -> None:
        value = copy.deepcopy(self.receipt)
        value["safety"]["notes"][0] = "Contact person@example.invalid"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "email.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ReceiptError, "email address"):
                MODULE.load(path)

    def test_digest_is_key_order_invariant(self) -> None:
        first = MODULE.digest(self.receipt)
        reordered = dict(reversed(list(self.receipt.items())))
        self.assertEqual(first, MODULE.digest(reordered))

    def test_mode_is_strict(self) -> None:
        with self.assertRaisesRegex(MODULE.ReceiptError, "mode must be"):
            MODULE.validate(self.receipt, mode="publish")


if __name__ == "__main__":
    unittest.main()
