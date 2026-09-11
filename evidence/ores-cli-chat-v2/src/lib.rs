#![forbid(unsafe_code)]

//! Exact-source non-merge certification harness for the ORES Chat repository audit.

/// Minimal adapter exposing the exact private ORES Chat repository-audit module.
pub mod audit;
/// Byte-identical report surface used by the private `ores-cli` implementation.
pub mod model;
