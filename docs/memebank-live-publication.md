# MemeBank live source-v2 publication

Tracking: DEN-1004, DEN-1005, DEN-1043, DEN-319, DEN-1011, DEN-1018.

This runbook publishes the reviewed, sealed MemeBank source-v2 fleet from
`repository-fleets/memebank-source-v2.json` through the portfolio GitHub App
installed on `github.com/memebank`.

## Canonical fleet

Publication order is fixed:

1. `.github`
2. `mb-interfaces`
3. `mb-clients`
4. `mb-cli`
5. `memebank-api-server.rs`
6. `memebank-web-server.rs`
7. `memebank-media-worker.rs`
8. `memebank-flutter`
9. `mb-infra`
10. `memebank.github.io`
11. `memebank-mcp-server.rs`
12. `memebank-e2e`
13. `memebank-monorepo`

The twelve repositories other than `.github` are private. The existing public
`memebank/.github` repository is a deliberate organization-profile exception.
Its newer governance is preserved through an additive documentation PR rather
than replaced by the sealed source snapshot. `mb-infra` is canonical;
`memebank-infra` is forbidden.

## Publication transaction

The one-shot workflow performs these steps with a short-lived owner-scoped
GitHub App installation token:

1. Revalidate chunk checksums, strict base64, tar safety, exact source roots,
   deterministic Git trees and commits, tracked-entry counts, and monorepo
   gitlinks.
2. Create missing canonical repositories as private, empty repositories. Existing
   canonical repositories are accepted only when empty or when the approved
   source-v2 commit is already reachable.
3. Push the exact deterministic source commit as the first `main` commit. No
   force push, rename, deletion, or legacy-repository rewrite is permitted.
4. Verify every child source commit before publishing the monorepo last.
5. Open one provenance PR per canonical repository. Merge only when the exact
   head is mergeable and no observed check or status is failing.
6. Create or reuse the organization GitHub Project `memebank-project`, create or
   update the canonical `.github` tracking issue, and add issue/PR URLs to the
   project.
7. Upload a redacted JSON and Markdown publication ledger containing repository
   IDs, URLs, visibility, source heads, PRs, merge results, project URL, and
   failures.
8. Revoke the installation token. On complete success, remove the one-shot
   workflow from its feature branch before the reusable publisher/test/runbook
   PR is merged.

## Security and authority

The workflow uses the protected `nightly-org-maintenance` environment and the
existing portfolio GitHub App secrets. It requests only the permissions needed
for repository administration, contents/workflows, PRs, checks/statuses, issues,
and organization Projects. Tokens are written to a mode-0600 temporary file,
used through `GIT_ASKPASS`, redacted from errors, revoked after the run, and never
committed.

The PAT previously pasted into chat is not used by this workflow and must be
revoked independently.

## Idempotency and failure handling

A retry reuses exact repositories, the exact GitHub Project, and the exact
tracking issue. Existing provenance matching the same source archive, approved
head, and project is not republished. A nonempty repository that lacks the
approved source commit is a hard stop; the publisher never overwrites it.

An unmergeable or failing provenance PR remains open and makes the publication
run fail. The one-shot workflow remains on the branch for a focused correction
and rerun. Partial repository creation is recorded in the ledger and is safe to
resume.

## Post-publication evidence

After a successful run, update the MemeBank Linear project document and issues
with:

- GitHub Project number and URL;
- canonical tracking issue URL;
- all 13 repository IDs, URLs, visibility, first source heads, provenance PRs,
  and merge SHAs;
- artifact workflow/run identity and source archive SHA-256;
- any open checks or follow-up migrations from legacy `mbk-*` repositories.

Repository creation is not the same as product completion. DEN-1011 remains the
benchmark/evidence gate for OCR and vision candidates; DEN-1018 remains the
adapter implementation and conformance gate.
