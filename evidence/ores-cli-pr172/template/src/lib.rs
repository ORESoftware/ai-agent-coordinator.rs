#![forbid(unsafe_code)]

//! Exact-module non-merge certification harness for `ORESoftware/ores-cli#172`.

/// Minimal adapter exposing the reconstructed exact OTel repository-audit module.
pub mod audit;
/// Minimal copy of the stable report model needed by the audit module.
pub mod model;
