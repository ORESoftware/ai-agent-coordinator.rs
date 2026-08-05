#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


COMPOSER = load_module(
    "compose_daily_portfolio_briefing",
    ROOT / "tools" / "compose_daily_portfolio_briefing.py",
)
SCHEDULER = load_module(
    "enqueue_daily_portfolio_briefing",
    ROOT / "tools" / "enqueue_daily_portfolio_briefing.py",
)
FIXTURE = ROOT / "tests" / "fixtures" / "daily_portfolio_briefing_input.json"


def fixture_input() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def scheduled_run(
    run_key: str = "daily-portfolio:scheduled:2026-07-31",
    *,
    generated_at: str = "2026-07-31T13:02:00Z",
    recovered: bool = False,
):
    return COMPOSER.build_run_context(
        mode="scheduled",
        run_key=run_key,
        scheduled_run_key=run_key,
        scheduled_for="2026-07-31T13:00:00Z",
        generated_at=generated_at,
        timezone_name="America/Chicago",
        manual_id=None,
        recovered=recovered,
    )


def manual_run(manual_id: str = "review-1"):
    return COMPOSER.build_run_context(
        mode="manual",
        run_key=f"daily-portfolio:manual:2026-07-31:{manual_id}",
        scheduled_run_key="daily-portfolio:scheduled:2026-07-31",
        scheduled_for="2026-07-31T13:00:00Z",
        generated_at="2026-07-31T15:00:00Z",
        timezone_name="America/Chicago",
        manual_id=manual_id,
        recovered=False,
    )


def make_item(index: int, *, disposition: str = "monitor") -> dict:
    return {
        "identity": f"synthetic:item-{index:02d}",
        "title": f"Synthetic portfolio item {index:02d}",
        "what_changed": f"Synthetic item {index:02d} changed.",
        "why_it_matters": f"Synthetic item {index:02d} matters.",
        "confidence": "high",
        "source_status": "confirmed",
        "relevant_date": f"2026-08-{(index % 20) + 1:02d}",
        "next_action": f"Review synthetic item {index:02d}.",
        "sources": [
            {
                "label": "Synthetic source",
                "url": f"https://example.invalid/items/{index}?secret=removed",
            }
        ],
        "disposition": disposition,
        "rank": {
            "deadline_risk": index % 6,
            "blocking_impact": (index + 1) % 6,
            "project_priority": (index + 2) % 6,
            "expected_value": (index + 3) % 6,
            "reversibility": (index + 4) % 6,
        },
        "material": {
            "status": "open",
            "urgency": "synthetic",
            "evidence": f"fixture-{index}",
            "deadline": f"2026-08-{(index % 20) + 1:02d}",
            "owner": "synthetic",
            "recommended_action": f"Review synthetic item {index:02d}.",
            "coverage_state": "covered",
        },
    }


