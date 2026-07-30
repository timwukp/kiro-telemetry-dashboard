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
