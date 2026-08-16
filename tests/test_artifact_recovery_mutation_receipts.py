from __future__ import annotations
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))
from artifact_recovery.common import RecoveryError, canonical_json, sha256_value
from artifact_recovery.mutation_receipts import SCHEMA_VERSION, build_example_mutation_receipts, build_mutation_receipts_report
NOW = '2026-08-10T20:00:00Z'

class MutationReceiptsTests(unittest.TestCase):

    def digest(self, *parts: str) -> str:
        return sha256_value({'fixture': list(parts)})

    def raw(self) -> dict:
        report = build_example_mutation_receipts(now=NOW)
        return {key: copy.deepcopy(report[key]) for key in ('schema_version', 'generated_at', 'policy', 'targets', 'intents', 'results')}

    def reseal_chain(self, intent: dict) -> None:
        chain = intent['chain']
        material = {key: chain[key] for key in ('workflow_sha256', 'step', 'sequence', 'previous_result_sha256')}
        chain['trace_sha256'] = sha256_value(material)

    def reseal_intent(self, intent: dict) -> None:
        material = {key: value for key, value in intent.items() if key != 'intent_sha256'}
        intent['intent_sha256'] = sha256_value(material)

    def add_result(self, fixture: dict, intent: dict, *, outcome: str='accepted', suffix: str='1', after_version: str | None=None, compensation_of: str | None=None, occurred_at: str='2026-08-10T19:59:30Z') -> dict:
        accepted = outcome in {'accepted', 'compensated'}
        if accepted and after_version is None:
            after_version = self.digest('after', suffix)
        material = {'intent_sha256': intent['intent_sha256'], 'idempotency_key_sha256': intent['idempotency_key_sha256'], 'system': intent['system'], 'target_identity_sha256': intent['target_identity_sha256'], 'outcome': outcome, 'provider_receipt_sha256': self.digest('provider', suffix) if accepted else None, 'before_version_sha256': intent['observed_version_sha256'], 'after_version_sha256': after_version if accepted else None, 'occurred_at': occurred_at, 'actor_sha256': intent['actor_sha256'], 'detail_sha256': self.digest('detail', suffix), 'compensation_of_result_sha256': compensation_of}
        result = {'result_sha256': sha256_value(material), **material}
        fixture['results'].append(result)
        return result

    def add_second_intent(self, fixture: dict, *, same_target: bool=False, same_idempotency: bool=False, previous_result_sha: str | None=None, compensation: str='delete_created_branch') -> tuple[dict, dict]:
        first_intent = fixture['intents'][0]
        first_target = fixture['targets'][0]
        if same_target:
            target = copy.deepcopy(first_target)
        else:
            target = {'system': 'github', 'resource_kind': 'github_branch', 'identity_sha256': self.digest('target', 'branch'), 'version_sha256': self.digest('branch', 'absent'), 'routing_sha256': self.digest('routing', 'github'), 'authorization_sha256': self.digest('authorization', 'github'), 'observed_at': '2026-08-10T19:59:50Z', 'resolvable': True, 'lease': {'token_sha256': self.digest('lease', 'github'), 'owner_sha256': self.digest('actor', 'github'), 'acquired_at': '2026-08-10T19:59:00Z', 'expires_at': '2026-08-10T20:10:00Z'}}
            fixture['targets'].append(target)
        sequence = 0 if previous_result_sha is None else 1
        chain_material = {'workflow_sha256': first_intent['chain']['workflow_sha256'], 'step': 'branch', 'sequence': sequence, 'previous_result_sha256': previous_result_sha}
        if same_target:
            system = first_intent['system']
            operation = first_intent['operation']
            resource_kind = first_intent['resource_kind']
            actor = first_intent['actor_sha256']
        else:
            system = 'github'
            operation = 'github_create_branch'
            resource_kind = 'github_branch'
            actor = target['lease']['owner_sha256']
        material = {'system': system, 'operation': operation, 'resource_kind': resource_kind, 'target_identity_sha256': target['identity_sha256'], 'observed_version_sha256': target['version_sha256'], 'observed_at': target['observed_at'], 'desired_patch_sha256': self.digest('patch', 'second'), 'relation_analysis_sha256': None, 'actor_sha256': actor, 'run_sha256': self.digest('run', 'second'), 'idempotency_key_sha256': first_intent['idempotency_key_sha256'] if same_idempotency else self.digest('idempotency', 'second'), 'policy_sha256': fixture['policy']['version_sha256'], 'routing_sha256': target['routing_sha256'], 'authorization_sha256': target['authorization_sha256'], 'lease': copy.deepcopy(target['lease']), 'compensation': compensation, 'chain': {**chain_material, 'trace_sha256': sha256_value(chain_material)}}
        intent = {'intent_sha256': sha256_value(material), **material}
        fixture['intents'].append(intent)
        return (target, intent)

    def assessment(self, report: dict, intent: dict) -> dict:
        return next((item for item in report['assessments'] if item['intent_sha256'] == intent['intent_sha256']))

    def test_ready_report_is_deterministic_digest_bound_and_round_trips(self) -> None:
        first = build_mutation_receipts_report(self.raw(), now=NOW)
        second = build_mutation_receipts_report(copy.deepcopy(first), now=NOW)
        self.assertEqual(first, second)
        self.assertEqual(first['summary']['status'], 'complete')
        self.assertEqual(first['summary']['ready'], 1)
        self.assertEqual(first['failure_ledger']['summary']['items'], 0)
        without_digest = {key: value for key, value in first.items() if key != 'report_sha256'}
        self.assertEqual(first['report_sha256'], sha256_value(without_digest))
        self.assertEqual(canonical_json(first), canonical_json(second))

    def test_stale_linear_version_is_rejected(self) -> None:
        fixture = self.raw()
        fixture['targets'][0]['version_sha256'] = self.digest('version', 'v2')
        report = build_mutation_receipts_report(fixture, now=NOW)
        item = report['assessments'][0]
        self.assertEqual(item['decision'], 'conflict')
        self.assertIn('target_version_changed', item['reason_codes'])

    def test_branch_head_movement_is_rejected(self) -> None:
        fixture = self.raw()
        _, intent = self.add_second_intent(fixture)
        target = next((item for item in fixture['targets'] if item['identity_sha256'] == intent['target_identity_sha256']))
        target['version_sha256'] = self.digest('branch', 'moved')
        report = build_mutation_receipts_report(fixture, now=NOW)
        item = self.assessment(report, intent)
        self.assertEqual(item['decision'], 'conflict')
        self.assertIn('target_version_changed', item['reason_codes'])

    def test_routing_and_authorization_drift_fail_closed(self) -> None:
        routing = self.raw()
        routing['targets'][0]['routing_sha256'] = self.digest('routing', 'v2')
        report = build_mutation_receipts_report(routing, now=NOW)
        self.assertEqual(report['assessments'][0]['decision'], 'conflict')
        self.assertIn('routing_changed', report['assessments'][0]['reason_codes'])
        authorization = self.raw()
        authorization['targets'][0]['authorization_sha256'] = self.digest('auth', 'v2')
        report = build_mutation_receipts_report(authorization, now=NOW)
        self.assertEqual(report['assessments'][0]['decision'], 'denied')
        self.assertIn('authorization_changed', report['assessments'][0]['reason_codes'])

    def test_policy_digest_must_bind_policy_and_intent(self) -> None:
        fixture = self.raw()
        fixture['policy']['max_attempts'] = 4
        with self.assertRaisesRegex(RecoveryError, 'must bind'):
            build_mutation_receipts_report(fixture, now=NOW)
        fixture = self.raw()
        fixture['intents'][0]['policy_sha256'] = self.digest('policy', 'old')
        self.reseal_intent(fixture['intents'][0])
        report = build_mutation_receipts_report(fixture, now=NOW)
        self.assertEqual(report['assessments'][0]['decision'], 'denied')
        self.assertIn('policy_changed', report['assessments'][0]['reason_codes'])

    def test_relation_mutation_requires_analysis_receipt(self) -> None:
        fixture = self.raw()
        intent = fixture['intents'][0]
        intent['operation'] = 'linear_set_relation'
        self.reseal_intent(intent)
        with self.assertRaisesRegex(RecoveryError, 'relation_analysis'):
            build_mutation_receipts_report(fixture, now=NOW)

    def test_exact_retry_replays_accepted_result_without_second_mutation(self) -> None:
        fixture = self.raw()
        intent = fixture['intents'][0]
        accepted = self.add_result(fixture, intent)
        fixture['targets'][0]['version_sha256'] = self.digest('later', 'version')
        report = build_mutation_receipts_report(fixture, now=NOW)
        item = report['assessments'][0]
        self.assertEqual(item['decision'], 'replay')
        self.assertEqual(item['reason_codes'], ['idempotent_replay'])
        self.assertEqual(item['accepted_result_sha256'], accepted['result_sha256'])
        self.assertEqual(report['summary']['ready'], 0)

    def test_idempotency_key_reuse_with_different_intent_conflicts(self) -> None:
        fixture = self.raw()
        self.add_second_intent(fixture, same_target=True, same_idempotency=True)
        report = build_mutation_receipts_report(fixture, now=NOW)
        self.assertEqual(report['summary']['conflict'], 2)
        for item in report['assessments']:
            self.assertIn('idempotency_key_reused', item['reason_codes'])

    def test_two_agents_racing_same_target_are_both_blocked_until_lease_replanned(self) -> None:
        fixture = self.raw()
        self.add_second_intent(fixture, same_target=True)
        report = build_mutation_receipts_report(fixture, now=NOW)
        self.assertEqual(report['summary']['conflict'], 2)
        for item in report['assessments']:
            self.assertIn('concurrent_intent_collision', item['reason_codes'])

    def test_expired_or_changed_lease_is_rejected(self) -> None:
        expired = self.raw()
        for lease in (expired['targets'][0]['lease'], expired['intents'][0]['lease']):
            lease['acquired_at'] = '2026-08-10T19:40:00Z'
            lease['expires_at'] = '2026-08-10T19:50:00Z'
        self.reseal_intent(expired['intents'][0])
        report = build_mutation_receipts_report(expired, now=NOW)
        self.assertEqual(report['assessments'][0]['decision'], 'expired')
        self.assertIn('lease_expired', report['assessments'][0]['reason_codes'])
        changed = self.raw()
        changed['targets'][0]['lease']['token_sha256'] = self.digest('lease', 'other')
        report = build_mutation_receipts_report(changed, now=NOW)
        self.assertEqual(report['assessments'][0]['decision'], 'conflict')
        self.assertIn('lease_changed', report['assessments'][0]['reason_codes'])

    def test_stale_and_future_snapshots_are_rejected(self) -> None:
        stale = self.raw()
        stale['targets'][0]['observed_at'] = '2026-08-10T19:00:00Z'
        report = build_mutation_receipts_report(stale, now=NOW)
        self.assertIn('snapshot_stale', report['assessments'][0]['reason_codes'])
        future = self.raw()
        future['targets'][0]['observed_at'] = '2026-08-10T20:05:01Z'
        with self.assertRaisesRegex(RecoveryError, 'clock skew'):
            build_mutation_receipts_report(future, now=NOW)

    def test_downstream_chain_blocks_when_previous_subject_moves(self) -> None:
        fixture = self.raw()
        first_intent = fixture['intents'][0]
        accepted = self.add_result(fixture, first_intent, after_version=self.digest('linear', 'after'))
        fixture['targets'][0]['version_sha256'] = accepted['after_version_sha256']
        _, second = self.add_second_intent(fixture, previous_result_sha=accepted['result_sha256'])
        ready = build_mutation_receipts_report(fixture, now=NOW)
        self.assertEqual(self.assessment(ready, second)['decision'], 'ready')
        self.assertTrue(self.assessment(ready, second)['chain_previous_current'])
        fixture['targets'][0]['version_sha256'] = self.digest('linear', 'moved-again')
        blocked = build_mutation_receipts_report(fixture, now=NOW)
        item = self.assessment(blocked, second)
        self.assertEqual(item['decision'], 'blocked_by_chain')
        self.assertIn('previous_subject_changed', item['reason_codes'])

    def test_missing_or_wrong_previous_result_blocks_chain(self) -> None:
        missing = self.raw()
        _, intent = self.add_second_intent(missing, previous_result_sha=self.digest('result', 'missing'))
        report = build_mutation_receipts_report(missing, now=NOW)
        self.assertEqual(self.assessment(report, intent)['decision'], 'blocked_by_chain')
        self.assertIn('previous_result_missing', self.assessment(report, intent)['reason_codes'])
        wrong = self.raw()
        first = wrong['intents'][0]
        failed = self.add_result(wrong, first, outcome='provider_error')
        _, intent = self.add_second_intent(wrong, previous_result_sha=failed['result_sha256'])
        report = build_mutation_receipts_report(wrong, now=NOW)
        self.assertIn('previous_result_not_accepted', self.assessment(report, intent)['reason_codes'])

    def test_provider_failure_emits_bounded_compensation_signal(self) -> None:
        fixture = self.raw()
        first = fixture['intents'][0]
        accepted = self.add_result(fixture, first, after_version=self.digest('linear', 'after'))
        fixture['targets'][0]['version_sha256'] = accepted['after_version_sha256']
        _, second = self.add_second_intent(fixture, previous_result_sha=accepted['result_sha256'])
        self.add_result(fixture, second, outcome='provider_error', suffix='provider-failure')
        report = build_mutation_receipts_report(fixture, now=NOW)
        item = self.assessment(report, second)
        self.assertTrue(item['compensation_required'])
        self.assertIn('provider_failure', item['reason_codes'])
        self.assertIn('compensation_required', item['reason_codes'])
        self.assertEqual(report['summary']['compensation_required'], 1)
        self.assertEqual(report['summary']['retryable_failures'], 1)
        self.assertEqual(report['summary']['status'], 'partial')
        self.assertGreater(report['failure_ledger']['summary']['items'], 0)

    def test_attempt_budget_is_bounded(self) -> None:
        fixture = self.raw()
        intent = fixture['intents'][0]
        for index in range(fixture['policy']['max_attempts']):
            self.add_result(fixture, intent, outcome='provider_error', suffix=str(index))
        report = build_mutation_receipts_report(fixture, now=NOW)
        item = report['assessments'][0]
        self.assertEqual(item['decision'], 'conflict')
        self.assertIn('attempt_budget_exhausted', item['reason_codes'])

    def test_result_time_must_be_current_and_within_the_authoritative_lease(self) -> None:
        future = self.raw()
        self.add_result(future, future['intents'][0], occurred_at='2026-08-10T20:05:01Z')
        with self.assertRaisesRegex(RecoveryError, 'clock skew'):
            build_mutation_receipts_report(future, now=NOW)
        after_lease = self.raw()
        after_lease['generated_at'] = '2026-08-10T20:20:00Z'
        self.add_result(after_lease, after_lease['intents'][0], occurred_at='2026-08-10T20:15:01Z')
        with self.assertRaisesRegex(RecoveryError, 'lease expired'):
            build_mutation_receipts_report(after_lease, now='2026-08-10T20:20:00Z')
        before_observation = self.raw()
        self.add_result(before_observation, before_observation['intents'][0], occurred_at='2026-08-10T19:54:00Z')
        with self.assertRaisesRegex(RecoveryError, 'intent observation'):
            build_mutation_receipts_report(before_observation, now=NOW)

    def test_result_receipts_are_content_addressed_and_tamper_evident(self) -> None:
        fixture = self.raw()
        result = self.add_result(fixture, fixture['intents'][0])
        result['detail_sha256'] = self.digest('tampered')
        with self.assertRaisesRegex(RecoveryError, 'result_sha256'):
            build_mutation_receipts_report(fixture, now=NOW)
        fixture = self.raw()
        result = self.add_result(fixture, fixture['intents'][0])
        result['after_version_sha256'] = None
        material = {key: value for key, value in result.items() if key != 'result_sha256'}
        result['result_sha256'] = sha256_value(material)
        with self.assertRaisesRegex(RecoveryError, 'after-version'):
            build_mutation_receipts_report(fixture, now=NOW)

    def test_compensation_must_reference_an_accepted_result(self) -> None:
        fixture = self.raw()
        original = self.add_result(fixture, fixture['intents'][0], outcome='provider_error')
        _, compensation_intent = self.add_second_intent(fixture)
        self.add_result(fixture, compensation_intent, outcome='compensated', compensation_of=original['result_sha256'])
        with self.assertRaisesRegex(RecoveryError, 'accepted result'):
            build_mutation_receipts_report(fixture, now=NOW)

    def test_unknown_fields_malformed_digests_and_duplicate_ids_are_rejected(self) -> None:
        unknown = self.raw()
        unknown['intents'][0]['raw_provider_error'] = 'not allowed'
        with self.assertRaisesRegex(RecoveryError, 'unsupported keys'):
            build_mutation_receipts_report(unknown, now=NOW)
        malformed = self.raw()
        malformed['intents'][0]['desired_patch_sha256'] = 'ghp_' + 'a' * 60
        with self.assertRaisesRegex(RecoveryError, 'lowercase SHA-256'):
            build_mutation_receipts_report(malformed, now=NOW)
        duplicate = self.raw()
        duplicate['targets'].append(copy.deepcopy(duplicate['targets'][0]))
        with self.assertRaisesRegex(RecoveryError, 'duplicate identities'):
            build_mutation_receipts_report(duplicate, now=NOW)

    def test_tampered_assessment_summary_ledger_and_report_digest_are_rejected(self) -> None:
        report = build_mutation_receipts_report(self.raw(), now=NOW)
        assessment = copy.deepcopy(report)
        assessment['assessments'][0]['decision'] = 'conflict'
        with self.assertRaisesRegex(RecoveryError, 'assessments'):
            build_mutation_receipts_report(assessment, now=NOW)
        summary = copy.deepcopy(report)
        summary['summary']['ready'] = 0
        with self.assertRaisesRegex(RecoveryError, 'summary'):
            build_mutation_receipts_report(summary, now=NOW)
        ledger = copy.deepcopy(report)
        ledger['failure_ledger']['ledger_sha256'] = '0' * 64
        with self.assertRaisesRegex(RecoveryError, 'failure_ledger'):
            build_mutation_receipts_report(ledger, now=NOW)
        digest = copy.deepcopy(report)
        digest['report_sha256'] = '0' * 64
        with self.assertRaisesRegex(RecoveryError, 'report_sha256'):
            build_mutation_receipts_report(digest, now=NOW)

    def test_input_is_not_mutated_and_example_round_trips_from_disk(self) -> None:
        fixture = self.raw()
        original = copy.deepcopy(fixture)
        report = build_mutation_receipts_report(fixture, now=NOW)
        self.assertEqual(fixture, original)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            path.write_text(json.dumps(report), encoding='utf-8')
            restored = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(build_mutation_receipts_report(restored, now=NOW), report)
if __name__ == '__main__':
    unittest.main()
