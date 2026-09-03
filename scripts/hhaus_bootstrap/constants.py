#!/usr/bin/env python3
"""Sealed H/HAUS repository-fleet contracts and common scaffold generators."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "repository-fleets" / "hhaus-org-standard.json"
EXPECTED_ORGANIZATION = "hhaus-org"
EXPECTED_REPOSITORIES = (
    "hhaus-interfaces",
    "hhaus-lib-core",
    "hhaus-orm-core",
    "hhaus-clients",
    "hhaus-sync",
    "hhaus-flutter",
    "hhaus-desktop-app.rs",
    "hhaus-lambdas",
)
EXPECTED_RATE_LIMIT_LAYERS = (
    "cloudflare-edge",
    "gateway-load-balancer",
    "service-runtime-lru",
    "distributed-redis-coordinator",
    "durable-security-billing",
)
EXPECTED_PLATFORM_DEPENDENCIES = {
    "dependency_orchestrator": "zed-pkg/zed-pkg",
    "auth_contracts": "shared-auth/shared-auth-interfaces",
    "auth_core": "shared-auth/shared-auth-lib-core",
    "auth_clients": "shared-auth/shared-auth-clients",
    "middleware": "ORESoftware/ores-middleware",
    "telemetry": "ores-otel/ores-otel-clients",
    "rate_limit_core": "ores-rate-limit/ores-rl-lib-core",
    "rate_limit_infra": "ores-rate-limit/ores-rl-infra",
    "sync": "opto-sync/opto-sync",
}
EXPECTED_INTERNAL_DEPENDENCIES = {
    "hhaus-interfaces": [],
    "hhaus-lib-core": ["hhaus-interfaces"],
    "hhaus-orm-core": ["hhaus-interfaces", "hhaus-lib-core"],
    "hhaus-clients": ["hhaus-interfaces", "hhaus-lib-core"],
    "hhaus-sync": ["hhaus-interfaces", "hhaus-lib-core"],
    "hhaus-flutter": ["hhaus-clients", "hhaus-lib-core", "hhaus-sync"],
    "hhaus-desktop-app.rs": ["hhaus-clients", "hhaus-lib-core", "hhaus-sync"],
    "hhaus-lambdas": ["hhaus-interfaces", "hhaus-lib-core", "hhaus-orm-core"],
}
EXPECTED_BACKEND_INTERNAL_DEPENDENCIES = {
    "hhaus-interfaces": [],
    "hhaus-lib-core": [],
    "hhaus-orm-core": [],
    "hhaus-clients": [],
    "hhaus-sync": ["hhaus-orm-core"],
    "hhaus-flutter": [],
    "hhaus-desktop-app.rs": [],
    "hhaus-lambdas": [],
}
EXPECTED_EXTERNAL_DEPENDENCIES = {
    "hhaus-interfaces": [
        "zed-pkg/zed-pkg",
        "shared-auth/shared-auth-interfaces",
    ],
    "hhaus-lib-core": [
        "zed-pkg/zed-pkg",
        "shared-auth/shared-auth-lib-core",
        "ORESoftware/ores-middleware",
        "ores-otel/ores-otel-clients",
        "ores-rate-limit/ores-rl-lib-core",
    ],
    "hhaus-orm-core": [
        "zed-pkg/zed-pkg",
        "shared-auth/shared-auth-orm-core",
    ],
    "hhaus-clients": [
        "zed-pkg/zed-pkg",
        "shared-auth/shared-auth-clients",
        "ores-otel/ores-otel-clients",
        "ores-rate-limit/ores-rl-lib-core",
    ],
    "hhaus-sync": [
        "zed-pkg/zed-pkg",
        "opto-sync/opto-sync",
        "shared-auth/shared-auth-sync",
        "ores-otel/ores-otel-sync",
        "ores-rate-limit/ores-rl-lib-core",
    ],
    "hhaus-flutter": [
        "zed-pkg/zed-pkg",
        "shared-auth/shared-auth-flutter",
        "ores-otel/ores-otel-flutter",
        "ores-rate-limit/ores-rl-flutter",
    ],
    "hhaus-desktop-app.rs": [
        "zed-pkg/zed-pkg",
        "shared-auth/shared-auth-lib-core",
        "ores-otel/ores-otel-clients",
        "ores-rate-limit/ores-rl-lib-core",
    ],
    "hhaus-lambdas": [
        "zed-pkg/zed-pkg",
        "shared-auth/shared-auth-lambdas",
        "ORESoftware/ores-middleware",
        "ores-otel/ores-otel-clients",
        "ores-rate-limit/ores-rl-lib-core",
    ],
}
EXPECTED_BACKEND_ONLY = {
    "hhaus-interfaces": False,
    "hhaus-lib-core": False,
    "hhaus-orm-core": True,
    "hhaus-clients": False,
    "hhaus-sync": False,
    "hhaus-flutter": False,
    "hhaus-desktop-app.rs": False,
    "hhaus-lambdas": True,
}
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"lin_api_[A-Za-z0-9_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
CONFLICT_MARKER = re.compile(r"(?m)^(?:<{7}|={7}|>{7})")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class BootstrapError(RuntimeError):
    """A bounded, user-safe bootstrap failure."""
