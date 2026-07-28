#!/usr/bin/env python3
"""Send one signed, disposable GitHub push payload to the coordinator.

The helper reads the webhook secret from an explicitly named environment
variable. It never accepts a secret as a command-line value and never prints the
secret or calculated signature.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

MAX_RESPONSE_BYTES = 64 * 1024
ISSUE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$", re.IGNORECASE)
COMMIT_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
ALLOWED_KEYWORDS = {
    "fixes",
    "closes",
    "resolves",
    "completes",
    "implements",
    "refs",
    "references",
    "part of",
    "related to",
    "contributes to",
}


@dataclass(frozen=True)
class PilotRequest:
    endpoint: str
    delivery: str
    body: bytes
    signature: str


def validate_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.username or parsed.password:
        raise ValueError("endpoint credentials are not allowed")
    if parsed.query or parsed.fragment:
        raise ValueError("endpoint query strings and fragments are not allowed")
    if parsed.path.rstrip("/") != "/webhooks/github":
        raise ValueError("endpoint path must be /webhooks/github")
    if parsed.scheme == "https" and parsed.hostname:
        return endpoint
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("endpoint must use HTTPS, or HTTP on a loopback host")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname == "localhost"
    if not loopback:
        raise ValueError("plain HTTP is allowed only for localhost or loopback addresses")
    return endpoint


def validate_commit(commit: str) -> str:
    if not COMMIT_PATTERN.fullmatch(commit) or set(commit) == {"0"}:
        raise ValueError("commit must be a nonzero 40- or 64-character hexadecimal identifier")
    return commit.lower()


def validate_repository(organization: str, repository: str) -> str:
    expected_prefix = f"{organization}/"
    if not repository.startswith(expected_prefix) or repository.count("/") != 1:
        raise ValueError("repository must be in owner/name form and match --organization")
    if not all(part and re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in repository.split("/")):
        raise ValueError("organization and repository contain unsupported characters")
    return repository


def build_payload(
    *,
    organization: str,
    repository: str,
    branch: str,
    commit: str,
    issue: str,
    keyword: str,
) -> dict[str, object]:
    repository = validate_repository(organization, repository)
    commit = validate_commit(commit)
    normalized_issue = issue.upper()
    if not ISSUE_PATTERN.fullmatch(normalized_issue):
        raise ValueError("issue must look like DEN-123")
    normalized_keyword = " ".join(keyword.lower().split())
    if normalized_keyword not in ALLOWED_KEYWORDS:
        raise ValueError("unsupported Linear magic word")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or branch.startswith("/"):
        raise ValueError("branch contains unsupported characters")

    return {
        "ref": f"refs/heads/{branch}",
        "after": commit,
        "deleted": False,
        "forced": False,
        "repository": {
            "full_name": repository,
            "default_branch": branch,
            "fork": False,
        },
        "commits": [
            {
                "id": commit,
                "message": f"{normalized_keyword.title()} {normalized_issue} pilot verification",
            }
        ],
    }


def sign(secret: str, body: bytes) -> str:
    if not secret:
        raise ValueError("webhook secret is empty")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_request(
    *,
    endpoint: str,
    secret: str,
    payload: dict[str, object],
    delivery: str | None = None,
) -> PilotRequest:
    endpoint = validate_endpoint(endpoint)
    delivery = delivery or str(uuid.uuid4())
    try:
        uuid.UUID(delivery)
    except ValueError as error:
        raise ValueError("delivery must be a UUID") from error
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return PilotRequest(
        endpoint=endpoint,
        delivery=delivery,
        body=body,
        signature=sign(secret, body),
    )


def redacted_preview(request: PilotRequest) -> dict[str, object]:
    return {
        "endpoint": request.endpoint,
        "headers": {
            "content-type": "application/json",
            "x-github-event": "push",
            "x-github-delivery": request.delivery,
            "x-hub-signature-256": "[REDACTED:HMAC]",
        },
        "payload": json.loads(request.body),
    }


def send(request: PilotRequest, timeout_seconds: float) -> dict[str, object]:
    outbound = urllib.request.Request(
        request.endpoint,
        method="POST",
        data=request.body,
        headers={
            "content-type": "application/json",
            "x-github-event": "push",
            "x-github-delivery": request.delivery,
            "x-hub-signature-256": request.signature,
            "user-agent": "ai-agent-coordinator-pilot/1",
        },
    )
    try:
        with urllib.request.urlopen(outbound, timeout=timeout_seconds) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise RuntimeError("coordinator response exceeded 64 KiB")
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"coordinator returned HTTP {response.status}")
    except urllib.error.HTTPError as error:
        response_body = error.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(
            f"coordinator returned HTTP {error.code}: {response_body}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"coordinator request failed: {error.reason}") from error

    if not body:
        return {}
    decoded = json.loads(body)
    if not isinstance(decoded, dict):
        raise RuntimeError("coordinator returned a non-object JSON response")
    return decoded


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--organization", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--keyword", default="refs", choices=sorted(ALLOWED_KEYWORDS))
    parser.add_argument("--secret-env", required=True)
    parser.add_argument("--delivery")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.timeout_seconds <= 0 or args.timeout_seconds > 60:
        raise ValueError("timeout must be greater than 0 and no more than 60 seconds")
    secret = os.environ.get(args.secret_env)
    if secret is None:
        raise ValueError(f"secret environment variable {args.secret_env} is not set")
    payload = build_payload(
        organization=args.organization,
        repository=args.repository,
        branch=args.branch,
        commit=args.commit,
        issue=args.issue,
        keyword=args.keyword,
    )
    request = build_request(
        endpoint=args.endpoint,
        secret=secret,
        payload=payload,
        delivery=args.delivery,
    )
    if args.dry_run:
        print(json.dumps(redacted_preview(request), indent=2, sort_keys=True))
        return 0
    print(json.dumps(send(request, args.timeout_seconds), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
