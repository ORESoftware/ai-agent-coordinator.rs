from __future__ import annotations

from .common import *
from .config import *

class JsonHttpClient:

    def __init__(self, *, timeout_seconds: int, max_response_bytes: int, opener: Any | None=None) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.opener = opener or build_opener(NoRedirect())

    def request(self, method: str, url: str, *, token: str | None, payload: Mapping[str, Any] | None=None, user_agent: str) -> tuple[int, dict[str, Any] | None]:
        body = None if payload is None else canonical_json(payload)
        headers = {'Accept': 'application/json', 'User-Agent': user_agent, 'Cache-Control': 'no-store'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if token:
            headers['Authorization'] = f'Bearer {token}'
        request = Request(url, data=body, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                raw = response.read(self.max_response_bytes + 1)
        except HTTPError as exc:
            try:
                exc.read(min(4096, self.max_response_bytes))
            except OSError:
                pass
            raise WorkerError(f'remote service returned HTTP {exc.code}', retryable=exc.code in {408, 409, 425, 429} or 500 <= exc.code <= 599, error_class='rate_limited' if exc.code == 429 else 'availability') from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise WorkerError('remote service request failed', retryable=True, error_class='availability') from exc
        if len(raw) > self.max_response_bytes:
            raise WorkerError('remote response exceeded the size limit', retryable=False, error_class='validation')
        if status == 204:
            return (status, None)
        try:
            value = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise WorkerError('remote service response was not valid JSON', retryable=False, error_class='validation') from exc
        if not isinstance(value, dict):
            raise WorkerError('remote service response must be a JSON object', retryable=False, error_class='validation')
        return (status, value)

class CoordinatorClient:

    def __init__(self, config: WorkerConfig, http: JsonHttpClient) -> None:
        self.config = config
        self.http = http

    def claim(self) -> dict[str, Any] | None:
        status, response = self.http.request('POST', f'{self.config.coordinator_url}/v1/jobs/claim', token=self.config.coordinator_token, payload={'worker_id': self.config.worker_id, 'task_types': [TASK_TYPE], 'lease_seconds': self.config.lease_seconds}, user_agent='ai-agent-coordinator-artifact-recovery-worker/1')
        if status == 204 or response is None:
            return None
        job = expect_object(response.get('job'), 'coordinator response.job')
        self._validate_claim(job)
        return job

    def _validate_claim(self, job: Mapping[str, Any]) -> None:
        if job.get('task_type') != TASK_TYPE:
            raise WorkerError('coordinator returned an unsupported task type')
        job_id = expect_string(job.get('id'), 'job.id', 200)
        if not SOURCE_ID_RE.fullmatch(job_id):
            raise WorkerError('job.id has an invalid shape')
        if job.get('claimed_by') not in {None, self.config.worker_id}:
            raise WorkerError('coordinator returned a job leased to another worker')
        payload = expect_object(job.get('payload'), 'job.payload')
        if payload.get('schema_version') != JOB_SCHEMA:
            raise WorkerError(f'job payload schema_version must be {JOB_SCHEMA}')

    def heartbeat(self, job_id: str) -> None:
        self.http.request('POST', f'{self.config.coordinator_url}/v1/jobs/{job_id}/heartbeat', token=self.config.coordinator_token, payload={'worker_id': self.config.worker_id, 'lease_seconds': self.config.lease_seconds}, user_agent='ai-agent-coordinator-artifact-recovery-worker/1')

    def complete(self, job_id: str, *, succeeded: bool, result: Mapping[str, Any], error: str | None, retryable: bool) -> None:
        safe_result = dict(result)
        ensure_public_safe(safe_result, 'completion result')
        safe_error = None
        if error:
            safe_error = error[:500]
            ensure_public_safe(safe_error, 'completion error')
        self.http.request('POST', f'{self.config.coordinator_url}/v1/jobs/{job_id}/complete', token=self.config.coordinator_token, payload={'worker_id': self.config.worker_id, 'outcome': 'succeeded' if succeeded else 'failed', 'result': safe_result, 'error': safe_error, 'retryable': retryable, 'retry_delay_seconds': self.config.retry_delay_seconds}, user_agent='ai-agent-coordinator-artifact-recovery-worker/1')

class LeaseHeartbeat:

    def __init__(self, coordinator: CoordinatorClient, job_id: str, lease_seconds: int) -> None:
        self.coordinator = coordinator
        self.job_id = job_id
        self.interval = max(10, lease_seconds // 3)
        self.stop_event = threading.Event()
        self.failed: WorkerError | None = None
        self.thread = threading.Thread(target=self._run, name='artifact-recovery-heartbeat', daemon=True)

    def __enter__(self) -> 'LeaseHeartbeat':
        self.thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop_event.set()
        self.thread.join(timeout=self.interval + 2)
        if exc is None and self.failed is not None:
            raise self.failed

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                self.coordinator.heartbeat(self.job_id)
            except WorkerError as exc:
                self.failed = WorkerError('coordinator lease heartbeat failed', retryable=True, error_class=exc.error_class)
                self.stop_event.set()
                return
