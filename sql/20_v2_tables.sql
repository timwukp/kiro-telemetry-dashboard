-- =====================================================================
-- Kiro Telemetry Dashboard v2 — data-layer upgrade
-- =====================================================================
-- Verified against LIVE S3 files on 2026-07-30:
--   * by_user_analytic CSVs: 46 columns, Date format MM-dd-yyyy
--   * user_report CSVs: 14 columns (the governance repo's table only
--     mapped the first 11 — New_User, User_Email, auto_messages were
--     silently dropped by OpenCSVSerde positional mapping)
--   * prompt log records carry modelId (e.g. "auto")
--
-- Placeholders (deploy.sh, envsubst style):
--   ${DATABASE} ${LOG_BUCKET} ${ACCOUNT_ID} ${REGION}
--   ${ACTIVITY_PREFIX}  e.g. kiro/user-activity-metrics
--   ${PROMPT_PREFIX}    e.g. kiro/prompt-log
--   ${PROJECTION_START} e.g. 2025/01/01/00
--   ${IDENTITY_TABLE}   user_mapping | user_identity
-- =====================================================================

-- ---------------------------------------------------------------------
-- TABLE: by_user_analytic — per-user developer-productivity metrics.
-- All 46 real columns kept (most are zero in this account today; charts
-- only use the verified-non-zero ones, but the schema is future-proof).
-- ---------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS ${DATABASE}.by_user_analytic (
  userid                                  string,
  `date`                                  string,
  chat_aicodelines                        string,
  chat_messagesinteracted                 string,
  chat_messagessent                       string,
  codefix_acceptanceeventcount            string,
  codefix_acceptedlines                   string,
  codefix_generatedlines                  string,
  codefix_generationeventcount            string,
  codereview_failedeventcount             string,
  codereview_findingscount                string,
  codereview_succeededeventcount          string,
  dev_acceptanceeventcount                string,
  dev_acceptedlines                       string,
  dev_generatedlines                      string,
  dev_generationeventcount                string,
  docgeneration_acceptedfileupdates       string,
  docgeneration_acceptedfilescreations    string,
  docgeneration_acceptedlineadditions     string,
  docgeneration_acceptedlineupdates       string,
  docgeneration_eventcount                string,
  docgeneration_rejectedfilecreations     string,
  docgeneration_rejectedfileupdates       string,
  docgeneration_rejectedlineadditions     string,
  docgeneration_rejectedlineupdates       string,
  inlinechat_acceptanceeventcount         string,
  inlinechat_acceptedlineadditions        string,
  inlinechat_acceptedlinedeletions        string,
  inlinechat_dismissaleventcount          string,
  inlinechat_dismissedlineadditions       string,
  inlinechat_dismissedlinedeletions       string,
  inlinechat_rejectedlineadditions        string,
  inlinechat_rejectedlinedeletions        string,
  inlinechat_rejectioneventcount          string,
  inlinechat_totaleventcount              string,
  inline_aicodelines                      string,
  inline_acceptancecount                  string,
  inline_suggestionscount                 string,
  testgeneration_acceptedlines            string,
  testgeneration_acceptedtests            string,
  testgeneration_eventcount               string,
  testgeneration_generatedlines           string,
  testgeneration_generatedtests           string,
  transformation_eventcount               string,
  transformation_linesgenerated           string,
  transformation_linesingested            string
)
PARTITIONED BY (dt string)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('separatorChar' = ',', 'quoteChar' = '"', 'escapeChar' = '\\')
LOCATION 's3://${LOG_BUCKET}/${ACTIVITY_PREFIX}/AWSLogs/${ACCOUNT_ID}/KiroLogs/by_user_analytic/${REGION}/'
TBLPROPERTIES (
  'has_encrypted_data'          = 'false',
  'skip.header.line.count'      = '1',
  'projection.enabled'          = 'true',
  'projection.dt.type'          = 'date',
  'projection.dt.format'        = 'yyyy/MM/dd/HH',
  'projection.dt.range'         = '${PROJECTION_START},NOW',
  'projection.dt.interval'      = '1',
  'projection.dt.interval.unit' = 'HOURS',
  'storage.location.template'   =
    's3://${LOG_BUCKET}/${ACTIVITY_PREFIX}/AWSLogs/${ACCOUNT_ID}/KiroLogs/by_user_analytic/${REGION}/${dt}'
);

