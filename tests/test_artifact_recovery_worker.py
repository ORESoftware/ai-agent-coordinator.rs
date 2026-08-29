from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_artifact_recovery_worker",
    ROOT / "tools" / "run_artifact_recovery_worker.py",
)
assert SPEC is not None and SPEC.loader is not None
worker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = worker
SPEC.loader.exec_module(worker)


NOW = datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc)


def item(origin_id: str = "chat-1", note: str | None = None) -> dict:
    value = {
        "origin": {
            "source": "chatgpt",
            "id": origin_id,
            "id_kind": "chat_id",
            "observed_at": "2026-08-11T03:55:00Z",
        },
        "target": {
            "owner": "ORESoftware",
            "repository": "ai-agent-coordinator.rs",
            "visibility": "private",
            "artifact_kind": "code",
            "ownership_resolved": True,
        },
        "intent": {
            "artifact_expected": True,
            "base_branch": "main",
            "branch": "agent/test",
            "pull_request_required": True,
            "allow_repository_creation": False,
        },
        "local": {
            "artifact": None,
            "git_repository": False,
            "remote_present": False,
            "branch": None,
            "branches": [],
            "head_sha": None,
            "dirty_paths": [],
        },
        "remote": {
            "collected": True,
            "repository": {
                "exists": True,
                "visibility": "private",
                "default_branch": "main",
                "url": "https://github.com/ORESoftware/ai-agent-coordinator.rs",
            },
            "branches": [
                {
                    "name": "agent/test",
                    "sha": "a" * 40,
                }
            ],
            "commits": [
                {
                    "sha": "a" * 40,
                    "url": (
                        "https://github.com/ORESoftware/"
                        "ai-agent-coordinator.rs/commit/" + "a" * 40
                    ),
                }
            ],
            "pull_requests": [
                {
                    "number": 1,
                    "url": (
                        "https://github.com/ORESoftware/"
                        "ai-agent-coordinator.rs/pull/1"
                    ),
                    "head": "agent/test",
                    "base": "main",
                    "state": "open",
                    "draft": True,
                }
            ],
        },
        "claims": {
            "repository_url": (
                "https://github.com/ORESoftware/ai-agent-coordinator.rs"
            ),
            "commit_sha": "a" * 40,
            "branch": "agent/test",
            "pull_request_url": (
                "https://github.com/ORESoftware/"
                "ai-agent-coordinator.rs/pull/1"
            ),
        },
        "note": note,
    }
    return value


def manifest_value() -> dict:
    return {
        "schema_version": worker.SOURCE_MANIFEST_SCHEMA,
        "required_sources": ["chatgpt"],
        "optional_sources": ["claude"],
        "sources": [
            {
                "source": "chatgpt",
                "endpoint": "https://chat-source.example.invalid/v1/pages",
                "token_env": "CHATGPT_RECOVERY_SOURCE_TOKEN",
                "identity": "chatgpt-primary",
                "capabilities": ["history", "pagination", "unresolved"],
                "max_age_seconds": 900,
            },
            {
                "source": "claude",
                "endpoint": "https://claude-source.example.invalid/v1/pages",
                "token_env": "CLAUDE_RECOVERY_SOURCE_TOKEN",
                "identity": "claude-export",
                "capabilities": ["exports", "pagination"],
                "max_age_seconds": 3600,
            },
        ],
    }


def job_value(task_type: str = worker.TASK_TYPE) -> dict:
    return {
        "id": "job-123",
        "org": "ORESoftware",
        "repo": "ai-agent-coordinator.rs",
        "task_type": task_type,
        "claimed_by": "worker-1",
        "payload": {
            "schema_version": worker.JOB_SCHEMA,
            "run": {
                "run_key": "artifact-recovery:scheduled:2026-08-11",
                "scheduled_for": "2026-08-11T07:00:00Z",
            },
            "tracking": {
                "local_cli_task_id": "019fd526-f34d-7f72-94fa-2da6185f2d74",
            },
            "source_contract": {
                "scan_all_accessible_authorized_chatgpt_threads": True,
                "scan_authorized_claude_session_exports": True,
            },
            "ledger_contract": {},
            "detection_contract": {},
            "delivery_contract": {},
            "allowed_actions": [],
            "forbidden_actions": [
                "reuse_chat_pasted_credentials",
                "force_push",
                "direct_default_branch_write",
                "bypass_protection",
                "auto_merge",
                "claim_delivery_without_remote_evidence",
            ],
        },
    }