class PortfolioComposerTests(unittest.TestCase):
    def test_composes_eight_lanes_with_ranked_top_three_and_bounded_output(self) -> None:
        payload = fixture_input()
        payload["lanes"]["ai_technology"]["items"] = [
            make_item(index, disposition="monitor" if index % 2 else "do_today")
            for index in range(20)
        ]
        plan = COMPOSER.compose_briefing(
            payload,
            COMPOSER.empty_state(),
            scheduled_run(),
        )
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["counts"]["selected"], COMPOSER.MAX_ITEMS)
        self.assertEqual(len(plan["items"]), COMPOSER.MAX_ITEMS)
        self.assertEqual(len(plan["priorities"]), 3)
        scores = [item["ranking"]["score"] for item in plan["items"]]
        dispositions = [item["disposition"] for item in plan["items"]]
        self.assertEqual(
            list(zip([COMPOSER.DISPOSITION_ORDER[value] for value in dispositions], [-score for score in scores])),
            sorted(
                zip(
                    [COMPOSER.DISPOSITION_ORDER[value] for value in dispositions],
                    [-score for score in scores],
                )
            ),
        )
        self.assertIn("## Do today", plan["markdown"])
        self.assertIn("## Monitor", plan["markdown"])
        self.assertIn("## Ignore", plan["markdown"])
        self.assertTrue(plan["markdown"].rstrip().endswith("item(s)."))

    def test_confirmed_inference_dates_actions_and_sanitized_links_are_rendered(self) -> None:
        plan = COMPOSER.compose_briefing(
            fixture_input(),
            COMPOSER.empty_state(),
            scheduled_run(),
        )
        markdown = plan["markdown"]
        self.assertIn("confirmed fact", markdown)
        self.assertIn("inference", markdown)
        self.assertIn("relevant date 2026-07-31", markdown)
        self.assertIn("**Next action:**", markdown)
        self.assertIn("https://linear.app/", markdown)
        self.assertNotIn("utm_source", markdown)

    def test_scheduled_commit_suppresses_unchanged_items_and_lane_failure(self) -> None:
        payload = fixture_input()
        first = COMPOSER.compose_briefing(
            payload,
            COMPOSER.empty_state(),
            scheduled_run(),
        )
        state = COMPOSER.commit_delivery(
            COMPOSER.empty_state(),
            first,
            "2026-07-31T13:05:00Z",
        )
        second = COMPOSER.compose_briefing(
            payload,
            state,
            scheduled_run(
                "daily-portfolio:scheduled:2026-08-01",
                generated_at="2026-08-01T13:00:00Z",
            ),
        )
        self.assertEqual(second["status"], "no_changes")
        self.assertEqual(second["counts"]["unchanged_suppressed"], 5)
        self.assertEqual(second["lane_failures"], [])

    def test_material_change_reemits_only_changed_identity(self) -> None:
        payload = fixture_input()
        first = COMPOSER.compose_briefing(
            payload,
            COMPOSER.empty_state(),
            scheduled_run(),
        )
        state = COMPOSER.commit_delivery(
            COMPOSER.empty_state(),
            first,
            "2026-07-31T13:05:00Z",
        )
        changed = fixture_input()
        target = changed["lanes"]["career"]["items"][0]
        target["material"]["status"] = "interview"
        target["material"]["evidence"] = "confirmed interview invitation"
        target["what_changed"] = "The role advanced to a confirmed interview."
        target["next_action"] = "Prepare the interview evidence packet."
        target["material"]["recommended_action"] = target["next_action"]
        next_plan = COMPOSER.compose_briefing(
            changed,
            state,
            scheduled_run("daily-portfolio:scheduled:2026-08-01"),
        )
        self.assertEqual(next_plan["counts"]["selected"], 1)
        self.assertEqual(next_plan["items"][0]["identity"], "career:role-1")

    def test_manual_commit_does_not_advance_scheduled_baseline(self) -> None:
        state = COMPOSER.empty_state()
        plan = COMPOSER.compose_briefing(fixture_input(), state, manual_run())
        committed = COMPOSER.commit_delivery(
            state,
            plan,
            "2026-07-31T15:02:00Z",
        )
        self.assertIsNone(committed["scheduled_baseline"]["run_key"])
        self.assertIn(manual_run().run_key, committed["deliveries"])

    def test_duplicate_delivery_is_noop_and_digest_conflict_fails_closed(self) -> None:
        payload = fixture_input()
        run = scheduled_run()
        plan = COMPOSER.compose_briefing(payload, COMPOSER.empty_state(), run)
        state = COMPOSER.commit_delivery(
            COMPOSER.empty_state(),
            plan,
            "2026-07-31T13:05:00Z",
        )
        duplicate = COMPOSER.compose_briefing(payload, state, run)
        self.assertEqual(duplicate["status"], "duplicate_delivery")
        self.assertEqual(duplicate["items"], [])
        self.assertIn("No duplicate delivery is permitted", duplicate["markdown"])

        changed = fixture_input()
        changed["lanes"]["career"]["items"][0]["material"]["status"] = "changed"
        with self.assertRaisesRegex(COMPOSER.BriefingError, "different material source state"):
            COMPOSER.compose_briefing(changed, state, run)

    def test_identity_conflict_across_lanes_fails_closed(self) -> None:
        payload = fixture_input()
        duplicate = copy.deepcopy(payload["lanes"]["career"]["items"][0])
        duplicate["material"]["status"] = "conflicting"
        payload["lanes"]["business_growth"]["items"] = [duplicate]
        with self.assertRaisesRegex(COMPOSER.BriefingError, "conflicting material facts"):
            COMPOSER.compose_briefing(payload, COMPOSER.empty_state(), scheduled_run())

    def test_identical_identity_across_lanes_merges_sources(self) -> None:
        payload = fixture_input()
        duplicate = copy.deepcopy(payload["lanes"]["career"]["items"][0])
        duplicate["sources"].append(
            {"label": "GitHub", "url": "https://github.com/ORESoftware/example"}
        )
        payload["lanes"]["business_growth"]["items"] = [duplicate]
        plan = COMPOSER.compose_briefing(
            payload,
            COMPOSER.empty_state(),
            scheduled_run(),
        )
        merged = next(item for item in plan["items"] if item["identity"] == "career:role-1")
        self.assertEqual(merged["lanes"], ["business_growth", "career"])
        self.assertEqual(len(merged["sources"]), 2)

    def test_lane_failure_is_isolated_and_redacted(self) -> None:
        payload = fixture_input()
        payload["lanes"]["ai_technology"].update(
            {
                "status": "unavailable",
                "error_summary": "provider token=should-not-leak was unavailable",
                "items": [],
            }
        )
        plan = COMPOSER.compose_briefing(
            payload,
            COMPOSER.empty_state(),
            scheduled_run(),
        )
        failure = next(
            item for item in plan["lane_failures"] if item["lane"] == "ai_technology"
        )
        self.assertIn("[REDACTED]", failure["error_summary"])
        self.assertNotIn("should-not-leak", plan["markdown"])
        self.assertTrue(plan["items"])

    def test_exact_eight_lane_and_child_issue_contracts_are_enforced(self) -> None:
        missing = fixture_input()
        missing["lanes"].pop("prompt_coverage")
        with self.assertRaisesRegex(COMPOSER.BriefingError, "exactly eight lanes"):
            COMPOSER.normalize_input(missing)

        wrong_issue = fixture_input()
        wrong_issue["lanes"]["career"]["source_issue"] = "DEN-999"
        with self.assertRaisesRegex(COMPOSER.BriefingError, "must be DEN-826"):
            COMPOSER.normalize_input(wrong_issue)

    def test_unknown_fields_fail_instead_of_widening_ingestion(self) -> None:
        payload = fixture_input()
        payload["lanes"]["career"]["items"][0]["raw_email_body"] = "private"
        with self.assertRaisesRegex(COMPOSER.BriefingError, "unknown fields"):
            COMPOSER.normalize_input(payload)


