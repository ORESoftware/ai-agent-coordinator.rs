# ORES Legal repository creation — DEN-319

This is an executable, create-only recovery of the sixteen repositories requested in
[dancing-dragons/dd-next-1#483](https://github.com/dancing-dragons/dd-next-1/issues/483).
It is not a signing product, SDK generator, compliance certification, or deployment.
The exact ordered repository list and responsibilities are in `manifest.json`.

Product delivery belongs in the [ores-legal Linear project](https://linear.app/denman/project/ores-legal-6ea9d5d4d0b7).
[Coordinator issue #230](https://github.com/ORESoftware/ai-agent-coordinator.rs/issues/230)
tracks this execution slice; DEN-319 is the shared repository-administration dependency.
The source legacy issue returned 404 through the current connection; no update to that
issue is claimed. Live receipts, not plans or these links, establish delivery.

## Run the credential-free contract

With Node.js 22, from the repository root:

```sh
node --test ores-legal-bootstrap/bootstrap.test.mjs
node ores-legal-bootstrap/run.mjs
```

The runner defaults to a plan and accepts no command-line arguments. It requires no
package installation and reads no dotenv, credential files, or shell history.
The tests use in-memory GitHub fixtures and cover all sixteen names, identity drift,
private visibility, sealed-manifest changes, exact execution gates, redirects,
authorization failures, ambiguous create recovery, issue deduplication, bounded
pagination, partial receipts, redaction, and replay without duplicate writes.
They are contract tests, not proof of live repository creation.

## Protected creation path

`.github/workflows/create-ores-legal-fleet.yml` validates pull requests without
credentials or side effects. Live creation is limited to a push on this repository's
`main`, after validation, through the existing `nightly-org-maintenance` environment.
Normal review, branch checks, and environment approvals remain in force. Do not
bypass a failed or pending gate to activate this workflow.

The environment must have `PORTFOLIO_GITHUB_APP_ID` and
`PORTFOLIO_GITHUB_APP_PRIVATE_KEY`, with an installation on `ores-legal` authorized
for repository administration, contents, issues, and metadata. Credentials are
minted by the pinned GitHub App action for this exact organization. There is no
PAT fallback, token relay, pull-request-target job, or arbitrary owner input.
Missing configuration is a blocker, not success. The runner rechecks the source
repository, push event, main ref, full commit SHA, explicit enablement, and exact
`ores-legal@<manifest-sha256>` confirmation before constructing its API client.

Creation is private by default, including the public-SDK source repository, until
its publication and license policy is explicitly reviewed. The workflow does not
change any existing repository's visibility or configuration. New repositories get
GitHub's ordinary initial README commit and a repository-local implementation issue.
Existing matching issues are preserved. Existing repositories must already match
exact owner, name, private visibility, non-archived state, and `main` default branch.
Any mismatch is reported for review rather than "repaired" destructively.

Only exact allowlisted GitHub API reads, organization repository creation, and
repository issue creation are permitted. No branch overwrites, force pushes,
repository deletion, PR merges, production deployments, or cloud database creation
are implemented here. Ambiguous POST outcomes are reconciled by reads; they are
never blindly repeated. One fixed Actions concurrency group prevents competing
runs of this publisher.

## Evidence and completion

A successful item has the verified organization/repository identity, repository ID,
canonical URL, readable `main` commit, and canonical roadmap issue URL. Checkpoints
are atomically written after each item and immediately after confirmed creation.
The workflow uploads only the non-secret receipt JSON, including source commit and
manifest digest. A partially created repository remains visible in its receipt even
if subsequent checks or issue creation fail. Errors include allowlisted codes and
HTTP statuses, never raw response or exception bodies.

A run is `complete` only when all sixteen items are verified. Unavailable credentials,
unauthorized requests, visibility drift, unreadable initial commits, and issue failures
produce `blocked` and a nonzero exit. The receipt must be reconciled back to issue #230
and the product project; do not check off an item before its live URL/ID/commit resolves.
GitHub Project membership is not updated by this publisher and requires a separately
verified Projects API path.

## Product work after creation

Repository-local acceptance issues deliberately remain open. Implement reusable
signature/initial/template/version/consent workflows with authorization and immutable
evidence. Keep TypeSpec and JSON Schema independent peer authorities; prove 15+
language projections with executable conformance instead of counting empty folders.
Keep internal ORM/admin operations separate from the external SDK. Use the existing
Shared Auth, ORES middleware, rate limits, telemetry, flags-2-env, and encrypted
configuration contracts where relevant.

`ores-legal-monorepo/apps/` must contain actual gitlinks to published application
commits, never fabricated pins or nested copies; infrastructure stays outside apps/.
`ores-legal-infra` needs separate top-level `supabase/` and `neon/` implementations
using reviewed tenant-isolated configuration and the canonical shared-Supabase policy.
No signing ceremony or customer storage path is certified by repository bootstrap.
Rust/Flutter UI parity, provider-neutral workers, signature rendering/storage, adversarial
acceptance in `ores-legal-test`, and comparisons with 5–10 signing platforms are
subsequent tested PRs, not completed features of this slice.