-- ---------------------------------------------------------------------
-- TABLE: user_activity — REDEFINED with the 3 real columns the original
-- 11-column definition dropped (New_User, User_Email, auto_messages).
-- OpenCSVSerde maps by position; older files simply yield NULL/empty for
-- trailing columns, so this is backward-safe. DROP+CREATE (external
-- table: metadata only, no data touched).
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ${DATABASE}.user_activity;

CREATE EXTERNAL TABLE ${DATABASE}.user_activity (
  `date`                 string,
  userid                 string,
  client_type            string,
  chat_conversations     string,
  credits_used           string,
  overage_cap            string,
  overage_credits_used   string,
  overage_enabled        string,
  profileid              string,
  subscription_tier      string,
  total_messages         string,
  new_user               string,
  user_email             string,
  auto_messages          string
)
PARTITIONED BY (dt string)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('separatorChar' = ',', 'quoteChar' = '"', 'escapeChar' = '\\')
LOCATION 's3://${LOG_BUCKET}/${ACTIVITY_PREFIX}/AWSLogs/${ACCOUNT_ID}/KiroLogs/user_report/${REGION}/'
TBLPROPERTIES (
  'has_encrypted_data'          = 'false',
  'skip.header.line.count'      = '1',
  'projection.enabled'          = 'true',
  'projection.dt.type'          = 'date',
  'projection.dt.format'        = 'yyyy/MM/dd/HH',
  'projection.dt.range'         = '${PROJECTION_START},NOW',
  'projection.dt.interval'      = '1',
  'projection.dt.interval.unit' = 'HOURS',
  'storage.location.template'   =
    's3://${LOG_BUCKET}/${ACTIVITY_PREFIX}/AWSLogs/${ACCOUNT_ID}/KiroLogs/user_report/${REGION}/${dt}'
);

-- ---------------------------------------------------------------------
-- TABLE: prompt_logs — REDEFINED with modelId in the request struct.
-- JsonSerDe returns NULL for absent keys, so older objects are safe.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS ${DATABASE}.prompt_logs;

CREATE EXTERNAL TABLE ${DATABASE}.prompt_logs (
  records array<struct<
    generateAssistantResponseEventRequest: struct<
      prompt: string,
      chatTriggerType: string,
      customizationArn: string,
      userId: string,
      timeStamp: string,
      modelId: string
    >,
    generateAssistantResponseEventResponse: struct<
      assistantResponse: string,
      followupPrompts: string,
      messageMetadata: struct<
        conversationId: string,
        utteranceId: string
      >,
      codeReferenceEvents: array<string>,
      supplementaryWebLinksEvent: array<struct<
        uri: string,
        title: string,
        snippet: string
      >>,
      requestId: string
    >,
    generateCompletionsEventRequest: struct<
      leftContext: string,
      rightContext: string,
      fileName: string,
      customizationArn: string,
      userId: string,
      timeStamp: string
    >,
    generateCompletionsEventResponse: struct<
      completions: array<string>,
      requestId: string
    >
  >>
)
PARTITIONED BY (dt string)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
WITH SERDEPROPERTIES ('ignore.malformed.json' = 'true')
LOCATION 's3://${LOG_BUCKET}/${PROMPT_PREFIX}/AWSLogs/${ACCOUNT_ID}/KiroLogs/GenerateAssistantResponse/${REGION}/'
TBLPROPERTIES (
  'has_encrypted_data'                = 'false',
  'compressionType'                   = 'gzip',
  'projection.enabled'                = 'true',
  'projection.dt.type'                = 'date',
  'projection.dt.format'              = 'yyyy/MM/dd/HH',
  'projection.dt.range'               = '${PROJECTION_START},NOW',
  'projection.dt.interval'            = '1',
  'projection.dt.interval.unit'       = 'HOURS',
  'storage.location.template'         =
    's3://${LOG_BUCKET}/${PROMPT_PREFIX}/AWSLogs/${ACCOUNT_ID}/KiroLogs/GenerateAssistantResponse/${REGION}/${dt}'
);

