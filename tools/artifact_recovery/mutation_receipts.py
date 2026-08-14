"""Fail-closed optimistic-concurrency and idempotency receipts for coordinator writes."""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Mapping
from .common import RecoveryError, expect_list, sha256_value, validate_public_safety
from .mutation_receipts_contract import FAILURE_LEDGER_SCHEMA, MAX_RECORDS, SCHEMA_VERSION, _normalize_intent, _normalize_policy, _normalize_result, _normalize_target, _parse_datetime, _strict_object

def _age_seconds(when: str, reference: datetime, *, max_clock_skew_seconds: int, field: str) -> int:
    _, parsed = _parse_datetime(when, field)
    if parsed > reference + timedelta(seconds=max_clock_skew_seconds):
        raise RecoveryError(f'{field} is beyond the allowed clock skew')
    return max(0, int((reference - parsed).total_seconds()))

def _result_is_accepted(result: Mapping[str, Any]) -> bool:
    return result['outcome'] in {'accepted', 'compensated'}

def _assess_intent(intent: Mapping[str, Any], *, generated_at: datetime, policy: Mapping[str, Any], targets: Mapping[str, Mapping[str, Any]], results_by_intent: Mapping[str, list[Mapping[str, Any]]], results_by_sha: Mapping[str, Mapping[str, Any]], intents_by_sha: Mapping[str, Mapping[str, Any]], idempotency_collisions: set[str]) -> dict[str, Any]:
    reasons: set[str] = set()
    decision = 'ready'
    current_version: str | None = None
    accepted_result_sha: str | None = None
    chain_previous_current: bool | None = None
    compensation_required = False
    intent_results = results_by_intent.get(intent['intent_sha256'], [])
    accepted = [result for result in intent_results if _result_is_accepted(result)]
    if len(accepted) > 1:
        raise RecoveryError('an intent has more than one accepted result; idempotency was violated')
    if accepted:
        accepted_result_sha = accepted[0]['result_sha256']
        return {'intent_sha256': intent['intent_sha256'], 'target_identity_sha256': intent['target_identity_sha256'], 'idempotency_key_sha256': intent['idempotency_key_sha256'], 'current_version_sha256': targets.get(intent['target_identity_sha256'], {}).get('version_sha256'), 'decision': 'replay', 'reason_codes': ['idempotent_replay'], 'attempt_count': len(intent_results), 'accepted_result_sha256': accepted_result_sha, 'chain_previous_current': None, 'compensation_required': False}
    if intent['idempotency_key_sha256'] in idempotency_collisions:
        reasons.add('idempotency_key_reused')
        decision = 'conflict'
    if len(intent_results) >= policy['max_attempts']:
        reasons.add('attempt_budget_exhausted')
        decision = 'conflict'
    target = targets.get(intent['target_identity_sha256'])
    if target is None or not target['resolvable']:
        reasons.add('target_unresolved')
        decision = 'unverifiable'
    else:
        current_version = target['version_sha256']
        if target['system'] != intent['system']:
            reasons.add('target_system_mismatch')
            decision = 'conflict'
        if target['resource_kind'] != intent['resource_kind']:
            reasons.add('target_resource_mismatch')
            decision = 'conflict'
        if target['version_sha256'] != intent['observed_version_sha256']:
            reasons.add('target_version_changed')
            decision = 'conflict'
        if target['routing_sha256'] != intent['routing_sha256']:
            reasons.add('routing_changed')
            decision = 'conflict'
        if target['authorization_sha256'] != intent['authorization_sha256']:
            reasons.add('authorization_changed')
            decision = 'denied'
        if intent['policy_sha256'] != policy['version_sha256']:
            reasons.add('policy_changed')
            decision = 'denied'
        snapshot_age = _age_seconds(target['observed_at'], generated_at, max_clock_skew_seconds=policy['max_clock_skew_seconds'], field='target.observed_at')
        intent_age = _age_seconds(intent['observed_at'], generated_at, max_clock_skew_seconds=policy['max_clock_skew_seconds'], field='intent.observed_at')
        if snapshot_age > policy['max_snapshot_age_seconds'] or intent_age > policy['max_snapshot_age_seconds']:
            reasons.add('snapshot_stale')
            decision = 'conflict'
        intent_lease = intent['lease']
        target_lease = target['lease']
        if intent_lease is None or target_lease is None:
            reasons.add('lease_changed')
            decision = 'conflict'
        else:
            if intent_lease['token_sha256'] != target_lease['token_sha256'] or intent_lease['owner_sha256'] != target_lease['owner_sha256'] or intent_lease['expires_at'] != target_lease['expires_at']:
                reasons.add('lease_changed')
                decision = 'conflict'
            _, acquired_dt = _parse_datetime(intent_lease['acquired_at'], 'intent.lease.acquired_at')
            if acquired_dt > generated_at + timedelta(seconds=policy['max_clock_skew_seconds']):
                raise RecoveryError('intent.lease.acquired_at is beyond the allowed clock skew')
            _, expires_dt = _parse_datetime(intent_lease['expires_at'], 'intent.lease.expires_at')
            if generated_at > expires_dt:
                reasons.add('lease_expired')
                decision = 'expired'
    chain = intent['chain']
    previous_sha = chain['previous_result_sha256']
    if previous_sha is not None:
        previous = results_by_sha.get(previous_sha)
        if previous is None:
            reasons.add('previous_result_missing')
            decision = 'blocked_by_chain'
            chain_previous_current = False
        elif not _result_is_accepted(previous):
            reasons.add('previous_result_not_accepted')
            decision = 'blocked_by_chain'
            chain_previous_current = False
        else:
            previous_intent = intents_by_sha[previous['intent_sha256']]
            if previous_intent['chain']['workflow_sha256'] != chain['workflow_sha256'] or previous_intent['chain']['sequence'] + 1 != chain['sequence']:
                reasons.add('previous_chain_mismatch')
                decision = 'blocked_by_chain'
                chain_previous_current = False
            else:
                previous_target = targets.get(previous['target_identity_sha256'])
                chain_previous_current = bool(previous_target and previous_target['resolvable'] and (previous_target['version_sha256'] == previous['after_version_sha256']))
                if not chain_previous_current:
                    reasons.add('previous_subject_changed')
                    decision = 'blocked_by_chain'
    provider_failures = [result for result in intent_results if result['outcome'] == 'provider_error']
    if provider_failures:
        reasons.add('provider_failure')
        if chain['previous_result_sha256'] is not None:
            compensation_required = True
            reasons.add('compensation_required')
            if intent['compensation'] == 'none':
                reasons.add('compensation_unavailable')
                decision = 'blocked_by_chain'
    return {'intent_sha256': intent['intent_sha256'], 'target_identity_sha256': intent['target_identity_sha256'], 'idempotency_key_sha256': intent['idempotency_key_sha256'], 'current_version_sha256': current_version, 'decision': decision, 'reason_codes': sorted(reasons), 'attempt_count': len(intent_results), 'accepted_result_sha256': accepted_result_sha, 'chain_previous_current': chain_previous_current, 'compensation_required': compensation_required}

