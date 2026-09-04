#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("digest_provenance.py")
SPEC = importlib.util.spec_from_file_location("digest_provenance", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIXTURES = Path(__file__).parents[1] / "fixtures"


class OpportunityDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.digest = MODULE.load(FIXTURES / "opportunity-digest-v1.json")

    def report(self) -> object:
        return MODULE.validate(self.digest)

    def assert_error(self, report: object, fragment: str) -> None:
        errors = getattr(report, "errors")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected {fragment!r} in {errors!r}",
        )

    def test_valid_opportunity_digest(self) -> None:
        report = self.report()
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.digest_kind, "opportunity")
        self.assertEqual(report.item_count, 4)
        self.assertEqual(report.company_count, 2)
        self.assertEqual(report.fiducia_company_count, 1)
        self.assertRegex(report.digest_sha256 or "", r"^[0-9a-f]{64}$")

    def test_digest_hash_is_object_key_order_invariant(self) -> None:
        first = MODULE.canonical_sha256(self.digest)
        reordered = dict(reversed(list(self.digest.items())))
        self.assertEqual(first, MODULE.canonical_sha256(reordered))

    def test_duplicate_requisition_fails(self) -> None:
        self.digest["items"][1]["requisition_id"] = "1042"
        report = self.report()
        self.assert_error(report, "duplicate requisition identity")

    def test_duplicate_canonical_url_fails(self) -> None:
        self.digest["items"][1]["canonical_url"] = self.digest["items"][0][
            "canonical_url"
        ]
        report = self.report()
        self.assert_error(report, "duplicate canonical_url")

    def test_noncanonical_url_query_fails(self) -> None:
        self.digest["items"][0]["canonical_url"] += "?tracking=1"
        report = self.report()
        self.assert_error(report, "without userinfo, query, or fragment")

    def test_third_party_source_fails(self) -> None:
        self.digest["items"][0]["source_kind"] = "job_aggregator"
        report = self.report()
        self.assert_error(report, "official first-party source")

    def test_verified_before_window_fails(self) -> None:
        self.digest["items"][0]["verified_at"] = "2026-08-31T23:59:59-04:00"
        report = self.report()
        self.assert_error(report, "precedes the digest window")

    def test_verified_at_window_end_fails(self) -> None:
        self.digest["items"][0]["verified_at"] = "2026-09-05T00:00:00-04:00"
        report = self.report()
        self.assert_error(report, "must fall inside the digest window")

    def test_posted_after_verification_fails(self) -> None:
        self.digest["items"][0]["posted_at"] = "2026-09-04T08:11:00-04:00"
        report = self.report()
        self.assert_error(report, "posted_at must not follow verified_at")

    def test_expiry_must_follow_verification(self) -> None:
        self.digest["items"][0]["expires_at"] = "2026-09-04T08:00:00-04:00"
        report = self.report()
        self.assert_error(report, "expires_at must follow verified_at")

    def test_compensation_minimum_must_not_exceed_maximum(self) -> None:
        compensation = self.digest["items"][0]["compensation"]
        compensation["minimum"] = 300000
        report = self.report()
        self.assert_error(report, "minimum must not exceed maximum")

    def test_compensation_currency_is_uppercase(self) -> None:
        self.digest["items"][0]["compensation"]["currency"] = "usd"
        report = self.report()
        self.assert_error(report, "uppercase ISO-style code")

    def test_unsupported_remote_policy_fails(self) -> None:
        self.digest["items"][0]["remote_policy"] = "sometimes"
        report = self.report()
        self.assert_error(report, "remote_policy is unsupported")

    def test_unsupported_role_family_fails(self) -> None:
        self.digest["items"][0]["role_family"] = "unrelated"
        report = self.report()
        self.assert_error(report, "role_family is unsupported")

    def test_fit_score_bounds_fail(self) -> None:
        self.digest["items"][0]["fit"]["score"] = 101
        report = self.report()
        self.assert_error(report, "score must be an integer in 0..100")

    def test_ranks_must_be_contiguous(self) -> None:
        self.digest["items"][1]["rank"] = 4
        report = self.report()
        self.assert_error(report, "ranks must be unique and contiguous")

    def test_ranks_must_follow_fit_score(self) -> None:
        self.digest["items"][3]["fit"]["score"] = 99
        report = self.report()
        self.assert_error(report, "must follow fit score descending")

    def test_fiducia_company_requires_two_or_three_roles(self) -> None:
        self.digest["items"] = [self.digest["items"][0], self.digest["items"][3]]
        report = self.report()
        self.assert_error(report, "must have exactly 2 or 3 job roles")

    def test_fiducia_opportunity_set_must_be_consistent(self) -> None:
        self.digest["items"][1]["fiducia_opportunity_ids"].append(
            "cloudforge-open-source-program"
        )
        report = self.report()
        self.assert_error(report, "one consistent Fiducia opportunity set")

    def test_personal_data_flag_fails_closed(self) -> None:
        self.digest["safety"]["contains_personal_data"] = True
        report = self.report()
        self.assert_error(report, "contains_personal_data must be false")

    def test_application_authorization_fails_closed(self) -> None:
        self.digest["safety"]["applications_authorized"] = True
        report = self.report()
        self.assert_error(report, "applications_authorized must be false")

    def test_forbidden_application_payload_field_fails(self) -> None:
        self.digest["items"][0]["application_payload"] = "redacted"
        report = self.report()
        self.assert_error(report, "application_payload is prohibited")

    def test_unknown_item_field_fails(self) -> None:
        self.digest["items"][0]["unexpected"] = True
        report = self.report()
        self.assert_error(report, "keys mismatch")

    def test_duplicate_json_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.json"
            path.write_text('{"kind":"opportunity","kind":"research"}')
            with self.assertRaisesRegex(MODULE.DigestError, "duplicate JSON key"):
                MODULE.load(path)

    def test_invalid_utf8_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.json"
            path.write_bytes(b"\xff\xfe")
            with self.assertRaisesRegex(MODULE.DigestError, "must be UTF-8"):
                MODULE.load(path)

    def test_oversized_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "large.json"
            path.write_bytes(b" " * (MODULE.MAX_BYTES + 1))
            with self.assertRaisesRegex(MODULE.DigestError, "exceeds"):
                MODULE.load(path)


class ResearchDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.digest = MODULE.load(FIXTURES / "research-digest-v1.json")

    def report(self) -> object:
        return MODULE.validate(self.digest)

    def assert_error(self, report: object, fragment: str) -> None:
        errors = getattr(report, "errors")
        self.assertTrue(
            any(fragment in error for error in errors),
            f"expected {fragment!r} in {errors!r}",
        )

    def test_valid_research_digest(self) -> None:
        report = self.report()
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.digest_kind, "research")
        self.assertEqual(report.item_count, 3)
        self.assertEqual(report.company_count, 0)
        self.assertEqual(report.fiducia_company_count, 0)
        self.assertRegex(report.digest_sha256 or "", r"^[0-9a-f]{64}$")

    def test_secondary_source_kind_fails(self) -> None:
        self.digest["items"][0]["source_kind"] = "news_article"
        report = self.report()
        self.assert_error(report, "recognized primary source")

    def test_withdrawn_record_fails_shortlist(self) -> None:
        self.digest["items"][0]["status"] = "withdrawn"
        report = self.report()
        self.assert_error(report, "status must be active")

    def test_duplicate_stable_identifier_fails(self) -> None:
        self.digest["items"][1]["stable_identifier"] = self.digest["items"][0][
            "stable_identifier"
        ]
        report = self.report()
        self.assert_error(report, "duplicate stable_identifier")

    def test_duplicate_url_fails(self) -> None:
        self.digest["items"][1]["canonical_url"] = self.digest["items"][0][
            "canonical_url"
        ]
        report = self.report()
        self.assert_error(report, "duplicate canonical_url")

    def test_publication_date_cannot_be_future(self) -> None:
        self.digest["items"][0]["publication_date"] = "2026-09-05"
        report = self.report()
        self.assert_error(report, "publication_date cannot be in the future")

    def test_update_cannot_follow_retrieval(self) -> None:
        self.digest["items"][0]["updated_at"] = "2026-09-04T09:11:00-04:00"
        report = self.report()
        self.assert_error(report, "updated_at cannot follow retrieved_at")

    def test_retrieval_before_window_fails(self) -> None:
        self.digest["items"][0]["retrieved_at"] = "2026-08-31T23:00:00-04:00"
        report = self.report()
        self.assert_error(report, "retrieved_at precedes the digest window")

    def test_unsupported_theme_fails(self) -> None:
        self.digest["items"][0]["themes"].append("unrelated-theme")
        report = self.report()
        self.assert_error(report, "themes[3] is unsupported")

    def test_claims_are_required(self) -> None:
        self.digest["items"][0]["claims"] = []
        report = self.report()
        self.assert_error(report, "claims must contain 1..24")

    def test_claim_fragment_digest_is_required(self) -> None:
        self.digest["items"][0]["claims"][0]["source_fragment_sha256"] = "short"
        report = self.report()
        self.assert_error(report, "must be a string of length 64..64")

    def test_interpretation_is_explicitly_required(self) -> None:
        self.digest["items"][0]["interpretation"] = []
        report = self.report()
        self.assert_error(report, "interpretation must contain 1..16")

    def test_uncertainty_is_explicitly_required(self) -> None:
        self.digest["items"][0]["uncertainty"] = []
        report = self.report()
        self.assert_error(report, "uncertainty must contain 1..16")

    def test_research_ranks_must_be_contiguous(self) -> None:
        self.digest["items"][1]["rank"] = 3
        report = self.report()
        self.assert_error(report, "ranks must be unique and contiguous")

    def test_research_ranks_follow_composite_score(self) -> None:
        self.digest["items"][2]["scores"]["relevance"] = 100
        report = self.report()
        self.assert_error(report, "must follow the deterministic composite score")

    def test_score_bounds_fail(self) -> None:
        self.digest["items"][0]["scores"]["novelty"] = -1
        report = self.report()
        self.assert_error(report, "novelty must be an integer in 0..100")

    def test_missing_score_fails_complete_ranking(self) -> None:
        del self.digest["items"][0]["scores"]["novelty"]
        report = self.report()
        self.assert_error(report, "complete deterministic scores")

    def test_unknown_superseded_item_fails(self) -> None:
        self.digest["items"][0]["supersedes"] = ["missing-item"]
        report = self.report()
        self.assert_error(report, "supersedes unknown item")

    def test_self_supersession_fails(self) -> None:
        item_id = self.digest["items"][0]["id"]
        self.digest["items"][0]["supersedes"] = [item_id]
        report = self.report()
        self.assert_error(report, "cannot supersede itself")

    def test_supersession_cycle_fails(self) -> None:
        first = self.digest["items"][0]
        second = self.digest["items"][1]
        first["supersedes"] = [second["id"]]
        second["supersedes"] = [first["id"]]
        report = self.report()
        self.assert_error(report, "supersession cycle")

    def test_external_mutation_flag_fails_closed(self) -> None:
        self.digest["safety"]["external_mutations_authorized"] = True
        report = self.report()
        self.assert_error(report, "external_mutations_authorized must be false")

    def test_forbidden_raw_source_field_fails(self) -> None:
        self.digest["items"][0]["raw_source"] = "redacted"
        report = self.report()
        self.assert_error(report, "raw_source is prohibited")

    def test_wrong_root_kind_fails(self) -> None:
        self.digest["kind"] = "commentary"
        report = self.report()
        self.assert_error(report, "kind must be opportunity or research")

    def test_generated_at_must_fall_inside_window(self) -> None:
        self.digest["generated_at"] = "2026-09-05T00:00:00-04:00"
        report = self.report()
        self.assert_error(report, "generated_at must fall inside the digest window")


if __name__ == "__main__":
    unittest.main()
