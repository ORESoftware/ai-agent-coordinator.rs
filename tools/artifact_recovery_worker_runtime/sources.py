from __future__ import annotations

from .common import *
from .config import *
from .transport import *

@dataclass(frozen=True)
class SourceCollection:
    spec: SourceSpec
    items: tuple[dict[str, Any], ...]
    receipt: dict[str, Any]
    high_water_mark: str | None

class SourceCollector:

    def __init__(self, *, http: JsonHttpClient, config: WorkerConfig, environment: Mapping[str, str] | None=None) -> None:
        self.http = http
        self.config = config
        self.environment = environment if environment is not None else os.environ

    def collect(self, spec: SourceSpec, *, window_start: datetime, window_end: datetime, prior_high_water_mark: str | None) -> SourceCollection:
        token = self.environment.get(spec.token_env, '')
        if not token:
            receipt = self._failure_receipt(spec, window_start=window_start, window_end=window_end, state='unauthorized', error_class='authorization', retryable=False)
            return SourceCollection(spec, (), receipt, prior_high_water_mark)
        pages_read = 0
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        captured_at = window_end
        watermark_at = window_start
        last_page_complete = False
        terminal_cursor: str | None = None
        try:
            while True:
                query = {'window_start': format_instant(window_start), 'window_end': format_instant(window_end), 'limit': str(self.config.source_page_size), 'include_unresolved': 'true'}
                if cursor is not None:
                    query['cursor'] = cursor
                if prior_high_water_mark:
                    query['high_water_mark'] = prior_high_water_mark
                url = f'{spec.endpoint}?{urlencode(query)}'
                _, page = self.http.request('GET', url, token=token, user_agent='ai-agent-coordinator-artifact-source/1')
                if page is None:
                    raise WorkerError('source adapter returned an empty response', error_class='pagination')
                normalized = self._validate_page(page, spec)
                pages_read += 1
                if pages_read > self.config.max_pages_per_source:
                    raise WorkerError('source adapter exceeded the page limit', retryable=False, error_class='pagination')
                captured_at = max(captured_at, normalized['captured_at'])
                watermark_at = max(watermark_at, normalized['watermark_at'])
                items.extend(normalized['items'])
                if len(items) > MAX_ITEMS:
                    raise WorkerError('source adapter exceeded the item limit', retryable=False, error_class='pagination')
                next_cursor = normalized['next_cursor']
                page_complete = normalized['complete']
                if page_complete:
                    if next_cursor is not None:
                        raise WorkerError('complete source page must not contain next_cursor', retryable=False, error_class='pagination')
                    last_page_complete = True
                    terminal_cursor = cursor
                    break
                if next_cursor is None:
                    raise WorkerError('partial source page requires next_cursor', retryable=False, error_class='pagination')
                if next_cursor in seen_cursors or next_cursor == cursor:
                    raise WorkerError('source adapter repeated a pagination cursor', retryable=False, error_class='pagination')
                seen_cursors.add(next_cursor)
                cursor = next_cursor
        except WorkerError as exc:
            receipt = self._failure_receipt(spec, window_start=window_start, window_end=window_end, state='partial' if pages_read else 'unavailable', error_class=exc.error_class, retryable=exc.retryable, pages_read=pages_read, items_read=len(items), watermark_at=watermark_at, terminal_cursor=cursor)
            return SourceCollection(spec, tuple(items), receipt, prior_high_water_mark)
        receipt = self._success_receipt(spec, window_start=window_start, window_end=window_end, captured_at=captured_at, watermark_at=watermark_at, pages_read=pages_read, items_read=len(items), last_page_complete=last_page_complete, terminal_cursor=terminal_cursor)
        return SourceCollection(spec, tuple(items), receipt, format_instant(watermark_at) if receipt['state'] == 'complete' else prior_high_water_mark)

    def _validate_page(self, raw: Mapping[str, Any], spec: SourceSpec) -> dict[str, Any]:
        page = expect_object(raw, 'source page')
        strict_keys(page, 'source page', {'schema_version', 'source', 'captured_at', 'watermark_at', 'items', 'complete', 'next_cursor'})
        if page.get('schema_version') != SOURCE_PAGE_SCHEMA:
            raise WorkerError(f'source page schema_version must be {SOURCE_PAGE_SCHEMA}')
        if page.get('source') != spec.source:
            raise WorkerError('source page identity does not match the configured adapter')
        captured_at = parse_instant(page.get('captured_at'), 'source page.captured_at')
        watermark_at = parse_instant(page.get('watermark_at'), 'source page.watermark_at')
        if watermark_at > captured_at + timedelta(minutes=5):
            raise WorkerError('source watermark is after captured_at')
        complete = page.get('complete')
        if not isinstance(complete, bool):
            raise WorkerError('source page.complete must be boolean')
        next_cursor = page.get('next_cursor')
        if next_cursor is not None:
            next_cursor = expect_string(next_cursor, 'source page.next_cursor', 512)
            if not CURSOR_RE.fullmatch(next_cursor):
                raise WorkerError('source page.next_cursor has an invalid shape')
        raw_items = expect_list(page.get('items'), 'source page.items', MAX_ITEMS)
        items: list[dict[str, Any]] = []
        for index, raw_item in enumerate(raw_items):
            item = expect_object(raw_item, f'source page.items[{index}]')
            origin = expect_object(item.get('origin'), f'source page.items[{index}].origin')
            origin_source = origin.get('source')
            if origin_source not in ORIGIN_SOURCES:
                raise WorkerError(f'source page.items[{index}].origin.source is unsupported')
            items.append(item)
        return {'captured_at': captured_at, 'watermark_at': watermark_at, 'items': items, 'complete': complete, 'next_cursor': next_cursor}

    def _success_receipt(self, spec: SourceSpec, *, window_start: datetime, window_end: datetime, captured_at: datetime, watermark_at: datetime, pages_read: int, items_read: int, last_page_complete: bool, terminal_cursor: str | None) -> dict[str, Any]:
        age = max(0, int((window_end - watermark_at).total_seconds()))
        return {'source': spec.source, 'source_identity_sha256': redacted_digest(spec.identity), 'capability_sha256': sha256_value(list(spec.capabilities)), 'window': {'start': format_instant(window_start), 'end': format_instant(window_end)}, 'captured_at': format_instant(captured_at), 'freshness': {'watermark_at': format_instant(watermark_at), 'max_age_seconds': spec.max_age_seconds, 'age_seconds': age}, 'pagination': {'started': True, 'complete': True, 'last_page_complete': last_page_complete, 'pages_read': pages_read, 'items_read': items_read, 'terminal_cursor_sha256': redacted_digest(terminal_cursor) if terminal_cursor is not None else None}, 'reported_state': 'complete', 'state': 'complete' if age <= spec.max_age_seconds else 'stale', 'error_class': None if age <= spec.max_age_seconds else 'stale_watermark', 'retryable': age > spec.max_age_seconds}

    def _failure_receipt(self, spec: SourceSpec, *, window_start: datetime, window_end: datetime, state: str, error_class: str, retryable: bool, pages_read: int=0, items_read: int=0, watermark_at: datetime | None=None, terminal_cursor: str | None=None) -> dict[str, Any]:
        watermark = watermark_at or window_start
        age = max(0, int((window_end - watermark).total_seconds()))
        return {'source': spec.source, 'source_identity_sha256': redacted_digest(spec.identity), 'capability_sha256': sha256_value(list(spec.capabilities)), 'window': {'start': format_instant(window_start), 'end': format_instant(window_end)}, 'captured_at': format_instant(window_end), 'freshness': {'watermark_at': format_instant(watermark), 'max_age_seconds': spec.max_age_seconds, 'age_seconds': age}, 'pagination': {'started': pages_read > 0, 'complete': False, 'last_page_complete': False, 'pages_read': pages_read, 'items_read': items_read, 'terminal_cursor_sha256': redacted_digest(terminal_cursor) if terminal_cursor is not None else None}, 'reported_state': state, 'state': state, 'error_class': error_class, 'retryable': retryable}

