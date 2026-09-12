#![forbid(unsafe_code)]

//! Exact-source non-merge certification harness for `ORESoftware/ores-cli#72`.

/// Minimal adapter exposing the exact private `.ores-rl.toml` audit module.
pub mod audit;
/// Report-model copy required by the exact-source audit adapter.
pub mod model;
