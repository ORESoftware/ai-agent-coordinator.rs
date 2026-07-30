# AI Agent Coordinator deployment

These manifests are namespace-scoped application resources for the `ai-agent-coordinator` tenant. The platform repository must create the namespace, service account, quota, limit range, default-deny network policy, AppProject, and Argo CD Application before syncing this directory.

## Safety defaults

- The image is pinned to immutable commit tag `sha-d737ce98fbb6f7c82fae598e8938050685633e05`.
- Repository creation is disabled with `GITHUB_REPOSITORY_ADMIN_ENABLED=false`.
- The allowed-organization list is initially restricted to `declarative-migrations`.
- The container runs as UID/GID 10001, drops all capabilities, forbids privilege escalation, uses `RuntimeDefault` seccomp, and has a read-only root filesystem.
- Durable state lives in the shared PostgreSQL service through SeaORM; the
  Deployment uses rolling updates and two replicas without a writable volume.
- Database DDL is owned by
  `k8s-libs-and-shared-defs/pg-defs/schema/databases/ai_agent_coordinator/schema.sql`
  and is converged with dpm before rollout. The application never migrates at boot.
- dpm manages schema only. If the retired SQLite PVC contains production
  records, snapshot it and complete a separately reviewed one-time data
  backfill before this rollout; retain the snapshot until PostgreSQL counts and
  queue state have been reconciled.
- No plaintext Kubernetes `Secret` is committed.
- Alertmanager delivery runs every minute, and bounded remediation dispatch runs
  at 04:00 in the `America/New_York` timezone.

## Required external secret

Create this property in the cluster secret backend before syncing:

- remote key: `dd/remote-dev/ai-agent-coordinator-secrets`
- properties: `COORDINATOR_API_TOKEN`, `AI_AGENT_COORDINATOR_DATABASE_URL`

The `ExternalSecret` materializes it as `ai-agent-coordinator-core`.

Provider credentials may later be supplied through an optional `ai-agent-coordinator-providers` Secret. The service can start without them; unavailable providers are disabled.

Telemetry automation additionally requires the AWS Secrets Manager JSON object
`dd/remote-dev/telemetry-ticket-automation`, documented in
[`../../docs/telemetry-ticket-automation.md`](../../docs/telemetry-ticket-automation.md).
The ExternalSecret is intentionally required because the checked-in deployment
enables live GitHub and Linear delivery. Provision and verify that bundle before
syncing this revision.

## Repository-administration activation

Do not store a long-lived personal access token. Supply a short-lived GitHub App installation token through an `ai-agent-coordinator-admin` Secret with key `GITHUB_REPOSITORY_ADMIN_TOKEN`.

Activation is a separate reviewed change:

1. create or rotate `ai-agent-coordinator-admin` through External Secrets;
2. verify the token is scoped to the required organization and has only repository Administration write permission;
3. change `GITHUB_REPOSITORY_ADMIN_ENABLED` to `true` in a feature-branch pull request;
4. perform an authenticated dry run for `declarative-migrations/declarative-migrations-monorepo`;
5. submit the live request with exact confirmation `declarative-migrations/declarative-migrations-monorepo`;
6. return the deployment to disabled mode after the bootstrap batch unless continued administration is explicitly required.

All repository implementation after bootstrap must occur on feature branches and through pull requests.
