#![forbid(unsafe_code)]

//! Exact-source non-merge certification harness for the DEN-2843 infra policy from `ORESoftware/ores-cli`.

/// Minimal audit surface exposing the exact production infra-policy module.
pub mod audit;
/// Minimal report model required by the exact production module.
pub mod model;