-- ---------------------------------------------------------------------
-- VIEW: v_user_activity — retyped, now exposing the 3 new columns.
-- Keeps the same column names/casts the dashboard already queries.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW ${DATABASE}.v_user_activity AS
SELECT
  dt,
  "date",
  userid,
  client_type,
  CAST(chat_conversations   AS BIGINT)            AS chat_conversations,
  CAST(credits_used         AS DOUBLE)            AS credits_used,
  CAST(overage_cap          AS DOUBLE)            AS overage_cap,
  CAST(overage_credits_used AS DOUBLE)            AS overage_credits_used,
  overage_enabled,
  profileid,
  subscription_tier,
  CAST(total_messages       AS BIGINT)            AS total_messages,
  LOWER(COALESCE(new_user, ''))                   AS new_user,
  NULLIF(user_email, '')                          AS user_email,
  TRY_CAST(auto_messages AS BIGINT)               AS auto_messages
FROM ${DATABASE}.user_activity;

-- ---------------------------------------------------------------------
-- VIEW: v_prompt_logs — same as governance repo's, plus model_id.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW ${DATABASE}.v_prompt_logs AS
SELECT
  dt,
  r.generateAssistantResponseEventRequest.userId                       AS user_id,
  COALESCE(m.username, r.generateAssistantResponseEventRequest.userId) AS username,
  COALESCE(m.display_name, 'Unknown')                                  AS display_name,
  r.generateAssistantResponseEventRequest.timeStamp                    AS ts,
  r.generateAssistantResponseEventRequest.chatTriggerType              AS trigger_type,
  COALESCE(r.generateAssistantResponseEventRequest.modelId, 'unknown') AS model_id,
  substr(r.generateAssistantResponseEventRequest.prompt, 1, 500)       AS prompt_text,
  length(r.generateAssistantResponseEventRequest.prompt)               AS prompt_length,
  length(r.generateAssistantResponseEventResponse.assistantResponse)   AS response_length,
  CASE WHEN length(r.generateAssistantResponseEventRequest.prompt) > 0
       THEN CAST(length(r.generateAssistantResponseEventResponse.assistantResponse) AS DOUBLE)
            / length(r.generateAssistantResponseEventRequest.prompt)
       ELSE 0 END                                                      AS response_prompt_ratio,
  CASE WHEN lower(r.generateAssistantResponseEventRequest.prompt) LIKE '%password%'
         OR lower(r.generateAssistantResponseEventRequest.prompt) LIKE '%secret%'
         OR lower(r.generateAssistantResponseEventRequest.prompt) LIKE '%credential%'
         OR lower(r.generateAssistantResponseEventRequest.prompt) LIKE '%api_key%'
         OR lower(r.generateAssistantResponseEventRequest.prompt) LIKE '%private_key%'
         OR lower(r.generateAssistantResponseEventRequest.prompt) LIKE '%token%'
       THEN 1 ELSE 0 END                                               AS sensitive_flag,
  CASE
    WHEN lower(r.generateAssistantResponseEventRequest.prompt) LIKE '%password%'    THEN 'password'
    WHEN lower(r.generateAssistantResponseEventRequest.prompt) LIKE '%secret%'      THEN 'secret'
    WHEN lower(r.generateAssistantResponseEventRequest.prompt) LIKE '%credential%'  THEN 'credential'
    WHEN lower(r.generateAssistantResponseEventRequest.prompt) LIKE '%api_key%'     THEN 'api_key'
    WHEN lower(r.generateAssistantResponseEventRequest.prompt) LIKE '%private_key%' THEN 'private_key'
    WHEN lower(r.generateAssistantResponseEventRequest.prompt) LIKE '%token%'       THEN 'token'
    ELSE 'none' END                                                    AS keyword_triggered,
  hour(from_iso8601_timestamp(r.generateAssistantResponseEventRequest.timeStamp))  AS hour_utc,
  date_format(from_iso8601_timestamp(r.generateAssistantResponseEventRequest.timeStamp), '%Y-%m-%d') AS log_date,
  CASE WHEN hour(from_iso8601_timestamp(r.generateAssistantResponseEventRequest.timeStamp)) < 1
         OR hour(from_iso8601_timestamp(r.generateAssistantResponseEventRequest.timeStamp)) > 11
       THEN 1 ELSE 0 END                                               AS after_hours_flag
