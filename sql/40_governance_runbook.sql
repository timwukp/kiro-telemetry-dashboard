-- =====================================================================
-- Kiro Telemetry Dashboard — governance runbook queries
-- =====================================================================
-- Ad-hoc investigation queries for admins, run manually in the Athena
-- console (workgroup ${WORKGROUP}). These deliberately live OUTSIDE the
-- dashboard API: they answer one-off governance questions (license
-- reviews, incident investigation) rather than powering charts.
--
-- Ported from kiro-telemetry-governance/sql/03_governance_queries.sql and
-- rewritten against the LIVE view schemas (v_user_activity exposes the
-- day column as "date", not activity_date; only v_user_activity_enriched
-- has activity_date).
--
-- Placeholders (envsubst style, same as the other sql/ files):
--   ${DATABASE}   e.g. kiro_governance
-- Literal placeholders you edit per run are written <LIKE_THIS>.
-- =====================================================================

-- ---------------------------------------------------------------------
-- R1. Inactive users — no activity in the last 7 days (license reviews).
-- ---------------------------------------------------------------------
SELECT DISTINCT userid
FROM ${DATABASE}.v_user_activity
WHERE userid NOT IN (
  SELECT DISTINCT userid
  FROM ${DATABASE}.v_user_activity
  WHERE "date" >= date_format(date_add('day', -7, current_date), '%Y-%m-%d')
);

-- ---------------------------------------------------------------------
-- R2. Full interaction history for one user (incident investigation).
--     Replace <TARGET_USER_ID> (or filter on username instead).
-- ---------------------------------------------------------------------
SELECT ts, prompt_text, response_length, keyword_triggered
FROM ${DATABASE}.v_prompt_logs
WHERE user_id = '<TARGET_USER_ID>'
ORDER BY ts DESC;

-- ---------------------------------------------------------------------
-- R3. Volume anomalies — users >3x their own average daily messages.
-- ---------------------------------------------------------------------
WITH user_avg AS (
  SELECT userid, "date" AS activity_date, total_messages,
         AVG(total_messages) OVER (PARTITION BY userid) AS avg_messages
  FROM ${DATABASE}.v_user_activity
)
SELECT userid, activity_date, total_messages, avg_messages,
       ROUND(CAST(total_messages AS DOUBLE) / NULLIF(avg_messages, 0), 2) AS ratio
FROM user_avg
WHERE total_messages > avg_messages * 3 AND avg_messages > 0
ORDER BY ratio DESC;

-- ---------------------------------------------------------------------
-- R4. License utilization — active in last 30d vs seats you pay for.
--     Replace <TOTAL_LICENSES> with your seat count (e.g. 50).
-- ---------------------------------------------------------------------
SELECT COUNT(DISTINCT userid)  AS active_users,
       <TOTAL_LICENSES>        AS total_licenses,
       ROUND(CAST(COUNT(DISTINCT userid) AS DOUBLE) / <TOTAL_LICENSES> * 100, 1) AS utilization_pct
FROM ${DATABASE}.v_user_activity
WHERE "date" >= date_format(date_add('day', -30, current_date), '%Y-%m-%d');

-- ---------------------------------------------------------------------
-- R5. Project adoption rate — active last 30d / assigned in user_project.
-- ---------------------------------------------------------------------
WITH project_active AS (
  SELECT project, COUNT(DISTINCT userid) AS active_users
  FROM ${DATABASE}.v_user_activity_enriched
  WHERE activity_date >= date_format(date_add('day', -30, current_date), '%Y-%m-%d')
  GROUP BY project
),
project_total AS (
  SELECT project, COUNT(DISTINCT userid) AS total_assigned
  FROM ${DATABASE}.user_project
  GROUP BY project
)
SELECT t.project, t.total_assigned,
       COALESCE(a.active_users, 0) AS active_users,
       ROUND(CAST(COALESCE(a.active_users, 0) AS DOUBLE) / NULLIF(t.total_assigned, 0) * 100, 1) AS adoption_pct
FROM project_total t
LEFT JOIN project_active a ON t.project = a.project
ORDER BY adoption_pct DESC;
