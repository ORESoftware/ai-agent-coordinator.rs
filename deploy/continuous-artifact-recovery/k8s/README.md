# Continuous artifact-recovery Kubernetes contract

This directory declares the separate worker workload for the continuous
50-day reconciliation control plane. It does not modify the coordinator
Deployment or the existing cross-org Linear pilot overlay.

## Current activation state

The Deployment is deliberately committed with:

- `replicas: 0`;
- `oresoftware.dev/activation: disabled`;
- one immutable worker image:
  `ghcr.io/oresoftware/ai-agent-coordinator-artifact-recovery:sha-5af90a5f89b76a91c348f2e6d5e52cf06e3fc311`;
- one in-pod supervisor configured for exactly three lease-bound workers;
- a 1,200-hour source window and six-hour overlap;
- one `ReadWriteOnce` persistent volume for cursor, ledger, and run evidence.

Merging this source contract does **not** start the loop.

## Protected runtime bundle

Before an activation change, provision the External Secrets backend object:

```text
dd/remote-dev/ai-agent-coordinator-artifact-recovery
```

It must contain these exact runtime keys:

```text
AI_AGENT_COORDINATOR_URL
AI_AGENT_COORDINATOR_API_TOKEN
CHATGPT_RECOVERY_SOURCE_TOKEN
GITHUB_RECOVERY_SOURCE_TOKEN
LINEAR_RECOVERY_SOURCE_TOKEN
LOCAL_REPO_RECOVERY_SOURCE_TOKEN
FILE_LIBRARY_RECOVERY_SOURCE_TOKEN
sources.json
```

`CLAUDE_RECOVERY_SOURCE_TOKEN` is optional. `sources.json` must use
`artifact_recovery_sources.v1`, point only at authorized HTTPS adapters, and
make ChatGPT, GitHub, Linear, local-repository, and file-library coverage
required. Do not populate this bundle from a chat transcript or committed
plaintext.

## Activation sequence

1. Verify the published image digest and SBOM/provenance.
2. Provision the protected bundle and confirm the ExternalSecret is Ready
   without printing any value.
3. Validate every source adapter independently for authorization, complete
   pagination, fresh high-water marks, and secret-free observations.
4. Add a reviewed Argo CD Application or exact-revision pin for this directory.
5. In a separate PR, change the activation annotations to `enabled` and
   `replicas` from `0` to `1`. Never run more than one pod; the pod itself owns
   the hard maximum of three worker processes.
6. Run one unique manual canary and an identical rerun. Require zero duplicate
   Linear issues/comments and zero duplicate GitHub branches/PRs.
7. Enable the Cloudflare three-minute scheduler only after the worker canary is
   terminal and duplicate-free.

Rollback scales the Deployment to zero first. Preserve the PVC and completion
receipts; do not delete or rewrite the ledger to manufacture a clean outcome.