def _apply_collision_control(assessments: list[dict[str, Any]], intents_by_sha: Mapping[str, Mapping[str, Any]]) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for assessment in assessments:
        if assessment['decision'] != 'ready':
            continue
        intent = intents_by_sha[assessment['intent_sha256']]
        key = (intent['system'], intent['target_identity_sha256'], intent['observed_version_sha256'])
        groups[key].append(assessment)
    for group in groups.values():
        if len(group) < 2:
            continue
        for assessment in group:
            assessment['decision'] = 'conflict'
            assessment['reason_codes'] = sorted(set(assessment['reason_codes']) | {'concurrent_intent_collision'})

def _build_failure_ledger(assessments: list[Mapping[str, Any]]) -> dict[str, Any]:
    items = []
    for assessment in assessments:
        if assessment['decision'] in {'ready', 'replay'} and (not assessment['reason_codes']) and (not assessment['compensation_required']):
            continue
        items.append({'intent_sha256': assessment['intent_sha256'], 'target_identity_sha256': assessment['target_identity_sha256'], 'decision': assessment['decision'], 'reason_codes': assessment['reason_codes'], 'compensation_required': assessment['compensation_required']})
    items.sort(key=lambda item: item['intent_sha256'])
    without_digest = {'schema_version': FAILURE_LEDGER_SCHEMA, 'summary': {'items': len(items)}, 'items': items}
    return {**without_digest, 'ledger_sha256': sha256_value(without_digest)}

