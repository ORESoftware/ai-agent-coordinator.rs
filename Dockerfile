FROM rust:1.97-bookworm AS builder
WORKDIR /workspace
COPY Cargo.toml Cargo.toml
COPY src src
RUN cargo build --release

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 coordinator
WORKDIR /app
COPY --from=builder /workspace/target/release/ai-agent-coordinator /usr/local/bin/ai-agent-coordinator
COPY coordinator.example.yaml /app/coordinator.yaml
RUN mkdir -p /app/data && chown -R coordinator:coordinator /app
USER coordinator
EXPOSE 8080
ENTRYPOINT ["ai-agent-coordinator"]
CMD ["--config", "/app/coordinator.yaml", "--json-logs"]
