//! SeaORM adapters for the coordinator's PostgreSQL tables.
//!
//! SOURCE OF TRUTH:
//! `k8s-libs-and-shared-defs/pg-defs/schema/databases/ai_agent_coordinator/schema.sql`.
//! These entities never create or migrate schema objects at runtime.

pub mod jobs;
pub mod linear_mutations;
pub mod model_usage;
