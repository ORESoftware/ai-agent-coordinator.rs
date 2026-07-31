use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel)]
#[sea_orm(schema_name = "ai_agent_coordinator", table_name = "jobs")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub id: String,
    pub org: String,
    pub repo: String,
    pub task_type: String,
    pub payload: Json,
    pub priority: i64,
    pub status: String,
    pub idempotency_key: Option<String>,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
    pub available_at: DateTimeWithTimeZone,
    pub claimed_by: Option<String>,
    pub lease_expires_at: Option<DateTimeWithTimeZone>,
    pub attempts: i64,
    pub max_attempts: i64,
    pub result: Option<Json>,
    pub last_error: Option<String>,
    pub budget_usd: Option<f64>,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {}

impl ActiveModelBehavior for ActiveModel {}
