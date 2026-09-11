from __future__ import annotations

import contextlib
from typing import Iterator

from .common import *
from .config import *
from .transport import *
from .sources import *
from .delivery import *


class RecoveryEngine:
    def __init__(
        self,
        config: WorkerConfig,
        *,
        http: JsonHttpClient | None = None,
        environment: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.config = config
        self.http = http or JsonHttpClient(
            timeout_seconds=config.request_timeout_seconds,
            max_response_bytes=config.max_response_bytes,
        )
        self.environment = environment if environment is not None else os.environ
        self.clock = clock

    @contextlib.contextmanager
    def state_transaction(self) -> Iterator[None]:
        """Fence only shared cursor/ledger transactions.

        The default engine is single-process and needs no external fence. A
        multi-process runner can override this context manager without wrapping
        source collection, report delivery, or the rest of a complete job.
        """

        yield

    def run(self, job: Mapping[str, Any]) -> dict[str, Any]:
        job_id, payload, run_key = validate_job(job)
        started_at = self.clock()
        if started_at.tzinfo is None:
            raise WorkerError("worker clock must be timezone-aware")

        manifest = SourceManifest.from_value(
            load_json_file(self.config.source_manifest, "source manifest")
        )
        manifest.enforce_job_contract(payload)
        run = expect_object(payload.get("run"), "job.payload.run")
        scheduled_for = parse_instant(
            run.get("scheduled_for"), "job.payload.run.scheduled_for"
        )
        if scheduled_for > started_at + timedelta(hours=1):
            raise WorkerError(
                "job schedule is beyond the allowed clock skew",
                retryable=False,
                error_class="clock_skew",
            )

        window_end = started_at
        window_start = window_end - timedelta(
            hours=DEFAULT_WINDOW_HOURS + DEFAULT_OVERLAP_HOURS
        )
        cursor_path = self.config.state_dir / "cursors.json"
        with self.state_transaction():
            cursor_state = load_cursor_state(cursor_path)

        collector = SourceCollector(
            http=self.http,
            config=self.config,
            environment=self.environment,
        )
        collections: list[SourceCollection] = []
        for spec in manifest.specs:
            prior = cursor_state["sources"].get(spec.source, {})
            prior_watermark = (
                prior.get("high_water_mark") if isinstance(prior, dict) else None
            )
            collections.append(
                collector.collect(
                    spec,
                    window_start=window_start,
                    window_end=window_end,
                    prior_high_water_mark=prior_watermark,
                )
            )

        generated_at = self.clock()
        coverage = build_coverage(manifest, collections, generated_at=generated_at)
        all_items = _deduplicate_items(
            item for collection in collections for item in collection.items
        )
        observation = {
            "schema_version": OBSERVATION_SCHEMA,
            "generated_at": format_instant(generated_at),
            "batch": {
                "id": run_key,
                "complete": coverage["summary"]["complete"],
                "next_cursor": None,
                "source_window": (
                    f"{format_instant(window_start)}/{format_instant(window_end)}"
                ),
            },
            "items": all_items,
        }
        ensure_public_safe(observation, "observation")

        try:
            from artifact_recovery.common import now_utc as ledger_now_utc
            from artifact_recovery.ledger import (
                atomic_write_json as ledger_atomic_write_json,
                reconcile,
                summary_document,
            )
            from artifact_recovery.observation import validate_observation
            from artifact_recovery.source_coverage import build_source_coverage_report
        except ImportError as exc:
            raise WorkerError(
                "artifact-recovery ledger engine is unavailable",
                retryable=False,
                error_class="availability",
            ) from exc

        observation = validate_observation(observation)
        coverage = build_source_coverage_report(
            coverage, now=format_instant(generated_at)
        )
        run_dir = self.config.state_dir / "runs" / safe_run_component(run_key)
        atomic_write_json(run_dir / "observation.json", observation)
        atomic_write_json(run_dir / "source-coverage.json", coverage)

        shared_ledger_path = self.config.state_dir / "ledger.json"
        tracking = expect_object(payload.get("tracking"), "job.payload.tracking")
        target_task_id = expect_string(
            tracking.get("local_cli_task_id"),
            "job.payload.tracking.local_cli_task_id",
            200,
        )
        batch_size = 50
        chunks = [
            all_items[index : index + batch_size]
            for index in range(0, len(all_items), batch_size)
        ] or [[]]

        # Only shared cursor/ledger read-modify-write work is serialized. Source
        # pagination and report delivery remain concurrent across up to three
        # lease-bound workers.
        with self.state_transaction():
            latest_cursor_state = load_cursor_state(cursor_path)
            previous = (
                load_json_file(shared_ledger_path, "artifact-recovery ledger")
                if shared_ledger_path.exists()
                else None
            )
            ledger = previous
            queue: dict[str, Any] | None = None
            for index, chunk in enumerate(chunks):
                chunk_observation = {
                    **observation,
                    "batch": {
                        **observation["batch"],
                        "id": f"{run_key}:batch:{index + 1}",
                        "complete": (
                            observation["batch"]["complete"]
                            and index == len(chunks) - 1
                        ),
                        "next_cursor": (
                            None
                            if index == len(chunks) - 1
                            else f"batch:{index + 2}"
                        ),
                    },
                    "items": chunk,
                }
                ledger, queue = reconcile(
                    chunk_observation,
                    ledger,
                    now=ledger_now_utc(format_instant(generated_at)),
                    batch_size=batch_size,
                    target_task_id=target_task_id,
                )
            assert ledger is not None and queue is not None
            ledger_atomic_write_json(shared_ledger_path, ledger)
            atomic_write_json(
                cursor_path,
                _merge_cursor_state(
                    latest_cursor_state,
                    collections,
                    updated_at=generated_at,
                ),
            )

        # These are immutable per-run snapshots, not shared mutable authority.
        ledger_atomic_write_json(run_dir / "ledger.json", ledger)
        ledger_atomic_write_json(run_dir / "cli-queue.json", queue)
        summary = summary_document(ledger)
        ledger_summary = summary["summary"]

        succeeded = (
            coverage["summary"]["complete"]
            and observation["batch"]["complete"]
            and ledger_summary["actionable"] == 0
            and ledger_summary["blocked"] == 0
        )
        completed_at = self.clock()
        finding_counts = ledger_summary.get("finding_counts", {})
        gates = {
            "source_coverage_complete": coverage["summary"]["complete"],
            "source_pagination_complete": observation["batch"]["complete"],
            "unclassified_items": 0,
            "unowned_items": int(finding_counts.get("ownership_ambiguous", 0)),
            "missing_evidence_items": int(
                finding_counts.get("remote_evidence_incomplete", 0)
            ),
            "actionable_items": ledger_summary["actionable"],
            "blocked_items": ledger_summary["blocked"],
        }
        succeeded = succeeded and all(
            value is True if isinstance(value, bool) else value == 0
            for value in gates.values()
        )
        completion = {
            "schema_version": COMPLETION_SCHEMA,
            "job_id": job_id,
            "run_key": run_key,
            "run_key_sha256": redacted_digest(run_key),
            "worker_id_sha256": redacted_digest(self.config.worker_id),
            "started_at": format_instant(started_at),
            "completed_at": format_instant(completed_at),
            "outcome": "succeeded" if succeeded else "blocked",
            "source_coverage": coverage["summary"],
            "summary": ledger_summary,
            "terminal_gates": gates,
            "artifact_digests": {
                "observation_sha256": sha256_value(observation),
                "source_coverage_sha256": coverage["report_sha256"],
                "ledger_sha256": summary["ledger_digest"],
                "cli_queue_sha256": sha256_value(queue),
            },
            "retryable": not succeeded,
        }
        completion["completion_sha256"] = sha256_value(completion)
        ensure_public_safe(completion, "completion")

        delivery = deliver_report_email(completion, environment=self.environment)
        atomic_write_json(run_dir / "report-delivery.json", delivery)
        completion["report_delivery"] = delivery
        completion["completion_sha256"] = sha256_value(
            {
                key: value
                for key, value in completion.items()
                if key != "completion_sha256"
            }
        )
        atomic_write_json(run_dir / "completion.json", completion)
        emit_metric(
            "artifact_recovery_terminal",
            {
                "job_id_sha256": redacted_digest(job_id),
                "outcome": completion["outcome"],
                "coverage": coverage["summary"]["status"],
                "entries": ledger_summary["entries"],
                "actionable": ledger_summary["actionable"],
                "blocked": ledger_summary["blocked"],
                "delivery": delivery["status"],
            },
        )
        return completion


def _merge_cursor_state(
    current: Mapping[str, Any],
    collections: Sequence[SourceCollection],
    *,
    updated_at: datetime,
) -> dict[str, Any]:
    """Merge source watermarks monotonically while holding state_transaction."""

    root = expect_object(current, "cursor state")
    if root.get("schema_version") != "artifact_recovery_cursors.v1":
        raise WorkerError("cursor state has an unsupported schema")
    sources = expect_object(root.get("sources"), "cursor state.sources")

    for collection in collections:
        high_water_mark = collection.high_water_mark
        if high_water_mark is None:
            continue
        candidate = {
            "high_water_mark": high_water_mark,
            "coverage_state": collection.receipt["state"],
            "receipt_sha256": sha256_value(collection.receipt),
        }
        prior = sources.get(collection.spec.source)
        if prior is not None:
            prior = expect_object(prior, f"cursor state.sources.{collection.spec.source}")
            prior_watermark = expect_string(
                prior.get("high_water_mark"),
                f"cursor state.sources.{collection.spec.source}.high_water_mark",
                128,
            )
            if parse_instant(
                prior_watermark,
                f"cursor state.sources.{collection.spec.source}.high_water_mark",
            ) > parse_instant(
                high_water_mark,
                f"collection.{collection.spec.source}.high_water_mark",
            ):
                continue
        sources[collection.spec.source] = candidate

    return {
        "schema_version": "artifact_recovery_cursors.v1",
        "updated_at": format_instant(updated_at),
        "sources": dict(sorted(sources.items())),
    }


def emit_metric(name: str, fields: Mapping[str, Any]) -> None:
    event = {
        "schema_version": "artifact_recovery_metric.v1",
        "name": name,
        "observed_at": format_instant(utc_now()),
        "fields": dict(fields),
    }
    ensure_public_safe(event, "metric")
    print(json.dumps(event, sort_keys=True), flush=True)


def process_one(
    config: WorkerConfig,
    coordinator: CoordinatorClient,
    engine: RecoveryEngine,
) -> bool:
    job = coordinator.claim()
    if job is None:
        emit_metric("artifact_recovery_claim_empty", {})
        return False

    job_id = expect_string(job.get("id"), "job.id", 200)
    started = time.monotonic()
    try:
        with LeaseHeartbeat(coordinator, job_id, config.lease_seconds):
            completion = engine.run(job)
        succeeded = completion["outcome"] == "succeeded"
        coordinator.complete(
            job_id,
            succeeded=succeeded,
            result=completion,
            error=(
                None
                if succeeded
                else "artifact recovery has unresolved or incomplete evidence"
            ),
            retryable=not succeeded,
        )
        emit_metric(
            "artifact_recovery_job_completed",
            {
                "job_id_sha256": redacted_digest(job_id),
                "outcome": completion["outcome"],
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return True
    except WorkerError as exc:
        failure = {
            "schema_version": COMPLETION_SCHEMA,
            "job_id": job_id,
            "worker_id_sha256": redacted_digest(config.worker_id),
            "outcome": "failed",
            "error_class": exc.error_class,
            "retryable": exc.retryable,
            "completed_at": format_instant(utc_now()),
        }
        failure["completion_sha256"] = sha256_value(failure)
        try:
            coordinator.complete(
                job_id,
                succeeded=False,
                result=failure,
                error=str(exc),
                retryable=exc.retryable,
            )
        except WorkerError:
            pass
        emit_metric(
            "artifact_recovery_job_failed",
            {
                "job_id_sha256": redacted_digest(job_id),
                "error_class": exc.error_class,
                "retryable": exc.retryable,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        raise
