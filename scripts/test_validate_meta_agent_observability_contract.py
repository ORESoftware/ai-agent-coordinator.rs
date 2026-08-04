#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("validate_meta_agent_observability_contract.py")
SPEC = importlib.util.spec_from_file_location("meta_agent_contract", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class MetaAgentObservabilityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = MODULE.validate_policy(MODULE.load_json(MODULE.POLICY_PATH))
        cls.valid = MODULE.load_json(
            MODULE.FIXTURE_ROOT / "valid" / "task-progress-http.json"
        )

    def assert_code(self, event: dict, code: str) -> None:
        with self.assertRaises(MODULE.ContractViolation) as context:
            MODULE.validate_event(event, self.policy)
        self.assertEqual(context.exception.code, code)

    def test_complete_report_is_deterministic(self) -> None:
        first = MODULE.build_report()
        second = MODULE.build_report()
        self.assertEqual(first, second)
        self.assertEqual(first["event_kind_count"], 42)
        self.assertEqual(first["udp_event_kind_count"], 5)
        self.assertEqual(first["valid_fixture_count"], 3)
        self.assertEqual(first["invalid_fixture_count"], 3)
        self.assertEqual(first["idempotency"]["identical_replay"], "duplicate")

    def test_unknown_top_level_field_is_rejected(self) -> None:
        event = copy.deepcopy(self.valid)
        event["unexpected"] = True
        self.assert_code(event, "unknown-field")

    def test_unknown_event_kind_is_rejected(self) -> None:
        event = copy.deepcopy(self.valid)
        event["kind"] = "future_unversioned_kind"
        self.assert_code(event, "unknown-event-kind")

    def test_hidden_reasoning_is_rejected_at_any_depth(self) -> None:
        event = copy.deepcopy(self.valid)
        event["payload"] = {"summary": {"private_reasoning": "forbidden"}}
        self.assert_code(event, "forbidden-field")

    def test_secret_shaped_field_is_rejected_at_any_depth(self) -> None:
        event = copy.deepcopy(self.valid)
        event["payload"] = {"connection": {"access_token": "redacted"}}
        self.assert_code(event, "secret-shaped-field")

    def test_udp_cannot_carry_human_control(self) -> None:
        event = copy.deepcopy(self.valid)
        event["kind"] = "human_rejected"
        event["delivery"] = {
            "transport": "udp",
            "delivery_id": "udp-human-control",
            "attempt": 1,
            "ack_requested": False,
        }
        self.assert_code(event, "udp-event-kind-forbidden")

    def test_udp_cannot_claim_ack_or_sequence(self) -> None:
        event = MODULE.load_json(
            MODULE.FIXTURE_ROOT / "valid" / "heartbeat-udp.json"
        )
        event["delivery"]["ack_requested"] = True
        self.assert_code(event, "udp-ack-forbidden")
        event["delivery"]["ack_requested"] = False
        event["delivery"]["sequence"] = 1
        self.assert_code(event, "udp-sequence-forbidden")

    def test_idempotency_is_apply_duplicate_conflict(self) -> None:
        seen: dict[str, str] = {}
        event = copy.deepcopy(self.valid)
        self.assertEqual(MODULE.apply_idempotently(event, self.policy, seen), "applied")
        self.assertEqual(
            MODULE.apply_idempotently(copy.deepcopy(event), self.policy, seen),
            "duplicate",
        )
        changed = copy.deepcopy(event)
        changed["event_id"] = "2df89d32-f31f-47f3-8a12-bda5b716dc9c"
        with self.assertRaises(MODULE.ContractViolation) as context:
            MODULE.apply_idempotently(changed, self.policy, seen)
        self.assertEqual(context.exception.code, "idempotency-conflict")

    def test_duplicate_json_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"event_id":"one","event_id":"two"}', encoding="utf-8")
            with self.assertRaises(MODULE.ContractViolation) as context:
                MODULE.load_json(path)
        self.assertEqual(context.exception.code, "duplicate-json-key")

    def test_payload_and_event_byte_ceilings(self) -> None:
        payload_event = copy.deepcopy(self.valid)
        payload_event["payload"] = {"summary": "x" * self.policy["max_payload_bytes"]}
        self.assert_code(payload_event, "payload-too-large")
        event = copy.deepcopy(self.valid)
        event["source"]["metadata"] = {
            f"key{index}": "x" * 512
            for index in range(self.policy["max_metadata_entries"])
        }
        event["payload"] = {"summary": "x" * 31_000}
        event["evidence_references"] = [
            {
                "kind": "artifact",
                "reference": f"artifact-{index}:" + ("x" * 2_000),
                "sha256": f"{index:064x}",
            }
            for index in range(self.policy["max_evidence_references"])
        ]
        self.assert_code(event, "event-too-large")


if __name__ == "__main__":
    unittest.main()