def validate_job(job: Mapping[str, Any]) -> tuple[str, dict[str, Any], str]:
    if job.get('task_type') != TASK_TYPE:
        raise WorkerError('worker accepts only artifact_recovery jobs')
    job_id = expect_string(job.get('id'), 'job.id', 200)
    payload = expect_object(job.get('payload'), 'job.payload')
    strict_keys(payload, 'job.payload', {'schema_version', 'run', 'tracking', 'source_contract', 'ledger_contract', 'detection_contract', 'delivery_contract', 'allowed_actions', 'forbidden_actions'})
    if payload.get('schema_version') != JOB_SCHEMA:
        raise WorkerError(f'job.payload.schema_version must be {JOB_SCHEMA}')
    run = expect_object(payload.get('run'), 'job.payload.run')
    run_key = expect_string(run.get('run_key'), 'job.payload.run.run_key', 200)
    if not RUN_KEY_RE.fullmatch(run_key):
        raise WorkerError('job.payload.run.run_key has an invalid shape')
    forbidden = expect_list(payload.get('forbidden_actions'), 'job.payload.forbidden_actions', 64)
    mandatory = {'reuse_chat_pasted_credentials', 'force_push', 'direct_default_branch_write', 'bypass_protection', 'auto_merge', 'claim_delivery_without_remote_evidence'}
    if not mandatory.issubset(set(forbidden)):
        raise WorkerError('job payload is missing mandatory forbidden actions')
    ensure_public_safe(payload, 'job.payload')
    return (job_id, payload, run_key)

