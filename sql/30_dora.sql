-- =====================================================================
-- Kiro Telemetry Dashboard — DORA metrics layer
-- =====================================================================
-- Source: kiro/dora/pull_requests/snapshot.ndjson written by the
-- dora-sync Lambda (overwrite snapshot — PRs mutate, so append+dedup
-- would complicate every query; the whole 120-day snapshot is tiny).
-- Metric definitions ported from timwukp/dora-metrics-platform
-- (dora_calculator.py): lead time = first_commit -> merge (merge
-- fallback, labeled honestly), review time = created -> merged.
--
-- Placeholders: ${DATABASE} ${LOG_BUCKET} ${DORA_PREFIX}
-- =====================================================================

CREATE EXTERNAL TABLE IF NOT EXISTS ${DATABASE}.dora_pull_requests (
  repo             string,
  number           int,
  title            string,
  author           string,
  author_email     string,
  state            string,
  base_ref         string,
  created_at       string,
  merged_at        string,
  closed_at        string,
  first_commit_at  string,
  first_review_at  string,
  approved_at      string,
  review_count     int,
  approval_count   int,
  commit_count     int,
  is_revert        boolean,
  is_hotfix        boolean,
  assisted_by      string
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
WITH SERDEPROPERTIES ('ignore.malformed.json' = 'true')
LOCATION 's3://${LOG_BUCKET}/${DORA_PREFIX}pull_requests/';

CREATE OR REPLACE VIEW ${DATABASE}.v_dora_prs AS
SELECT
  repo,
  number,
  title,
  author,
  author_email,
  state,
  base_ref,
  CAST(from_iso8601_timestamp(created_at) AS timestamp)  AS created_ts,
  CAST(TRY(from_iso8601_timestamp(merged_at)) AS timestamp) AS merged_ts,
  CAST(TRY(from_iso8601_timestamp(first_commit_at)) AS timestamp) AS first_commit_ts,
  CAST(TRY(from_iso8601_timestamp(first_review_at)) AS timestamp) AS first_review_ts,
  CAST(TRY(from_iso8601_timestamp(approved_at)) AS timestamp) AS approved_ts,
  date_format(TRY(from_iso8601_timestamp(merged_at)), '%Y-%m-%d') AS merged_date,
  -- review time: PR opened -> merged (hours)
  CAST(date_diff('minute', from_iso8601_timestamp(created_at),
       TRY(from_iso8601_timestamp(merged_at))) AS DOUBLE) / 60      AS time_to_merge_hours,
  -- lead time (merge fallback): first commit -> merged (hours)
  CAST(date_diff('minute', TRY(from_iso8601_timestamp(first_commit_at)),
       TRY(from_iso8601_timestamp(merged_at))) AS DOUBLE) / 60      AS lead_time_hours,
  -- review latency: PR opened -> first review (hours)
  CAST(date_diff('minute', from_iso8601_timestamp(created_at),
       TRY(from_iso8601_timestamp(first_review_at))) AS DOUBLE) / 60 AS review_latency_hours,
  review_count,
  approval_count,
  commit_count,
  is_revert,
  is_hotfix,
  assisted_by
FROM ${DATABASE}.dora_pull_requests;

-- ---------------------------------------------------------------------
-- VIEW: v_dora_prs_attributed — AI attribution from TWO kinds of evidence
-- ---------------------------------------------------------------------
-- Co-authored-by trailers only catch tools that write one. Kiro sessions,
-- and most CLI-driven agent work, leave no trailer, so trailer-only
-- detection under-counts AI-assisted PRs and the AI-vs-unassisted merge
-- comparison reads them as "unassisted".
--
-- This view keeps the trailer verdict and adds the telemetry the dashboard
-- already holds: a PR also counts as Kiro-assisted when its author is
-- mapped to a Kiro userid (user_project.github_login) and that user sent
-- Kiro messages on a day between the PR's first commit and its merge.
--
--   ai_evidence  'trailer'         a Co-authored-by trailer named the tool
--                'kiro-telemetry'  no trailer, but the mapped author used Kiro
--                                  while the PR was in flight
--                'none'            neither (includes unmapped authors)
--   ai_tool      the trailer's tool, else 'kiro', else 'none'
--   kiro_active_days  days of Kiro use inside the PR window (0 if unmapped)
--
-- Telemetry evidence is correlation, not proof that Kiro wrote the change;
-- the dashboard labels it as such. Unmapped authors stay 'none' rather than
-- being guessed. The activity scan is bounded to the dora-sync lookback
-- (120 days) so the partition projection is pruned.
CREATE OR REPLACE VIEW ${DATABASE}.v_dora_prs_attributed AS
WITH kiro_days AS (
  SELECT lower(p.github_login) AS login, date(a."date") AS active_date
  FROM ${DATABASE}.v_user_activity a
  JOIN ${DATABASE}.user_project p ON a.userid = p.userid
  WHERE p.github_login IS NOT NULL AND p.github_login <> ''
    AND a.total_messages > 0
    AND a.dt >= date_format(date_add('day', -120, current_date), '%Y/%m/%d')
  GROUP BY 1, 2
),
pr_kiro AS (
  SELECT d.repo, d.number, COUNT(k.active_date) AS kiro_active_days
  FROM ${DATABASE}.v_dora_prs d
  LEFT JOIN kiro_days k
    ON k.login = lower(d.author)
   AND d.merged_ts IS NOT NULL
   AND k.active_date BETWEEN date(COALESCE(d.first_commit_ts, d.created_ts))
                         AND date(d.merged_ts)
  GROUP BY d.repo, d.number
)
SELECT
  d.*,
  COALESCE(k.kiro_active_days, 0) AS kiro_active_days,
  CASE WHEN d.assisted_by <> 'none' THEN 'trailer'
       WHEN COALESCE(k.kiro_active_days, 0) > 0 THEN 'kiro-telemetry'
       ELSE 'none' END AS ai_evidence,
  CASE WHEN d.assisted_by <> 'none' THEN d.assisted_by
       WHEN COALESCE(k.kiro_active_days, 0) > 0 THEN 'kiro'
       ELSE 'none' END AS ai_tool
FROM ${DATABASE}.v_dora_prs d
LEFT JOIN pr_kiro k ON k.repo = d.repo AND k.number = d.number