def config(tmp: Path) -> worker.WorkerConfig:
    return worker.WorkerConfig(
        coordinator_url="https://coordinator.example.invalid",
        coordinator_token="coordinator-test-token",
        source_manifest=tmp / "sources.json",
        state_dir=tmp / "state",
        worker_id="worker-1",
        lease_seconds=60,
        request_timeout_seconds=5,
        max_response_bytes=1024 * 1024,
        max_pages_per_source=5,
        source_page_size=50,
        retry_delay_seconds=60,
        poll_seconds=1,
        once=True,
    )


class ScriptedHttp:
    def __init__(self, responses: list[tuple[int, dict | None]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


class WorkerBoundaryTests(unittest.TestCase):
    def test_url_policy_rejects_credentials_queries_and_remote_http(self) -> None:
        for value in (
            "https://user:password@example.com",
            "https://example.com/path?token=value",
            "http://example.com/path",
            "file:///etc/passwd",
        ):
            with self.subTest(value=value):
                with self.assertRaises(worker.WorkerError):
                    worker.validate_base_url(value, "endpoint")
        self.assertEqual(
            worker.validate_base_url("http://127.0.0.1:8080/worker", "endpoint"),
            "http://127.0.0.1:8080/worker",
        )

    def test_manifest_requires_exact_adapter_set(self) -> None:
        value = manifest_value()
        value["sources"].pop()
        with self.assertRaisesRegex(worker.WorkerError, "adapter mismatch"):
            worker.SourceManifest.from_value(value)

    def test_manifest_enforces_required_chatgpt_contract(self) -> None:
        value = manifest_value()
        value["required_sources"] = ["claude"]
        value["optional_sources"] = ["chatgpt"]
        manifest = worker.SourceManifest.from_value(value)
        with self.assertRaisesRegex(worker.WorkerError, "requires ChatGPT"):
            manifest.enforce_job_contract(job_value()["payload"])

    def test_public_config_summary_never_contains_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config(Path(directory))
            encoded = json.dumps(cfg.public_summary())
            self.assertNotIn(cfg.coordinator_token, encoded)
            self.assertIn(worker.REDACTED, encoded)
            worker.ensure_public_safe(cfg.public_summary())

    def test_validate_job_requires_all_forbidden_guardrails(self) -> None:
        value = job_value()
        value["payload"]["forbidden_actions"].remove("auto_merge")
        with self.assertRaisesRegex(worker.WorkerError, "mandatory forbidden"):
            worker.validate_job(value)

    def test_validate_job_rejects_any_other_task_type(self) -> None:
        with self.assertRaisesRegex(worker.WorkerError, "only artifact_recovery"):
            worker.validate_job(job_value("github_push"))

    def test_atomic_write_refuses_obvious_secret_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            with self.assertRaisesRegex(worker.WorkerError, "credential-like"):
                worker.atomic_write_json(path, {"value": "ghp_example"})
            self.assertFalse(path.exists())


class CoordinatorClientTests(unittest.TestCase):
    def test_claim_filters_to_one_exact_task_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            http = ScriptedHttp([(200, {"job": job_value()})])
            client = worker.CoordinatorClient(config(Path(directory)), http)
            claimed = client.claim()
            self.assertEqual(claimed["id"], "job-123")
            payload = http.calls[0]["payload"]
            self.assertEqual(payload["task_types"], [worker.TASK_TYPE])
            self.assertNotIn("coordinator-test-token", json.dumps(payload))

    def test_claim_rejects_misrouted_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wrong = job_value("github_push")
            http = ScriptedHttp([(200, {"job": wrong})])
            client = worker.CoordinatorClient(config(Path(directory)), http)
            with self.assertRaisesRegex(worker.WorkerError, "unsupported task type"):
                client.claim()


class SourceCollectorTests(unittest.TestCase):
    def _spec(self) -> worker.SourceSpec:
        return worker.SourceManifest.from_value(manifest_value()).specs[0]

    def test_missing_source_token_produces_unauthorized_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            collector = worker.SourceCollector(
                http=ScriptedHttp([]),
                config=config(Path(directory)),
                environment={},
            )
            result = collector.collect(
                self._spec(),
                window_start=NOW.replace(hour=0),
                window_end=NOW,
                prior_high_water_mark=None,
            )
            self.assertEqual(result.receipt["state"], "unauthorized")
            self.assertFalse(result.receipt["pagination"]["started"])
            self.assertEqual(result.items, ())

    def test_paginated_collection_is_bounded_and_complete(self) -> None:
        page_one = {
            "schema_version": worker.SOURCE_PAGE_SCHEMA,
            "source": "chatgpt",
            "captured_at": "2026-08-11T04:00:00Z",
            "watermark_at": "2026-08-11T03:59:00Z",
            "items": [item("chat-1")],
            "complete": False,
            "next_cursor": "page-2",
        }
        page_two = {
            "schema_version": worker.SOURCE_PAGE_SCHEMA,
            "source": "chatgpt",
            "captured_at": "2026-08-11T04:00:00Z",
            "watermark_at": "2026-08-11T04:00:00Z",
            "items": [item("chat-2")],
            "complete": True,
            "next_cursor": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            http = ScriptedHttp([(200, page_one), (200, page_two)])
            collector = worker.SourceCollector(
                http=http,
                config=config(Path(directory)),
                environment={"CHATGPT_RECOVERY_SOURCE_TOKEN": "opaque-test-token"},
            )
            result = collector.collect(
                self._spec(),
                window_start=NOW.replace(hour=0),
                window_end=NOW,
                prior_high_water_mark="2026-08-10T04:00:00Z",
            )
            self.assertEqual(len(result.items), 2)
            self.assertEqual(result.receipt["state"], "complete")
            self.assertEqual(result.receipt["pagination"]["pages_read"], 2)
            self.assertIn("cursor=page-2", http.calls[1]["url"])
            self.assertIn("include_unresolved=true", http.calls[0]["url"])
            self.assertNotIn("opaque-test-token", json.dumps(result.receipt))

    def test_repeated_cursor_fails_closed_as_partial(self) -> None:
        repeated = {
            "schema_version": worker.SOURCE_PAGE_SCHEMA,
            "source": "chatgpt",
            "captured_at": "2026-08-11T04:00:00Z",
            "watermark_at": "2026-08-11T03:59:00Z",
            "items": [item("chat-1")],
            "complete": False,
            "next_cursor": "same",
        }
        with tempfile.TemporaryDirectory() as directory:
            http = ScriptedHttp([(200, repeated), (200, repeated)])
            collector = worker.SourceCollector(
                http=http,
                config=config(Path(directory)),
                environment={"CHATGPT_RECOVERY_SOURCE_TOKEN": "opaque-test-token"},
            )
            result = collector.collect(
                self._spec(),
                window_start=NOW.replace(hour=0),
                window_end=NOW,
                prior_high_water_mark=None,
            )
            self.assertEqual(result.receipt["state"], "partial")
            self.assertEqual(result.receipt["error_class"], "pagination")
            self.assertFalse(result.receipt["pagination"]["complete"])

    def test_complete_page_cannot_smuggle_next_cursor(self) -> None:
        bad = {
            "schema_version": worker.SOURCE_PAGE_SCHEMA,
            "source": "chatgpt",
            "captured_at": "2026-08-11T04:00:00Z",
            "watermark_at": "2026-08-11T04:00:00Z",
            "items": [],
            "complete": True,
            "next_cursor": "more",
        }
        with tempfile.TemporaryDirectory() as directory:
            collector = worker.SourceCollector(
                http=ScriptedHttp([(200, bad)]),
                config=config(Path(directory)),
                environment={"CHATGPT_RECOVERY_SOURCE_TOKEN": "opaque-test-token"},
            )
            result = collector.collect(
                self._spec(),
                window_start=NOW.replace(hour=0),
                window_end=NOW,
                prior_high_water_mark=None,
            )
            self.assertEqual(result.receipt["state"], "partial")
            self.assertEqual(result.receipt["error_class"], "pagination")


class ReconciliationReceiptTests(unittest.TestCase):
    def test_duplicate_items_are_idempotent_but_conflicts_are_rejected(self) -> None:
        first = item("chat-1")
        duplicate = json.loads(json.dumps(first))
        self.assertEqual(len(worker._deduplicate_items([first, duplicate])), 1)
        conflict = json.loads(json.dumps(first))
        conflict["note"] = "different"
        with self.assertRaisesRegex(worker.WorkerError, "conflicting duplicate"):
            worker._deduplicate_items([first, conflict])

    def test_required_unauthorized_source_blocks_coverage(self) -> None:
        manifest = worker.SourceManifest.from_value(manifest_value())
        chat = manifest.specs[0]
        claude = manifest.specs[1]
        collector = worker.SourceCollector(
            http=ScriptedHttp([]),
            config=config(Path("/tmp")),
            environment={},
        )
        chat_result = collector.collect(
            chat,
            window_start=NOW.replace(hour=0),
            window_end=NOW,
            prior_high_water_mark=None,
        )
        claude_result = collector.collect(
            claude,
            window_start=NOW.replace(hour=0),
            window_end=NOW,
            prior_high_water_mark=None,
        )
        coverage = worker.build_coverage(
            manifest,
            [chat_result, claude_result],
            generated_at=NOW,
        )
        self.assertEqual(coverage["summary"]["status"], "blocked")
        self.assertEqual(coverage["summary"]["blocked_sources"], ["chatgpt"])
        self.assertEqual(len(coverage["report_sha256"]), 64)


class ReportDeliveryTests(unittest.TestCase):
    def _completion(self) -> dict:
        return {
            "schema_version": worker.COMPLETION_SCHEMA,
            "job_id": "job-123",
            "run_key_sha256": "a" * 64,
            "worker_id_sha256": "b" * 64,
            "started_at": "2026-08-11T03:00:00Z",
            "completed_at": "2026-08-11T04:00:00Z",
            "outcome": "succeeded",
            "source_coverage": {"status": "complete"},
            "summary": {
                "entries": 3,
                "complete": 3,
                "excluded": 0,
                "actionable": 0,
                "blocked": 0,
            },
        }

    def test_report_message_contains_only_receipt_summary(self) -> None:
        message = worker.build_report_email(
            recipient="ops@example.com",
            sender="coordinator@example.com",
            completion=self._completion(),
        )
        rendered = message.as_string()
        self.assertIn("Outcome: succeeded", rendered)
        self.assertNotIn("Authorization", rendered)
        worker.ensure_public_safe(rendered)

    def test_configured_report_requires_smtp_host(self) -> None:
        with self.assertRaisesRegex(worker.WorkerError, "SMTP_HOST"):
            worker.deliver_report_email(
                self._completion(),
                environment={
                    "ARTIFACT_RECOVERY_REPORT_TO": "ops@example.com",
                    "ARTIFACT_RECOVERY_REPORT_FROM": "coordinator@example.com",
                },
            )

    def test_unencrypted_remote_smtp_is_rejected(self) -> None:
        with self.assertRaisesRegex(worker.WorkerError, "only on loopback"):
            worker.deliver_report_email(
                self._completion(),
                environment={
                    "ARTIFACT_RECOVERY_REPORT_TO": "ops@example.com",
                    "ARTIFACT_RECOVERY_REPORT_FROM": "coordinator@example.com",
                    "ARTIFACT_RECOVERY_SMTP_HOST": "smtp.example.com",
                    "ARTIFACT_RECOVERY_SMTP_SECURITY": "none",
                },
            )


class HttpBoundaryTests(unittest.TestCase):
    def test_http_redirect_is_not_followed(self) -> None:
        class RedirectingOpener:
            def open(self, request, timeout):
                raise HTTPError(
                    request.full_url,
                    302,
                    "Found",
                    {},
                    None,
                )

        client = worker.JsonHttpClient(
            timeout_seconds=1,
            max_response_bytes=1024,
            opener=RedirectingOpener(),
        )
        with self.assertRaisesRegex(worker.WorkerError, "HTTP 302"):
            client.request(
                "GET",
                "https://source.example.invalid/v1/pages",
                token="opaque-token",
                user_agent="test",
            )


if __name__ == "__main__":
    unittest.main()
