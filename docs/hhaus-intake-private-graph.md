# H/HAUS intake private dependency validation

The H/HAUS public and authenticated residency forms ultimately rely on two Rust
servers whose exact source graphs include private repositories. Repository-local
GitHub Actions cannot compile those graphs until a credential is configured, and
a broad personal access token must not be copied into each repository.

`validate-hhaus-intake-private-graph.yml` provides a narrow hosted boundary:

1. a sealed manifest names the exact API and web merge heads and destination
   branches;
2. the `nightly-org-maintenance` environment mints short-lived GitHub App tokens
   independently for `hacker-house-medellin` and `shared-auth`;
3. checkouts never persist credentials and Git URL rewrites are scoped by owner;
4. `cargo generate-lockfile` and `cargo fmt` may change only `Cargo.lock` and
   Rust source files on the already-open feature branches;
5. each exact resulting head must pass formatting, check, Clippy, tests, and a
   BuildKit-secret container build;
6. immutable commit statuses record the Rust and container outcomes.

The workflow cannot merge pull requests, publish packages or images, deploy a
service, alter repository settings, or force-push. It does not expose the App
private key or installation tokens in artifacts, source, logs, or target
repositories. A failure to mint the `shared-auth` token is treated as evidence
that the App installation/permission boundary is incomplete, not as permission
to substitute a workstation credential.

After a successful run, repository-local CI may verify the two exact status
contexts as a fail-closed alternative when no local private-dependency secret is
present. Production deployment and live form submission remain separate gates.