def build_mutation_receipts_report(value: Mapping[str, Any], *, now: str | None=None) -> dict[str, Any]:
    """Normalize and assess mutation intents and result receipts."""
    root = _strict_object(value, '$', {'schema_version', 'generated_at', 'policy', 'targets', 'intents', 'results', 'assessments', 'summary', 'failure_ledger', 'report_sha256'})
    if root.get('schema_version') != SCHEMA_VERSION:
        raise RecoveryError(f'schema_version must be {SCHEMA_VERSION}')
    policy = _normalize_policy(root.get('policy'))
    generated_at, generated_dt = _parse_datetime(root.get('generated_at'), 'generated_at')
    if now is not None:
        _, now_dt = _parse_datetime(now, '--now')
        if generated_dt > now_dt + timedelta(seconds=policy['max_clock_skew_seconds']):
            raise RecoveryError('generated_at is beyond the allowed clock skew')
    raw_targets = expect_list(root.get('targets'), 'targets', MAX_RECORDS)
    targets = [_normalize_target(item, index, policy=policy) for index, item in enumerate(raw_targets)]
    identities = [target['identity_sha256'] for target in targets]
    if len(identities) != len(set(identities)):
        raise RecoveryError('targets contain duplicate identities')
    targets.sort(key=lambda item: item['identity_sha256'])
    targets_by_identity = {target['identity_sha256']: target for target in targets}
    raw_intents = expect_list(root.get('intents'), 'intents', MAX_RECORDS)
    intents = [_normalize_intent(item, index, policy=policy) for index, item in enumerate(raw_intents)]
    intent_shas = [intent['intent_sha256'] for intent in intents]
    if len(intent_shas) != len(set(intent_shas)):
        raise RecoveryError('intents contain duplicate identities')
    intents.sort(key=lambda item: item['intent_sha256'])
    intents_by_sha = {intent['intent_sha256']: intent for intent in intents}
    key_to_intents: dict[str, set[str]] = defaultdict(set)
    for intent in intents:
        key_to_intents[intent['idempotency_key_sha256']].add(intent['intent_sha256'])
    idempotency_collisions = {key for key, values in key_to_intents.items() if len(values) > 1}
    raw_results = expect_list(root.get('results'), 'results', MAX_RECORDS)
    results = [_normalize_result(item, index, intents_by_sha=intents_by_sha) for index, item in enumerate(raw_results)]
    skew = timedelta(seconds=policy['max_clock_skew_seconds'])
    for result in results:
        _age_seconds(result['occurred_at'], generated_dt, max_clock_skew_seconds=policy['max_clock_skew_seconds'], field='results[].occurred_at')
        _, occurred_dt = _parse_datetime(result['occurred_at'], 'results[].occurred_at')
        intent = intents_by_sha[result['intent_sha256']]
        _, observed_dt = _parse_datetime(intent['observed_at'], 'intents[].observed_at')
        if occurred_dt < observed_dt - skew:
            raise RecoveryError('result occurred before the intent observation')
        lease = intent['lease']
        if lease is not None:
            _, acquired_dt = _parse_datetime(lease['acquired_at'], 'intents[].lease.acquired_at')
            _, expires_dt = _parse_datetime(lease['expires_at'], 'intents[].lease.expires_at')
            if occurred_dt < acquired_dt - skew:
                raise RecoveryError('result occurred before the intent lease was acquired')
            if occurred_dt > expires_dt + skew:
                raise RecoveryError('result occurred after the intent lease expired')
    result_shas = [result['result_sha256'] for result in results]
    if len(result_shas) != len(set(result_shas)):
        raise RecoveryError('results contain duplicate identities')
    results.sort(key=lambda item: item['result_sha256'])
    results_by_sha = {result['result_sha256']: result for result in results}
    for result in results:
        compensation_of = result['compensation_of_result_sha256']
        if compensation_of is not None:
            original = results_by_sha.get(compensation_of)
            if original is None or original['outcome'] != 'accepted':
                raise RecoveryError('compensation must reference an accepted result in this report')
    results_by_intent: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for result in results:
        results_by_intent[result['intent_sha256']].append(result)
    assessments = [_assess_intent(intent, generated_at=generated_dt, policy=policy, targets=targets_by_identity, results_by_intent=results_by_intent, results_by_sha=results_by_sha, intents_by_sha=intents_by_sha, idempotency_collisions=idempotency_collisions) for intent in intents]
    _apply_collision_control(assessments, intents_by_sha)
    assessments.sort(key=lambda item: item['intent_sha256'])
    decisions = Counter((item['decision'] for item in assessments))
    compensation_required = sum((1 for item in assessments if item['compensation_required']))
    retryable_failures = sum((1 for item in assessments if 'provider_failure' in item['reason_codes']))
    blocked = sum((decisions[name] for name in ('conflict', 'expired', 'denied', 'unverifiable', 'blocked_by_chain')))
    if blocked:
        status = 'blocked'
    elif compensation_required or retryable_failures:
        status = 'partial'
    else:
        status = 'complete'
    summary = {'status': status, 'complete': status == 'complete', 'targets': len(targets), 'intents': len(intents), 'results': len(results), 'ready': decisions['ready'], 'replay': decisions['replay'], 'conflict': decisions['conflict'], 'expired': decisions['expired'], 'denied': decisions['denied'], 'unverifiable': decisions['unverifiable'], 'blocked_by_chain': decisions['blocked_by_chain'], 'compensation_required': compensation_required, 'retryable_failures': retryable_failures}
    failure_ledger = _build_failure_ledger(assessments)
    without_digest = {'schema_version': SCHEMA_VERSION, 'generated_at': generated_at, 'policy': policy, 'targets': targets, 'intents': intents, 'results': results, 'assessments': assessments, 'summary': summary, 'failure_ledger': failure_ledger}
    report = {**without_digest, 'report_sha256': sha256_value(without_digest)}
    for field, expected in (('assessments', assessments), ('summary', summary), ('failure_ledger', failure_ledger), ('report_sha256', report['report_sha256'])):
        if field in root and root[field] != expected:
            raise RecoveryError(f'{field} does not match the derived report')
    validate_public_safety(report)
    return report

