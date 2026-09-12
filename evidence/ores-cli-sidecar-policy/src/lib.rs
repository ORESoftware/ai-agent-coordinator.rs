#![forbid(unsafe_code)]

//! Exact-source non-merge certification harness for Sidecar runtime-policy linting.

/// Minimal adapter exposing the exact private Sidecar runtime-policy module.
pub mod audit;
/// Byte-identical report surface used by the private `ores-cli` implementation.
pub mod model;
