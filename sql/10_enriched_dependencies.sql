-- =====================================================================
-- Kiro Telemetry Dashboard — Athena dependencies for the Cost tab
-- =====================================================================
-- The governance repo (kiro-telemetry-governance) owns the base tables
-- and views. This file only reconciles what the LIVE account is missing
-- for v_user_activity_enriched:
--
--   live account today: tables prompt_logs / user_activity / user_mapping,
--                       views v_prompt_logs / v_user_activity
--   missing:            user_project table, v_user_activity_enriched view
--
-- The live identity table is named `user_mapping` (written daily by the
-- kiro-user-mapping-sync Lambda to kiro/user-mapping/user_mapping.csv),
-- not `user_identity` as in the repo. ${IDENTITY_TABLE} is substituted by
-- deploy.sh after it inspects the Glue catalog, so this file works on both
-- a hand-built account and a repo-deployed one.
--
-- Placeholders (deploy.sh, envsubst style):
--   ${DATABASE}        e.g. kiro_governance
--   ${LOG_BUCKET}      e.g. amazon-q-logging-<account>
--   ${MAPPING_PREFIX}  e.g. kiro/mappings
--   ${IDENTITY_TABLE}  user_mapping | user_identity
-- =====================================================================

-- ---------------------------------------------------------------------
-- user_project — manually maintained cost-allocation mapping (FinOps).
-- Upload mappings/user-project.csv to the LOCATION below.
-- ---------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS ${DATABASE}.user_project (
  userid       string,
  team         string,
  project      string,
  cost_center  string
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('separatorChar' = ',', 'quoteChar' = '"')
LOCATION 's3://${LOG_BUCKET}/${MAPPING_PREFIX}/user-project/'
TBLPROPERTIES ('skip.header.line.count' = '1');

-- ---------------------------------------------------------------------
-- v_user_activity_enriched — activity + identity + org mapping.
-- LEFT JOINs so unmapped users still appear (team/project = 'UNMAPPED').
-- Mirrors the governance repo's sql/02_create_views.sql definition, with
-- the identity table name parameterized.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW ${DATABASE}.v_user_activity_enriched AS
SELECT
  a.dt,
  a."date" AS activity_date,
  a.userid,
  COALESCE(i.username, a.userid)        AS username,
  COALESCE(i.display_name, 'Unknown')   AS display_name,
  COALESCE(p.team, 'UNMAPPED')          AS team,
  COALESCE(p.project, 'UNMAPPED')       AS project,
  COALESCE(p.cost_center, 'UNMAPPED')   AS cost_center,
  a.client_type,
  a.subscription_tier,
  a.chat_conversations,
  a.total_messages,
  a.credits_used,
  a.overage_cap,
  a.overage_credits_used,
  a.overage_enabled
FROM ${DATABASE}.v_user_activity a
-- Key formats differ between sources: activity reports use the bare userId
-- ("96672d6eb2-e761…"), while the identity CSV keys are
-- "<identityStoreId>.<userId>" ("d-1234567890.abcd1234-…").
-- Join on the userId part after the dot, falling back to exact match.
LEFT JOIN ${DATABASE}.${IDENTITY_TABLE} i
  ON a.userid = COALESCE(element_at(split(i.kiro_userid, '.'), 2), i.kiro_userid)
LEFT JOIN ${DATABASE}.user_project  p ON a.userid = p.userid;
