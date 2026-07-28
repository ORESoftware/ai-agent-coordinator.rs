#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import hmac
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pilot_github_push as pilot


class PilotGithubPushTests(unittest.TestCase):
    def test_builds_normalized_nonclosing_payload(self) -> None:
        payload = pilot.build_payload(
            organization="sonus-auris",
            repository="sonus-auris/sonus-auris-site.web",
            branch="main",
            commit="a" * 40,
            issue="den-455",
            keyword="refs",
        )
        self.assertEqual(payload["ref"], "refs/heads/main")
        self.assertEqual(payload["after"], "a" * 40)
        self.assertEqual(payload["commits"][0]["message"], "Refs DEN-455 pilot verification")

    def test_rejects_repository_owner_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "match --organization"):
            pilot.build_payload(
                organization="sonus-auris",
                repository="daedalus-fab/daedalus-clients",
                branch="main",
                commit="b" * 40,
                issue="DEN-455",
                keyword="refs",
            )

    def test_rejects_external_plain_http(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            pilot.validate_endpoint("http://example.com/webhooks/github")
        self.assertEqual(
            pilot.validate_endpoint("http://127.0.0.1:8080/webhooks/github"),
            "http://127.0.0.1:8080/webhooks/github",
        )

    def test_signature_matches_standard_hmac_and_preview_redacts_it(self) -> None:
        payload = pilot.build_payload(
            organization="daedalus-fab",
            repository="daedalus-fab/daedalus-clients",
            branch="main",
            commit="c" * 40,
            issue="DEN-455",
            keyword="fixes",
        )
        request = pilot.build_request(
            endpoint="https://coordinator.example/webhooks/github",
            secret="test-secret",
            payload=payload,
            delivery="ad39510e-cc97-4e4d-83e7-c570faeaac12",
        )
        expected = hmac.new(b"test-secret", request.body, hashlib.sha256).hexdigest()
        self.assertEqual(request.signature, f"sha256={expected}")
        preview = pilot.redacted_preview(request)
        self.assertEqual(
            preview["headers"]["x-hub-signature-256"],
            "[REDACTED:HMAC]",
        )
        self.assertNotIn("test-secret", str(preview))
        self.assertNotIn(expected, str(preview))

    def test_rejects_zero_commit_identifier(self) -> None:
        with self.assertRaisesRegex(ValueError, "nonzero"):
            pilot.validate_commit("0" * 40)


if __name__ == "__main__":
    unittest.main()
