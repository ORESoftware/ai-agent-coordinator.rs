pub mod agent_pontifex;
pub mod agent_pontifex_discovery;
pub mod app;
pub mod config;
/// Storage-independent fenced delivery contract; persistence adapters must preserve its invariants.
pub mod daily_portfolio_delivery;
/// PostgreSQL-backed fenced delivery repository for durable coordinator execution.
pub mod daily_portfolio_delivery_store;
pub mod db;
pub mod email_attention;
pub mod entity;
pub mod error;
pub mod gateway;
pub mod github_admin;
pub mod jobs;
pub mod linear_delivery_worker;
pub mod linear_delivery {
    pub use crate::linear_delivery_worker::*;
}
pub mod prompt_intake;
pub mod prompt_reconciliation;
pub mod prompt_reconciliation_adapters;
pub mod providers;
pub mod security;
pub mod slack_run;
pub mod telemetry;
pub mod webhooks;
