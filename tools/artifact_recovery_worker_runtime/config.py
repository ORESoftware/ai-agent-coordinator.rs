from __future__ import annotations

from .common import *

@dataclass(frozen=True)
class WorkerConfig:
    coordinator_url: str
    coordinator_token: str
    source_manifest: Path
    state_dir: Path
    worker_id: str
    lease_seconds: int
    request_timeout_seconds: int
    max_response_bytes: int
    max_pages_per_source: int
    source_page_size: int
    retry_delay_seconds: int
    poll_seconds: int
    once: bool

    @classmethod
    def from_env(cls, *, once: bool=False) -> 'WorkerConfig':
        coordinator_url = validate_base_url(os.environ.get('AI_AGENT_COORDINATOR_URL', ''), 'AI_AGENT_COORDINATOR_URL')
        coordinator_token = os.environ.get('AI_AGENT_COORDINATOR_API_TOKEN', '')
        if not coordinator_token:
            raise WorkerError('AI_AGENT_COORDINATOR_API_TOKEN is required')
        worker_id = os.environ.get('ARTIFACT_RECOVERY_WORKER_ID', f'artifact-recovery-{socket.gethostname()}').strip()
        if not WORKER_ID_RE.fullmatch(worker_id):
            raise WorkerError('ARTIFACT_RECOVERY_WORKER_ID has an invalid shape')
        source_manifest = Path(os.environ.get('ARTIFACT_RECOVERY_SOURCE_MANIFEST', '/etc/artifact-recovery/sources.json'))
        state_dir = Path(os.environ.get('ARTIFACT_RECOVERY_STATE_DIR', '/var/lib/artifact-recovery'))
        if not source_manifest.is_absolute():
            raise WorkerError('ARTIFACT_RECOVERY_SOURCE_MANIFEST must be absolute')
        if not state_dir.is_absolute():
            raise WorkerError('ARTIFACT_RECOVERY_STATE_DIR must be absolute')
        return cls(coordinator_url=coordinator_url, coordinator_token=coordinator_token, source_manifest=source_manifest, state_dir=state_dir, worker_id=worker_id, lease_seconds=parse_int_env('ARTIFACT_RECOVERY_LEASE_SECONDS', DEFAULT_LEASE_SECONDS, 30, 3600), request_timeout_seconds=parse_int_env('ARTIFACT_RECOVERY_REQUEST_TIMEOUT_SECONDS', 20, 1, 120), max_response_bytes=parse_int_env('ARTIFACT_RECOVERY_MAX_RESPONSE_BYTES', DEFAULT_MAX_RESPONSE_BYTES, 1024, 16 * 1024 * 1024), max_pages_per_source=parse_int_env('ARTIFACT_RECOVERY_MAX_PAGES_PER_SOURCE', 200, 1, MAX_PAGES), source_page_size=parse_int_env('ARTIFACT_RECOVERY_SOURCE_PAGE_SIZE', DEFAULT_SOURCE_PAGE_SIZE, 1, 100), retry_delay_seconds=parse_int_env('ARTIFACT_RECOVERY_RETRY_DELAY_SECONDS', DEFAULT_RETRY_DELAY_SECONDS, 30, 24 * 60 * 60), poll_seconds=parse_int_env('ARTIFACT_RECOVERY_POLL_SECONDS', 30, 1, 600), once=once)

    def public_summary(self) -> dict[str, Any]:
        return {'coordinator_url': self.coordinator_url, 'source_manifest': str(self.source_manifest), 'state_dir': str(self.state_dir), 'worker_id_sha256': redacted_digest(self.worker_id), 'lease_seconds': self.lease_seconds, 'request_timeout_seconds': self.request_timeout_seconds, 'max_response_bytes': self.max_response_bytes, 'max_pages_per_source': self.max_pages_per_source, 'source_page_size': self.source_page_size, 'retry_delay_seconds': self.retry_delay_seconds, 'poll_seconds': self.poll_seconds, 'once': self.once, 'coordinator_token': REDACTED}

@dataclass(frozen=True)
class SourceSpec:
    source: str
    endpoint: str
    token_env: str
    identity: str
    capabilities: tuple[str, ...]
    max_age_seconds: int
    required: bool