def _deduplicate_items(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, tuple[str, dict[str, Any]]] = {}
    for raw in items:
        item = dict(raw)
        origin = expect_object(item.get('origin'), 'item.origin')
        target = expect_object(item.get('target'), 'item.target')
        key = '|'.join([expect_string(origin.get('source'), 'item.origin.source', 32), expect_string(origin.get('id'), 'item.origin.id', 200), expect_string(target.get('owner'), 'item.target.owner', 39), expect_string(target.get('repository'), 'item.target.repository', 100)])
        digest = sha256_value(item)
        prior = by_key.get(key)
        if prior is not None and prior[0] != digest:
            raise WorkerError('source adapters returned conflicting duplicate items')
        by_key[key] = (digest, item)
    result = [entry[1] for entry in by_key.values()]
    result.sort(key=lambda item: (item['origin']['source'], item['origin']['id'], item['target']['owner'], item['target']['repository']))
    return result

def build_coverage(manifest: SourceManifest, collections: Sequence[SourceCollection], *, generated_at: datetime) -> dict[str, Any]:
    receipts = [collection.receipt for collection in collections]
    states = {receipt['source']: receipt['state'] for receipt in receipts}
    blocked = sorted((source for source in manifest.required_sources if states.get(source) in {'unauthorized', 'not_configured', 'excluded'}))
    partial = sorted((source for source in manifest.required_sources if states.get(source) in {'partial', 'unavailable', 'stale'}))
    status = 'blocked' if blocked else 'partial' if partial else 'complete'
    report = {'schema_version': COVERAGE_SCHEMA, 'generated_at': format_instant(generated_at), 'policy': {'required_sources': list(manifest.required_sources), 'optional_sources': list(manifest.optional_sources), 'max_clock_skew_seconds': 300}, 'receipts': sorted(receipts, key=lambda value: value['source']), 'summary': {'status': status, 'complete': status == 'complete', 'required_sources': len(manifest.required_sources), 'optional_sources': len(manifest.optional_sources), 'complete_sources': sorted((source for source, state in states.items() if state == 'complete')), 'partial_sources': partial, 'blocked_sources': blocked, 'source_states': dict(sorted(states.items()))}}
    report['report_sha256'] = sha256_value(report)
    ensure_public_safe(report, 'coverage')
    return report

def load_cursor_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {'schema_version': 'artifact_recovery_cursors.v1', 'sources': {}}
    value = load_json_file(path, 'cursor state')
    root = expect_object(value, 'cursor state')
    if root.get('schema_version') != 'artifact_recovery_cursors.v1':
        raise WorkerError('cursor state has an unsupported schema')
    sources = expect_object(root.get('sources'), 'cursor state.sources')
    return {'schema_version': 'artifact_recovery_cursors.v1', 'sources': sources}
