#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import hmac
import io
import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pilot_github_push as pilot


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        return self.body if limit < 0 else self.body[:limit]


class PilotGithubPushTests(unittest.TestCase):
    def build_request(self, secret: str = "test-secret") -> pilot.PilotRequest:
        payload = pilot.build_payload(
            organization="daedalus-fab",
            repository="daedalus-fab/daedalus-clients",
            branch="main",
            commit="c" * 40,
            issue="DEN-455",
            keyword="fixes",
        )
        return pilot.build_request(
            endpoint="https://coordinator.example/webhooks/github",
            secret=secret,
            payload=payload,
            delivery="ad39510e-cc97-4e4d-83e7-c570faeaac12",
        )

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

    def test_rejects_external_plain_http_and_endpoint_credentials(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            pilot.validate_endpoint("http://example.com/webhooks/github")
        with self.assertRaisesRegex(ValueError, "credentials"):
            pilot.validate_endpoint("https://user:password@example.com/webhooks/github")
        with self.assertRaisesRegex(ValueError, "path"):
            pilot.validate_endpoint("https://example.com/not-the-webhook")
        self.assertEqual(
            pilot.validate_endpoint("http://127.0.0.1:8080/webhooks/github"),
            "http://127.0.0.1:8080/webhooks/github",
        )

    def test_signature_matches_standard_hmac_and_preview_redacts_it(self) -> None:
        request = self.build_request()
        expected = hmac.new(b"test-secret", request.body, hashlib.sha256).hexdigest()
        self.assertEqual(request.signature, f"sha256={expected}")
        preview = pilot.redacted_preview(request)
        self.assertEqual(
            preview["headers"]["x-hub-signature-256"],
            "[REDACTED:HMAC]",
        )
        self.assertNotIn("test-secret", str(preview))
        self.assertNotIn(expected, str(preview))

    def test_redacts_signature_digest_and_secret_from_success_response(self) -> None:
        request = self.build_request()
        digest = request.signature.removeprefix("sha256=")
        response = json.dumps(
            {
                "signature": request.signature,
                "digest": digest,
                "secret": "test-secret",
                "nested": [f"prefix:{request.signature}:suffix"],
            }
        ).encode("utf-8")
        with mock.patch.object(
            pilot.urllib.request,
            "urlopen",
            return_value=FakeResponse(response),
        ):
            result = pilot.send(
                request,
                5.0,
                sensitive_values=("test-secret",),
            )
        rendered = json.dumps(result)
        self.assertNotIn(request.signature, rendered)
        self.assertNotIn(digest, rendered)
        self.assertNotIn("test-secret", rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_redacts_signature_and_secret_from_http_error(self) -> None:
        request = self.build_request()
        digest = request.signature.removeprefix("sha256=")
        error = pilot.urllib.error.HTTPError(
            request.endpoint,
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(
                f"signature={request.signature}; digest={digest}; secret=test-secret".encode()
            ),
        )
        with mock.patch.object(pilot.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                pilot.send(request, 5.0, sensitive_values=("test-secret",))
        rendered = str(raised.exception)
        self.assertNotIn(request.signature, rendered)
        self.assertNotIn(digest, rendered)
        self.assertNotIn("test-secret", rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_rejects_oversized_response(self) -> None:
        request = self.build_request()
        with mock.patch.object(
            pilot.urllib.request,
            "urlopen",
            return_value=FakeResponse(b"x" * (pilot.MAX_RESPONSE_BYTES + 1)),
        ):
            with self.assertRaisesRegex(RuntimeError, "exceeded 64 KiB"):
                pilot.send(request, 5.0)

    def test_rejects_unsafe_git_branch_names(self) -> None:
        for branch in [
            ".hidden",
            "feature..branch",
            "feature//branch",
            "release.lock",
            "feature.",
            "/main",
            "main/",
        ]:
            with self.subTest(branch=branch):
                with self.assertRaises(ValueError):
                    pilot.validate_branch(branch)
        self.assertEqual(pilot.validate_branch("release/2026.07"), "release/2026.07")

    def test_rejects_invalid_secret_environment_name(self) -> None:
        for name in ["", "lowercase", "BAD-NAME", "1SECRET"]:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "environment variable name"):
                    pilot.validate_secret_environment_name(name)
        self.assertEqual(
            pilot.validate_secret_environment_name("GITHUB_WEBHOOK_SECRET_SONUS_AURIS"),
            "GITHUB_WEBHOOK_SECRET_SONUS_AURIS",
        )

    def test_rejects_zero_commit_identifier(self) -> None:
        with self.assertRaisesRegex(ValueError, "nonzero"):
            pilot.validate_commit("0" * 40)


if __name__ == "__main__":
    unittest.main()
