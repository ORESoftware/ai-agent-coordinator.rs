use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel)]
#[sea_orm(schema_name = "ai_agent_coordinator", table_name = "linear_mutations")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub mutation_key: String,
    pub job_id: String,
    pub organization: String,
    pub repository: String,
    pub issue_identifier: String,
    pub commit_id: String,
    pub keyword: String,
    pub action: String,
    pub status: String,
    pub attempts: i64,
    pub last_error: Option<String>,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {}

impl ActiveModelBehavior for ActiveModel {}