def build_example_mutation_receipts(*, now: str) -> dict[str, Any]:
    generated_at, generated_dt = _parse_datetime(now, '--now')
    policy_material = {'max_clock_skew_seconds': 300, 'max_snapshot_age_seconds': 300, 'max_lease_seconds': 1800, 'max_attempts': 3}
    policy_sha = sha256_value(policy_material)
    target_identity = sha256_value({'fixture': 'linear-issue'})
    version = sha256_value({'fixture': 'version', 'v': 1})
    routing = sha256_value({'fixture': 'routing', 'v': 1})
    authorization = sha256_value({'fixture': 'authorization', 'v': 1})
    actor = sha256_value({'fixture': 'actor'})
    lease = {'token_sha256': sha256_value({'fixture': 'lease-token'}), 'owner_sha256': actor, 'acquired_at': (generated_dt - timedelta(seconds=30)).isoformat().replace('+00:00', 'Z'), 'expires_at': (generated_dt + timedelta(minutes=10)).isoformat().replace('+00:00', 'Z')}
    observed_at = (generated_dt - timedelta(seconds=10)).isoformat().replace('+00:00', 'Z')
    chain_material = {'workflow_sha256': sha256_value({'fixture': 'workflow'}), 'step': 'linear_issue', 'sequence': 0, 'previous_result_sha256': None}
    chain = {**chain_material, 'trace_sha256': sha256_value(chain_material)}
    intent_material = {'system': 'linear', 'operation': 'linear_update_issue', 'resource_kind': 'linear_issue', 'target_identity_sha256': target_identity, 'observed_version_sha256': version, 'observed_at': observed_at, 'desired_patch_sha256': sha256_value({'fixture': 'patch'}), 'relation_analysis_sha256': None, 'actor_sha256': actor, 'run_sha256': sha256_value({'fixture': 'run'}), 'idempotency_key_sha256': sha256_value({'fixture': 'idempotency'}), 'policy_sha256': policy_sha, 'routing_sha256': routing, 'authorization_sha256': authorization, 'lease': lease, 'compensation': 'restore_previous_state', 'chain': chain}
    raw = {'schema_version': SCHEMA_VERSION, 'generated_at': generated_at, 'policy': {'version_sha256': policy_sha, **policy_material}, 'targets': [{'system': 'linear', 'resource_kind': 'linear_issue', 'identity_sha256': target_identity, 'version_sha256': version, 'routing_sha256': routing, 'authorization_sha256': authorization, 'observed_at': observed_at, 'resolvable': True, 'lease': lease}], 'intents': [{'intent_sha256': sha256_value(intent_material), **intent_material}], 'results': []}
    return build_mutation_receipts_report(raw, now=generated_at)