@dataclass(frozen=True)
class SourceManifest:
    required_sources: tuple[str, ...]
    optional_sources: tuple[str, ...]
    specs: tuple[SourceSpec, ...]

    @classmethod
    def from_value(cls, raw: Any) -> 'SourceManifest':
        root = expect_object(raw, 'source manifest')
        strict_keys(root, 'source manifest', {'schema_version', 'required_sources', 'optional_sources', 'sources'})
        if root.get('schema_version') != SOURCE_MANIFEST_SCHEMA:
            raise WorkerError(f'source manifest schema_version must be {SOURCE_MANIFEST_SCHEMA}')
        required = _source_names(root.get('required_sources'), 'required_sources')
        optional = _source_names(root.get('optional_sources', []), 'optional_sources')
        overlap = set(required) & set(optional)
        if overlap:
            raise WorkerError(f'source manifest source sets overlap: {sorted(overlap)}')
        raw_specs = expect_list(root.get('sources'), 'sources', MAX_SOURCES)
        specs: list[SourceSpec] = []
        for index, raw_spec in enumerate(raw_specs):
            field = f'sources[{index}]'
            spec = expect_object(raw_spec, field)
            strict_keys(spec, field, {'source', 'endpoint', 'token_env', 'identity', 'capabilities', 'max_age_seconds'})
            source = expect_string(spec.get('source'), f'{field}.source', 32)
            if source not in SUPPORTED_SOURCES:
                raise WorkerError(f'{field}.source is unsupported')
            endpoint = validate_base_url(expect_string(spec.get('endpoint'), f'{field}.endpoint', 2048), f'{field}.endpoint')
            token_env = validate_env_name(spec.get('token_env'), f'{field}.token_env')
            identity = expect_string(spec.get('identity'), f'{field}.identity', 300)
            capabilities_raw = expect_list(spec.get('capabilities'), f'{field}.capabilities', 32)
            capabilities = tuple(sorted({expect_string(value, f'{field}.capabilities', 80) for value in capabilities_raw}))
            if not capabilities:
                raise WorkerError(f'{field}.capabilities must not be empty')
            max_age = spec.get('max_age_seconds', 900)
            if isinstance(max_age, bool) or not isinstance(max_age, int) or (not 1 <= max_age <= 31 * 24 * 60 * 60):
                raise WorkerError(f'{field}.max_age_seconds must be between 1 and 2678400')
            specs.append(SourceSpec(source=source, endpoint=endpoint, token_env=token_env, identity=identity, capabilities=capabilities, max_age_seconds=max_age, required=source in required))
        names = [spec.source for spec in specs]
        if len(names) != len(set(names)):
            raise WorkerError('sources contains duplicate source adapters')
        expected = set(required) | set(optional)
        actual = set(names)
        if actual != expected:
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            raise WorkerError(f'source manifest adapter mismatch; missing={missing}, unexpected={unexpected}')
        if not required:
            raise WorkerError('required_sources must not be empty')
        return cls(tuple(required), tuple(optional), tuple(specs))

    def enforce_job_contract(self, payload: Mapping[str, Any]) -> None:
        source_contract = expect_object(payload.get('source_contract'), 'job.payload.source_contract')
        if source_contract.get('scan_all_accessible_authorized_chatgpt_threads') is True:
            if 'chatgpt' not in self.required_sources:
                raise WorkerError('job requires ChatGPT coverage but chatgpt is not a required source')
        if source_contract.get('scan_authorized_claude_session_exports') is True:
            if 'claude' not in set(self.required_sources) | set(self.optional_sources):
                raise WorkerError('job requires configured Claude coverage but claude is absent')

def _source_names(value: Any, field: str) -> list[str]:
    raw = expect_list(value, field, MAX_SOURCES)
    result = []
    for index, entry in enumerate(raw):
        source = expect_string(entry, f'{field}[{index}]', 32)
        if source not in SUPPORTED_SOURCES:
            raise WorkerError(f'{field}[{index}] is unsupported')
        result.append(source)
    if len(result) != len(set(result)):
        raise WorkerError(f'{field} contains duplicate sources')
    return sorted(result)
