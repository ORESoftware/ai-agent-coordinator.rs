use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel)]
#[sea_orm(schema_name = "ai_agent_coordinator", table_name = "model_usage")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: i64,
    pub request_id: String,
    pub created_at: DateTimeWithTimeZone,
    pub org: String,
    pub repo: String,
    pub provider: String,
    pub model: String,
    pub prompt_tokens: i64,
    pub completion_tokens: i64,
    pub cost_usd: f64,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {}

impl ActiveModelBehavior for ActiveModel {}
