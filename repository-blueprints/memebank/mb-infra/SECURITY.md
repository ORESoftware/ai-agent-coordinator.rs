# Security policy

Report suspected vulnerabilities privately through the security-reporting channel configured on the canonical `memebank/mb-infra` repository. Do not include credentials, user images, OCR text, provider payloads, signed URLs, or exploit details in a public issue.

Infrastructure changes must preserve least privilege, default-deny network policy, immutable artifact references, secret-manager references, non-root workloads, and auditable Git promotion. A secret committed to Git must be considered compromised and rotated; removing it from the latest revision is not sufficient.
