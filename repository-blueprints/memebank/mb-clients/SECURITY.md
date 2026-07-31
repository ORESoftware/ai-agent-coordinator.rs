# Security policy

Report vulnerabilities privately through the security-reporting channel configured on the canonical `memebank/mb-clients` repository.

Client diagnostics must redact bearer tokens, cookies, provider tokens, presigned URLs, provider file identifiers where sensitive, request/response bodies, OCR text, captions, and event payloads by default. Never attach real credentials or user images to an issue, fixture, trace, snapshot, or generated example. A leaked credential must be rotated; deleting it from the current revision is not sufficient.
