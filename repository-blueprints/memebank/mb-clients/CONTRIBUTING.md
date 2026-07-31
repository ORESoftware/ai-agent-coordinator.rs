# Contributing

1. Read `agents.md` and update source contracts before generated outputs.
2. Run `make agent-check` from a clean checkout.
3. Never place credentials, access tokens, signed URLs, provider IDs, OCR text, captions, or real user images in fixtures or logs.
4. Preserve equivalent retry, cancellation, pagination, idempotency, refresh, redaction, and error behavior across Rust, Dart, and TypeScript.
5. Treat generated files as outputs. Modify `contract/` or `scripts/generate_clients.py`, regenerate, and review the semantic diff.
6. Add a sanitized positive fixture and at least one negative test for each new behavior.
7. Describe interface compatibility, publication impact, rollout, and rollback in the pull request.
