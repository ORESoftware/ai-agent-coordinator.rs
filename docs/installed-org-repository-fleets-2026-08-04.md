# Installed-organization repository fleet plans — August 4, 2026

This change converts the newest content-free Google Chat reconciliation into deterministic repository-administration inputs. It does not create repositories by itself and does not contain a credential.

## Scope

The connected GitHub App reports installations for `apostille-me`, `evento-globolo`, `embedded-alerts`, and `hacker-house-medellin`. At audit time, the connector exposed zero repositories for each owner. That observation is evidence of current access/state, not a claim that a repository can never exist outside the connector's view.

The checked-in manifests cover:

| Organization | Tracking issue | Planned repositories | State |
|---|---:|---:|---|
| `apostille-me` | `DEN-1951` | 5 | public dry-run requests ready; live disabled |
| `evento-globolo` | `DEN-1889` | 12 | public dry-run requests ready; live disabled |
| `embedded-alerts` | `DEN-1949` | 12 | public dry-run requests ready; live disabled |
| `hacker-house-medellin` | `DEN-1950` | 12 | public dry-run requests ready; live disabled |
| `liberty-cal` | `DEN-1948` | 1 foundation repository | organization/App installation and visibility review required |

The four installed organizations total **41** planned repositories. Every orchestration monorepo is last, `.github` remains deferred, and every infra repository explicitly limits its Cloudflare Worker scope rather than introducing an unrestricted edge proxy.

## Safety boundary

`scripts/render_repository_fleet.py` performs no network I/O and reads no credentials. The manifests keep `live_creation_enabled` false. They can render bounded dry-run request bodies for the coordinator's guarded `POST /v1/github/repositories` endpoint, but they cannot render a live request until a reviewed commit deliberately flips the switch.

The deployed coordinator must still require all of the following for each repository:

- authenticated coordinator access;
- `GITHUB_REPOSITORY_ADMIN_ENABLED=true`;
- an exact `GITHUB_REPOSITORY_ADMIN_ALLOWED_ORGS` entry;
- a short-lived GitHub App installation token in `GITHUB_REPOSITORY_ADMIN_TOKEN`;
- `dry_run=false`;
- one repository selected at a time; and
- `confirm_repository` exactly equal to `<organization>/<repository>`.

Personal access tokens, pasted OAuth values, and credentials in arguments, manifests, reports, issues, or logs are prohibited.

## Review commands

```bash
python -m unittest -v scripts/test_installed_org_repository_fleets.py

python scripts/render_repository_fleet.py \
  --manifest repository-fleets/embedded-alerts.json \
  --mode plan

python scripts/render_repository_fleet.py \
  --manifest repository-fleets/embedded-alerts.json \
  --mode dry-run
```

## Remaining publication work

A reviewed manifest is not remote delivery. Before any tracking issue is completed, verify the installation permission and organization allowlist, run and archive the coordinator dry-run, enable and confirm one repository at a time, initialize nonempty source, push a Linear-linked branch, open a focused repository-local PR, run checks, merge intentionally, and verify the default branch, visibility, source digest, PR state, and merge commit directly from GitHub.

`liberty-cal` remains externally blocked until the organization exists and the connected GitHub App is installed. Its manifest deliberately retains unresolved visibility so even dry-run request rendering fails closed.