class PortfolioSchedulerTests(unittest.TestCase):
    def test_default_schedule_handles_daylight_and_standard_time(self) -> None:
        summer = SCHEDULER.schedule_decision(
            datetime.fromisoformat("2026-07-31T13:00:00+00:00")
        )
        winter = SCHEDULER.schedule_decision(
            datetime.fromisoformat("2026-12-01T14:00:00+00:00")
        )
        self.assertTrue(summer.due)
        self.assertTrue(winter.due)
        self.assertEqual(summer.local_time.hour, 8)
        self.assertEqual(winter.local_time.hour, 8)

    def test_one_hour_recovery_reuses_scheduled_idempotency_key(self) -> None:
        initial = SCHEDULER.schedule_decision(
            datetime.fromisoformat("2026-07-31T13:00:00+00:00")
        )
        recovery = SCHEDULER.schedule_decision(
            datetime.fromisoformat("2026-07-31T14:00:00+00:00")
        )
        too_late = SCHEDULER.schedule_decision(
            datetime.fromisoformat("2026-07-31T15:00:00+00:00")
        )
        self.assertTrue(recovery.due)
        self.assertTrue(recovery.recovered)
        self.assertEqual(initial.run_key, recovery.run_key)
        self.assertFalse(too_late.due)

    def test_manual_run_has_distinct_identity_without_changing_scheduled_key(self) -> None:
        decision = SCHEDULER.schedule_decision(
            datetime.fromisoformat("2026-07-31T15:00:00+00:00"),
            force=True,
            manual_id="review-1",
        )
        self.assertEqual(decision.kind, "manual")
        self.assertNotEqual(decision.run_key, decision.scheduled_run_key)
        self.assertEqual(
            decision.scheduled_run_key,
            "daily-portfolio:scheduled:2026-07-31",
        )

    def test_payload_has_eight_failure_isolated_lanes_and_no_external_writes(self) -> None:
        decision = SCHEDULER.schedule_decision(
            datetime.fromisoformat("2026-07-31T13:00:00+00:00")
        )
        payload = SCHEDULER.build_payload(
            decision,
            timezone_name="America/Chicago",
            local_time="08:00",
            recovery_minutes=60,
        )
        inner = payload["payload"]
        self.assertEqual(len(inner["source_lanes"]), 8)
        self.assertTrue(all(lane["failure_isolated"] for lane in inner["source_lanes"]))
        self.assertTrue(inner["safety"]["external_writes_disabled"])
        self.assertIn("DEN-834", inner["linear"]["source_issues"])
        self.assertIn("merge_pull_request", inner["forbidden_actions"])

    def test_dry_run_redacts_authorization(self) -> None:
        decision = SCHEDULER.schedule_decision(
            datetime.fromisoformat("2026-07-31T13:00:00+00:00")
        )
        payload = SCHEDULER.build_payload(
            decision,
            timezone_name="America/Chicago",
            local_time="08:00",
            recovery_minutes=60,
        )
        plan = SCHEDULER.redacted_plan(
            "https://coordinator.example.invalid",
            decision,
            payload,
        )
        self.assertEqual(plan["authorization"], "Bearer [REDACTED]")
        self.assertEqual(plan["idempotency_key"], decision.run_key)


if __name__ == "__main__":
    unittest.main()
