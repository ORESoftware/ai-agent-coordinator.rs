#![forbid(unsafe_code)]

//! Exact-source non-merge certification harness for `ORESoftware/ores-cli#103`.

/// Minimal adapter exposing the exact private workflow-permissions audit module.
pub mod audit;
/// Reuse the byte-identical report model already certified for ores-cli PR #72.
#[path = "../../ores-cli-pr72/src/model.rs"]
pub mod model;
