from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import smtplib
import socket
import ssl
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.client import HTTPResponse
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
TASK_TYPE = 'artifact_recovery'
JOB_SCHEMA = 'artifact_recovery_job.v1'
SOURCE_MANIFEST_SCHEMA = 'artifact_recovery_sources.v1'
SOURCE_PAGE_SCHEMA = 'artifact_recovery_source_page.v1'
OBSERVATION_SCHEMA = 'artifact_recovery_observation.v1'
COVERAGE_SCHEMA = 'artifact_recovery_source_coverage.v1'
COMPLETION_SCHEMA = 'artifact_recovery_completion.v1'
DELIVERY_SCHEMA = 'artifact_recovery_report_delivery.v1'
SUPPORTED_SOURCES = {'chatgpt', 'claude', 'linear', 'github', 'local_repo', 'file_library', 'conversation'}
ORIGIN_SOURCES = {'chatgpt', 'claude', 'file_library', 'conversation', 'task'}
ENV_NAME_RE = re.compile('^[A-Z_][A-Z0-9_]*$')
WORKER_ID_RE = re.compile('^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$')
RUN_KEY_RE = re.compile('^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,199}$')
CURSOR_RE = re.compile('^[A-Za-z0-9][A-Za-z0-9._~:+/=-]{0,511}$')
SOURCE_ID_RE = re.compile('^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$')
EMAIL_RE = re.compile('^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$')
MAX_SOURCES = 32
MAX_PAGES = 1000
MAX_ITEMS = 5000
DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_SOURCE_PAGE_SIZE = 50
DEFAULT_WINDOW_HOURS = 96
DEFAULT_OVERLAP_HOURS = 6
DEFAULT_LEASE_SECONDS = 300
DEFAULT_RETRY_DELAY_SECONDS = 900
REDACTED = '[REDACTED]'
PRIVATE_KEY_MARKER = '-----BEGIN ' + 'PRIVATE KEY-----'
SECRET_MARKERS = ('ghp_', 'github_pat_', 'lin_api_', 'sk-', 'xoxb-', 'xoxp-', 'AKIA', PRIVATE_KEY_MARKER)

class WorkerError(RuntimeError):

    def __init__(self, message: str, *, retryable: bool=False, error_class: str='validation') -> None:
        super().__init__(message)
        self.retryable = retryable
        self.error_class = error_class

class NoRedirect(HTTPRedirectHandler):

    def redirect_request(self, req: Request, fp: HTTPResponse, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def format_instant(value: datetime) -> str:
    if value.tzinfo is None:
        raise WorkerError('timestamps must be timezone-aware')
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

def parse_instant(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise WorkerError(f'{field} must be an ISO-8601 timestamp')
    normalized = value[:-1] + '+00:00' if value.endswith('Z') else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise WorkerError(f'{field} must be an ISO-8601 timestamp') from exc
    if parsed.tzinfo is None:
        raise WorkerError(f'{field} must include a UTC offset or Z')
    return parsed.astimezone(timezone.utc)

def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')

def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()

def redacted_digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def ensure_public_safe(value: Any, field: str='$') -> None:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False)
    for marker in SECRET_MARKERS:
        if marker in encoded:
            raise WorkerError(f'{field} contains credential-like material')
    if re.search('(?i)\\bauthorization\\b\\s*[:=]\\s*[\\"\']?(?:bearer|token)\\s+', encoded):
        raise WorkerError(f'{field} contains authorization material')

def atomic_write_json(path: Path, value: Any) -> None:
    ensure_public_safe(value, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n'
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

def load_json_file(path: Path, field: str) -> Any:
    try:
        with path.open('r', encoding='utf-8') as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise WorkerError(f'{field} does not exist') from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerError(f'{field} is not readable valid JSON') from exc
    return value

def expect_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkerError(f'{field} must be a JSON object')
    return dict(value)

def expect_list(value: Any, field: str, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise WorkerError(f'{field} must be a JSON array')
    if len(value) > maximum:
        raise WorkerError(f'{field} exceeds the item limit')
    return list(value)

def expect_string(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkerError(f'{field} must be a non-empty string')
    if len(value) > maximum:
        raise WorkerError(f'{field} exceeds the length limit')
    return value.strip()

def strict_keys(value: Mapping[str, Any], field: str, allowed: set[str]) -> None:
    extra = set(value) - allowed
    if extra:
        raise WorkerError(f'{field} contains unsupported keys: {sorted(extra)}')

def parse_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    raise WorkerError(f'{name} must be a boolean')

def parse_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise WorkerError(f'{name} must be an integer') from exc
    if not minimum <= value <= maximum:
        raise WorkerError(f'{name} must be between {minimum} and {maximum}')
    return value

def validate_base_url(raw: str, field: str, *, allow_loopback_http: bool=True) -> str:
    value = raw.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {'https', 'http'}:
        raise WorkerError(f'{field} must use HTTPS or loopback HTTP')
    if parsed.username or parsed.password:
        raise WorkerError(f'{field} must not contain credentials')
    if parsed.query or parsed.fragment:
        raise WorkerError(f'{field} must not contain query or fragment')
    if not parsed.hostname:
        raise WorkerError(f'{field} must include a hostname')
    loopback = parsed.hostname in {'localhost', '127.0.0.1', '::1'}
    if parsed.scheme == 'http' and (not (allow_loopback_http and loopback)):
        raise WorkerError(f'{field} must use HTTPS outside loopback')
    return value.rstrip('/')

def validate_env_name(value: Any, field: str) -> str:
    name = expect_string(value, field, 128)
    if not ENV_NAME_RE.fullmatch(name):
        raise WorkerError(f'{field} must be an environment variable name')
    return name

def safe_run_component(value: str) -> str:
    if not RUN_KEY_RE.fullmatch(value):
        raise WorkerError('run key has an invalid shape')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]
