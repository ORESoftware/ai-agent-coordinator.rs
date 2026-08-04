# Organization `.github` repository relationship rollout

Tracking: DEN-629.

This change finishes the organization-context rollout by making every mapped GitHub organization own a public `.github` repository and by adding deterministic, machine-readable repository relationship declarations to each generated organization bundle.

The rollout is fail-closed:

- only organizations with a reviewed immutable GitHub owner mapping are eligible;
- installed but unmapped organizations remain excluded until their product identity and Linear project are reviewed;
- missing `.github` repositories are created through the isolated, secret-backed, one-shot Kubernetes bootstrap rather than through source-embedded credentials;
- relationship declarations are generated from the reviewed central registry and verified byte-for-byte against an immutable source commit;
- repository-local implementation instructions continue to take precedence over organization-level context;
- public mirrors contain only explicitly approved repository identities and public operating metadata.

The final PR will include the complete schema, renderer, verifier, mutation tests, browser checks, one-shot bootstrap contract, exact organization inventory, and rollout evidence.
