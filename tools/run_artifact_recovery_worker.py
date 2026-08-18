#!/usr/bin/env python3
"""Lease-bound, fail-closed worker for nightly artifact-recovery jobs."""
from __future__ import annotations

import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from artifact_recovery_worker_runtime.common import *
from artifact_recovery_worker_runtime.config import *
from artifact_recovery_worker_runtime.transport import *
from artifact_recovery_worker_runtime.sources import *
from artifact_recovery_worker_runtime.sources import _deduplicate_items
from artifact_recovery_worker_runtime.delivery import *
from artifact_recovery_worker_runtime.engine import *

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='claim at most one artifact-recovery job and exit')
    parser.add_argument('--check-config', action='store_true', help='validate protected configuration without contacting a source')
    return parser

def main(argv: Sequence[str] | None=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = WorkerConfig.from_env(once=args.once)
        manifest = SourceManifest.from_value(load_json_file(config.source_manifest, 'source manifest'))
        if args.check_config:
            output = {'status': 'valid', 'config': config.public_summary(), 'required_sources': list(manifest.required_sources), 'optional_sources': list(manifest.optional_sources), 'source_identity_sha256': {spec.source: redacted_digest(spec.identity) for spec in manifest.specs}}
            ensure_public_safe(output, 'configuration summary')
            print(json.dumps(output, indent=2, sort_keys=True))
            return 0
        http = JsonHttpClient(timeout_seconds=config.request_timeout_seconds, max_response_bytes=config.max_response_bytes)
        coordinator = CoordinatorClient(config, http)
        engine = RecoveryEngine(config, http=http)
        while True:
            try:
                processed = process_one(config, coordinator, engine)
            except WorkerError as exc:
                print(f'error: {exc}', file=sys.stderr)
                if config.once or not exc.retryable:
                    return 2
                time.sleep(config.poll_seconds)
                continue
            if config.once:
                return 0
            if not processed:
                time.sleep(config.poll_seconds)
    except (OSError, WorkerError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
if __name__ == '__main__':
    raise SystemExit(main())