FROM ${DATABASE}.prompt_logs
CROSS JOIN UNNEST(records) AS t(r)
LEFT JOIN ${DATABASE}.${IDENTITY_TABLE} m
  ON r.generateAssistantResponseEventRequest.userId = m.kiro_userid
WHERE r.generateAssistantResponseEventRequest.prompt IS NOT NULL;

-- ---------------------------------------------------------------------
-- VIEW: v_productivity — typed view over by_user_analytic.
-- Only the columns verified non-zero in this account get charts, but all
-- are exposed. NOTE: source Date format is MM-dd-yyyy (verified live) —
-- normalized here to yyyy-MM-dd so every consumer sees one format.
-- Identity resolved the same way as v_user_activity_enriched.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW ${DATABASE}.v_productivity AS
SELECT
  a.dt,
  date_format(date_parse(a."date", '%m-%d-%Y'), '%Y-%m-%d')  AS activity_date,
  a.userid,
  COALESCE(i.username, a.userid)                             AS username,
  COALESCE(i.display_name, 'Unknown')                        AS display_name,
  TRY_CAST(a.chat_aicodelines          AS BIGINT)            AS chat_ai_code_lines,
  TRY_CAST(a.chat_messagessent         AS BIGINT)            AS chat_messages_sent,
  TRY_CAST(a.chat_messagesinteracted   AS BIGINT)            AS chat_messages_interacted,
  TRY_CAST(a.inline_aicodelines        AS BIGINT)            AS inline_ai_code_lines,
  TRY_CAST(a.inline_acceptancecount    AS BIGINT)            AS inline_acceptance_count,
  TRY_CAST(a.inline_suggestionscount   AS BIGINT)            AS inline_suggestions_count,
  TRY_CAST(a.dev_acceptedlines         AS BIGINT)            AS dev_accepted_lines,
  TRY_CAST(a.dev_generatedlines        AS BIGINT)            AS dev_generated_lines,
  TRY_CAST(a.testgeneration_acceptedlines AS BIGINT)         AS testgen_accepted_lines,
  TRY_CAST(a.codereview_findingscount  AS BIGINT)            AS codereview_findings,
  TRY_CAST(a.docgeneration_eventcount  AS BIGINT)            AS docgen_events,
  TRY_CAST(a.transformation_eventcount AS BIGINT)            AS transformation_events
FROM ${DATABASE}.by_user_analytic a
LEFT JOIN ${DATABASE}.${IDENTITY_TABLE} i
  ON a.userid = COALESCE(element_at(split(i.kiro_userid, '.'), 2), i.kiro_userid)
WHERE a."date" IS NOT NULL AND a."date" <> '';

-- ---------------------------------------------------------------------
-- VIEW: v_user_activity_enriched — now prefers the report's own
-- user_email for identity (no join needed for those rows), falling back
-- to the identity-sync mapping, then the raw userid.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW ${DATABASE}.v_user_activity_enriched AS
SELECT
  a."date" AS activity_date,
  a.userid,
  COALESCE(a.user_email, i.username, a.userid)   AS username,
  COALESCE(i.display_name, a.user_email, 'Unknown') AS display_name,
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
  a.overage_enabled,
  a.new_user,
  a.auto_messages
FROM ${DATABASE}.v_user_activity a
LEFT JOIN ${DATABASE}.${IDENTITY_TABLE} i
  ON a.userid = COALESCE(element_at(split(i.kiro_userid, '.'), 2), i.kiro_userid)
LEFT JOIN ${DATABASE}.user_project  p ON a.userid = p.userid;
