#![forbid(unsafe_code)]

//! Exact-source non-merge certification harness for `ORESoftware/ores-cli#133` flags/flags2env hygiene.

/// Minimal adapter exposing exact flags hygiene audit modules.
pub mod audit;
/// Reuse the byte-identical report model already certified for ores-cli PR #72.
#[path = "../../ores-cli-pr72/src/model.rs"]
pub mod model;
