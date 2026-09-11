FROM python:3.12-slim-bookworm

RUN useradd --create-home --uid 10001 artifact-worker
WORKDIR /app

COPY tools/artifact_recovery /app/tools/artifact_recovery
COPY tools/artifact_recovery_worker_runtime /app/tools/artifact_recovery_worker_runtime
COPY tools/run_continuous_artifact_recovery_worker.py /app/tools/run_continuous_artifact_recovery_worker.py
COPY tools/run_continuous_artifact_recovery_pool.py /app/tools/run_continuous_artifact_recovery_pool.py

RUN python3 -m py_compile \
      /app/tools/artifact_recovery/*.py \
      /app/tools/artifact_recovery_worker_runtime/*.py \
      /app/tools/run_continuous_artifact_recovery_worker.py \
      /app/tools/run_continuous_artifact_recovery_pool.py \
    && mkdir -p /var/lib/artifact-recovery \
    && chown -R artifact-worker:artifact-worker /app /var/lib/artifact-recovery

USER artifact-worker
ENV ARTIFACT_RECOVERY_WORKER_COUNT=3 \
    ARTIFACT_RECOVERY_WINDOW_HOURS=1200 \
    ARTIFACT_RECOVERY_OVERLAP_HOURS=6 \
    ARTIFACT_RECOVERY_STATE_DIR=/var/lib/artifact-recovery \
    ARTIFACT_RECOVERY_SOURCE_MANIFEST=/etc/artifact-recovery/sources.json

ENTRYPOINT ["python3", "/app/tools/run_continuous_artifact_recovery_pool.py"]
