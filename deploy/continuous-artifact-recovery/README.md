# Continuous artifact-recovery worker image

This image runs the existing lease-bound artifact-recovery worker through a
supervisor capped at three child processes. The workers share a durable state
volume and use a filesystem fence around the current cursor/ledger transaction.

Required runtime configuration:

- `AI_AGENT_COORDINATOR_URL`
- `AI_AGENT_COORDINATOR_API_TOKEN`
- `ARTIFACT_RECOVERY_SOURCE_MANIFEST`
- every source-token environment variable named by that protected manifest

The default window is 1,200 hours with six hours of overlap. The image contains
no source endpoint, token, customer submission, or chat body.

The main-branch workflow publishes
`ghcr.io/oresoftware/ai-agent-coordinator-artifact-recovery:sha-<commit>`. A
separate `ORESoftware/k8s-cluster` pull request must pin that immutable image,
mount the protected manifest and credentials, attach persistent storage, and
prove one live canary plus an identical duplicate-free rerun before activation.
