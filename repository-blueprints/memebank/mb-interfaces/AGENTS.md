# Agent instructions

This repository defines contracts; generated clients and server models consume them.

1. Edit only source contracts under `openapi/`, `schemas/`, `fixtures/`, and `compatibility/`.
2. Run `make agent-check` before proposing a change.
3. Never add credentials, access tokens, presigned URLs, private object locations, or real user data to schemas or fixtures.
4. Preserve provenance for generated metadata and preserve the distinction between native visual and text-derived embeddings.
5. Do not hand-edit generated language bindings. Regenerate them in downstream client repositories from a tagged contract release.
6. Treat required-field removals, type changes, closed-enum changes, cursor reinterpretation, and identifier reinterpretation as breaking changes requiring a new versioned surface.
7. Keep provider capabilities explicit; do not flatten unlike storage systems into an inaccurate shared API.
8. Keep instructions here minimal. Tool-specific files should point back to this document rather than duplicate policy.
