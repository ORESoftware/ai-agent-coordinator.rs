#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one replacement target, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/app.rs",
    '''        .route("/healthz", get(health))
        .route("/readyz", get(ready))
        .route("/v1/models", get(list_models))''',
    '''        .route("/healthz", get(health))
        .route("/readyz", get(ready))
        .route(
            crate::agent_pontifex_discovery::DISCOVERY_PATH,
            get(agent_pontifex_descriptor),
        )
        .route("/v1/models", get(list_models))''',
)
replace_once(
    "src/app.rs",
    '''async fn health() -> Json<Value> {
    Json(json!({"status": "ok"}))
}
''',
    '''async fn agent_pontifex_descriptor(
) -> Json<crate::agent_pontifex_discovery::ServiceDescriptor> {
    Json(crate::agent_pontifex_discovery::coordinator_descriptor())
}

async fn health() -> Json<Value> {
    Json(json!({"status": "ok"}))
}
''',
)

replace_once(
    "README.md",
    '''- OpenAI-compatible `POST /v1/chat/completions`
''',
    '''- Credential-free Agent Pontifex discovery at `GET /.well-known/agent-pontifex`
- OpenAI-compatible `POST /v1/chat/completions`
''',
)
replace_once(
    "README.md",
    '''curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
```

## Model gateway
''',
    '''curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/.well-known/agent-pontifex
```

## Agent Pontifex compatibility

The well-known endpoint advertises only the vendor-neutral leased-job contract:
create, claim, heartbeat, complete, cancel, retry, idempotency, and bounded leases.
It is intentionally public-safe and contains no credentials, tenant identifiers,
provider routes, budgets, GitHub administration, Linear delivery, Slack payloads,
or deployment topology.

The descriptor binds the canonical `coordinator` service to the
`agent-pontifex.coordinator` protocol and an explicit supported major-version
range. Agent Pontifex SDK clients fail closed when the service role, protocol, or
version range is incompatible. Product-specific behavior must remain in a
namespaced extension; this community descriptor currently advertises none.

After the shared protocol crate moves to `agent-pontifex/agent-sdk.rs`, this local
compatibility module should consume that crate rather than becoming an
independent protocol authority.

## Model gateway
''',
)
