use std::path::PathBuf;

use ai_agent_coordinator::{app, config::Config};
use anyhow::Context;
use clap::Parser;
use tokio::net::TcpListener;
use tracing::info;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

#[derive(Debug, Parser)]
#[command(author, version, about)]
struct Args {
    /// Path to the YAML configuration file.
    #[arg(long, env = "COORDINATOR_CONFIG", default_value = "coordinator.yaml")]
    config: PathBuf,

    /// Emit structured JSON logs.
    #[arg(long, env = "COORDINATOR_JSON_LOGS", default_value_t = false)]
    json_logs: bool,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args = Args::parse();
    init_tracing(args.json_logs);

    let config = Config::load(&args.config)?;
    let bind = config.server.bind.clone();
    let state = app::AppState::new(config)?;
    let router = app::router(state);
    let listener = TcpListener::bind(&bind)
        .await
        .with_context(|| format!("failed to bind coordinator server to {bind}"))?;

    info!(bind = %bind, "AI agent coordinator is listening");
    axum::serve(listener, router)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .context("HTTP server failed")?;
    Ok(())
}

fn init_tracing(json_logs: bool) {
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("ai_agent_coordinator=info,tower_http=info"));
    let registry = tracing_subscriber::registry().with(filter);
    if json_logs {
        registry
            .with(tracing_subscriber::fmt::layer().json())
            .init();
    } else {
        registry.with(tracing_subscriber::fmt::layer()).init();
    }
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}
