"""
Kiro Telemetry Dashboard — named query library.

SECURITY CONTRACT: every query is a fixed template. The ONLY runtime value
interpolated is `days`, validated upstream against ALLOWED_DAYS, and the
database name from the Lambda environment (operator-controlled, not
client-controlled). No client string ever reaches SQL.

Each entry: key -> (sql_template, [result column names]).
Templates use {db} for the Glue database and {days} for the window.
`dt` partition-projection predicates keep every query pruned (see the
governance repo's DESIGN_DECISIONS.md).
"""

# Prompt-log tables are partitioned hourly (yyyy/MM/dd/HH); user-activity
# CSVs land under the same scheme. Restricting dt >= the window start keeps
# scans proportional to the window instead of the whole prefix.
_DT_WINDOW = (
    "dt >= date_format(date_add('day', -{days}, current_date), '%Y/%m/%d/00')"
)

_ACTIVITY_WINDOW = (
    "dt >= date_format(date_add('day', -{days}, current_date), '%Y/%m/%d') "
    "AND date(\"date\") >= date_add('day', -{days}, current_date)"
)

_ENRICHED_WINDOW = (
    "dt >= date_format(date_add('day', -{days}, current_date), '%Y/%m/%d') "
    "AND date(activity_date) >= date_add('day', -{days}, current_date)"
)

_LOG_WINDOW = (
    "dt >= date_format(date_add('day', -{days}, current_date), '%Y/%m/%d/00') "
    "AND date(log_date) >= date_add('day', -{days}, current_date)"
)

QUERIES = {
    # ---------------------------------------------------------------- overview
    # KPI queries scan TWO windows (current + the equal-length one before it)
    # and bucket with CASE — the tiles show a delta vs the previous period.
    "overview_kpis": (
        """
        SELECT
          COUNT(DISTINCT CASE WHEN date("date") >= date_add('day', -{days}, current_date)
                              THEN a.userid END)                       AS active_users,
          SUM(CASE WHEN date("date") >= date_add('day', -{days}, current_date)
                   THEN a.credits_used ELSE 0 END)                     AS total_credits,
          SUM(CASE WHEN date("date") >= date_add('day', -{days}, current_date)
                   THEN a.total_messages ELSE 0 END)                   AS total_messages,
          SUM(CASE WHEN date("date") >= date_add('day', -{days}, current_date)
                   THEN a.overage_credits_used ELSE 0 END)             AS overage_credits,
          COUNT(DISTINCT CASE WHEN date("date") < date_add('day', -{days}, current_date)
                              THEN a.userid END)                       AS prev_active_users,
          SUM(CASE WHEN date("date") < date_add('day', -{days}, current_date)
                   THEN a.credits_used ELSE 0 END)                     AS prev_credits,
          SUM(CASE WHEN date("date") < date_add('day', -{days}, current_date)
                   THEN a.total_messages ELSE 0 END)                   AS prev_messages
        FROM {db}.v_user_activity a
        WHERE dt >= date_format(date_add('day', -2*{days}, current_date), '%Y/%m/%d')
          AND date("date") >= date_add('day', -2*{days}, current_date)
        """,
        ["active_users", "total_credits", "total_messages", "overage_credits",
         "prev_active_users", "prev_credits", "prev_messages"],
    ),
    "overview_security_kpis": (
        """
        SELECT
          SUM(CASE WHEN date(log_date) >= date_add('day', -{days}, current_date)
                   THEN sensitive_flag ELSE 0 END)                     AS sensitive_hits,
          SUM(CASE WHEN date(log_date) >= date_add('day', -{days}, current_date)
                   THEN after_hours_flag ELSE 0 END)                   AS after_hours_events,
          SUM(CASE WHEN date(log_date) >= date_add('day', -{days}, current_date)
                   THEN 1 ELSE 0 END)                                  AS total_prompts,
          SUM(CASE WHEN date(log_date) < date_add('day', -{days}, current_date)
                   THEN sensitive_flag ELSE 0 END)                     AS prev_sensitive,
          SUM(CASE WHEN date(log_date) < date_add('day', -{days}, current_date)
                   THEN after_hours_flag ELSE 0 END)                   AS prev_after_hours,
          SUM(CASE WHEN date(log_date) < date_add('day', -{days}, current_date)
                   THEN 1 ELSE 0 END)                                  AS prev_prompts
        FROM {db}.v_prompt_logs
        WHERE dt >= date_format(date_add('day', -2*{days}, current_date), '%Y/%m/%d/00')
          AND date(log_date) >= date_add('day', -2*{days}, current_date)
        """,
        ["sensitive_hits", "after_hours_events", "total_prompts",
         "prev_sensitive", "prev_after_hours", "prev_prompts"],
    ),
    "overview_daily_trends": (
        """
        SELECT "date" AS d,
               COUNT(DISTINCT userid)          AS users,
               ROUND(SUM(credits_used), 2)     AS credits,
               SUM(total_messages)             AS messages
        FROM {db}.v_user_activity
        WHERE """ + _ACTIVITY_WINDOW + """
        GROUP BY "date" ORDER BY d
        """,
        ["d", "users", "credits", "messages"],
    ),
    "overview_security_trends": (
        """
        SELECT log_date AS d,
               SUM(sensitive_flag)    AS sensitive,
               SUM(after_hours_flag)  AS after_hours,
               COUNT(*)               AS prompts
        FROM {db}.v_prompt_logs
        WHERE """ + _LOG_WINDOW + """
        GROUP BY log_date ORDER BY d
        """,
        ["d", "sensitive", "after_hours", "prompts"],
    ),

    # ------------------------------------------------------- usage & adoption
    "usage_daily_active_users": (
        """
        SELECT "date" AS d, COUNT(DISTINCT userid) AS v
        FROM {db}.v_user_activity
        WHERE """ + _ACTIVITY_WINDOW + """
        GROUP BY "date" ORDER BY d
        """,
        ["d", "v"],
    ),
    "usage_daily_credits": (
        """
        SELECT "date" AS d, ROUND(SUM(credits_used), 2) AS v
        FROM {db}.v_user_activity
        WHERE """ + _ACTIVITY_WINDOW + """
        GROUP BY "date" ORDER BY d
        """,
        ["d", "v"],
    ),
    "usage_daily_messages": (
        """
        SELECT "date" AS d, SUM(total_messages) AS v
        FROM {db}.v_user_activity
        WHERE """ + _ACTIVITY_WINDOW + """
        GROUP BY "date" ORDER BY d
        """,
        ["d", "v"],
    ),
    "usage_by_client_type": (
        """
        SELECT client_type AS k, SUM(total_messages) AS v
        FROM {db}.v_user_activity
        WHERE """ + _ACTIVITY_WINDOW + """
        GROUP BY client_type ORDER BY v DESC
        """,
        ["k", "v"],
    ),

    # -------------------------------------------------- security & compliance
    "security_keyword_alerts_daily": (
        """
        SELECT log_date AS d, SUM(sensitive_flag) AS v
        FROM {db}.v_prompt_logs
        WHERE """ + _LOG_WINDOW + """
        GROUP BY log_date ORDER BY d
        """,
        ["d", "v"],
    ),
    "security_after_hours_daily": (
        """
        SELECT log_date AS d, SUM(after_hours_flag) AS v
        FROM {db}.v_prompt_logs
        WHERE """ + _LOG_WINDOW + """
        GROUP BY log_date ORDER BY d
        """,
        ["d", "v"],
    ),
    "security_keyword_breakdown": (
        """
        SELECT keyword_triggered AS k, COUNT(*) AS v
        FROM {db}.v_prompt_logs
        WHERE sensitive_flag = 1 AND """ + _LOG_WINDOW + """
        GROUP BY keyword_triggered ORDER BY v DESC
        """,
        ["k", "v"],
    ),
    "security_audit_trail": (
        # prompt_text is already truncated to 500 chars in the view;
        # row cap bounds both payload size and reviewer exposure.
        """
        SELECT log_date, username, keyword_triggered, after_hours_flag,
               prompt_text
        FROM {db}.v_prompt_logs
        WHERE (sensitive_flag = 1 OR after_hours_flag = 1)
          AND """ + _LOG_WINDOW + """
        ORDER BY log_date DESC
        LIMIT 200
        """,
        ["log_date", "username", "keyword_triggered", "after_hours_flag", "prompt_text"],
    ),

    # ----------------------------------------------------------- prompt quality
    "quality_avg_prompt_length": (
        """
        SELECT log_date AS d, ROUND(AVG(prompt_length), 1) AS v
        FROM {db}.v_prompt_logs
        WHERE """ + _LOG_WINDOW + """
        GROUP BY log_date ORDER BY d
        """,
        ["d", "v"],
    ),
    "quality_response_prompt_ratio": (
        """
        SELECT log_date AS d, ROUND(AVG(response_prompt_ratio), 2) AS v
        FROM {db}.v_prompt_logs
        WHERE """ + _LOG_WINDOW + """
        GROUP BY log_date ORDER BY d
        """,
        ["d", "v"],
    ),
    "quality_avg_response_length": (
        """
        SELECT log_date AS d, ROUND(AVG(response_length), 1) AS v
        FROM {db}.v_prompt_logs
        WHERE """ + _LOG_WINDOW + """
        GROUP BY log_date ORDER BY d
        """,
        ["d", "v"],
    ),
    "quality_trigger_type": (
        """
        SELECT trigger_type AS k, COUNT(*) AS v
        FROM {db}.v_prompt_logs
        WHERE """ + _LOG_WINDOW + """
        GROUP BY trigger_type ORDER BY v DESC
        """,
        ["k", "v"],
    ),

    # ----------------------------------------------------------- cost governance
    "cost_by_tier": (
        """
        SELECT subscription_tier AS k, ROUND(SUM(credits_used), 2) AS v
        FROM {db}.v_user_activity
        WHERE """ + _ACTIVITY_WINDOW + """
        GROUP BY subscription_tier ORDER BY v DESC
        """,
        ["k", "v"],
    ),
    "cost_overage_trend": (
        """
        SELECT "date" AS d, ROUND(SUM(overage_credits_used), 2) AS v
        FROM {db}.v_user_activity
        WHERE """ + _ACTIVITY_WINDOW + """
        GROUP BY "date" ORDER BY d
        """,
        ["d", "v"],
    ),
    "cost_by_team": (
        """
        SELECT team AS k, ROUND(SUM(credits_used), 2) AS v
        FROM {db}.v_user_activity_enriched
        WHERE """ + _ENRICHED_WINDOW + """
        GROUP BY team ORDER BY v DESC
        """,
        ["k", "v"],
    ),
    "cost_by_project": (
        """
        SELECT project AS k, ROUND(SUM(credits_used), 2) AS v
        FROM {db}.v_user_activity_enriched
        WHERE """ + _ENRICHED_WINDOW + """
        GROUP BY project ORDER BY v DESC
        """,
        ["k", "v"],
    ),
    "cost_by_cost_center": (
        """
        SELECT cost_center AS k, ROUND(SUM(credits_used), 2) AS v
        FROM {db}.v_user_activity_enriched
        WHERE """ + _ENRICHED_WINDOW + """
        GROUP BY cost_center ORDER BY v DESC
        """,
        ["k", "v"],
    ),
    "cost_mapping_coverage": (
        """
        SELECT
          ROUND(SUM(credits_used), 2)                                     AS total_credits,
          ROUND(SUM(CASE WHEN team <> 'UNMAPPED' THEN credits_used ELSE 0 END), 2) AS mapped_credits,
          COUNT(DISTINCT userid)                                          AS total_users,
          COUNT(DISTINCT CASE WHEN team <> 'UNMAPPED' THEN userid END)    AS mapped_users
        FROM {db}.v_user_activity_enriched
        WHERE """ + _ENRICHED_WINDOW,
        ["total_credits", "mapped_credits", "total_users", "mapped_users"],
    ),
    "cost_allocation_table": (
        """
        SELECT team, project, cost_center,
               COUNT(DISTINCT userid)          AS users,
               ROUND(SUM(credits_used), 2)     AS credits
        FROM {db}.v_user_activity_enriched
        WHERE """ + _ENRICHED_WINDOW + """
        GROUP BY team, project, cost_center
        ORDER BY credits DESC LIMIT 50
        """,
        ["team", "project", "cost_center", "users", "credits"],
    ),
    "cost_top_users": (
        """
        SELECT COALESCE(display_name, userid) AS k,
               ROUND(SUM(credits_used), 2) AS v
        FROM {db}.v_user_activity_enriched
        WHERE """ + _ENRICHED_WINDOW + """
        GROUP BY COALESCE(display_name, userid)
        ORDER BY v DESC
        LIMIT 20
        """,
        ["k", "v"],
    ),
    # ----------------------------------------------------------- productivity
    # Source: by_user_analytic (v_productivity). Only columns verified
    # non-zero in this account are charted; others exist in the view.
    "prod_ai_code_lines_daily": (
        """
        SELECT activity_date AS d,
               SUM(COALESCE(chat_ai_code_lines,0) + COALESCE(inline_ai_code_lines,0)) AS v
        FROM {db}.v_productivity
        WHERE """ + _ENRICHED_WINDOW + """
        GROUP BY activity_date ORDER BY d
        """,
        ["d", "v"],
    ),
    "prod_inline_acceptance_daily": (
        # acceptance rate = accepted / suggested, guarded against /0
        """
        SELECT activity_date AS d,
               ROUND(100.0 * SUM(COALESCE(inline_acceptance_count,0))
                     / NULLIF(SUM(COALESCE(inline_suggestions_count,0)), 0), 1) AS v
        FROM {db}.v_productivity
        WHERE """ + _ENRICHED_WINDOW + """
        GROUP BY activity_date ORDER BY d
        """,
        ["d", "v"],
    ),
    "prod_output_mix": (
        """
        SELECT 'Chat' AS k, SUM(COALESCE(chat_ai_code_lines,0)) AS v
        FROM {db}.v_productivity WHERE """ + _ENRICHED_WINDOW + """
        UNION ALL
        SELECT 'Inline' AS k, SUM(COALESCE(inline_ai_code_lines,0)) AS v
        FROM {db}.v_productivity WHERE """ + _ENRICHED_WINDOW + """
        UNION ALL
        SELECT 'Dev agent' AS k, SUM(COALESCE(dev_accepted_lines,0)) AS v
        FROM {db}.v_productivity WHERE """ + _ENRICHED_WINDOW + """
        UNION ALL
        SELECT 'Test gen' AS k, SUM(COALESCE(testgen_accepted_lines,0)) AS v
        FROM {db}.v_productivity WHERE """ + _ENRICHED_WINDOW,
        ["k", "v"],
    ),
    "prod_lines_per_credit": (
        # ROI proxy: AI code lines per credit, per user (joins the two
        # daily grains on user+date; both sides pre-aggregated)
        """
        WITH p AS (
          SELECT activity_date, userid, username,
                 SUM(COALESCE(chat_ai_code_lines,0) + COALESCE(inline_ai_code_lines,0)) AS lines
          FROM {db}.v_productivity
          WHERE """ + _ENRICHED_WINDOW + """
          GROUP BY activity_date, userid, username
        ), c AS (
          SELECT "date" AS activity_date, userid, SUM(credits_used) AS credits
          FROM {db}.v_user_activity
          WHERE dt >= date_format(date_add('day', -{days}, current_date), '%Y/%m/%d')
            AND date("date") >= date_add('day', -{days}, current_date)
          GROUP BY "date", userid
        )
        SELECT p.username AS k,
               ROUND(SUM(p.lines) / NULLIF(SUM(c.credits), 0), 1) AS v
        FROM p JOIN c ON p.activity_date = c.activity_date AND p.userid = c.userid
        GROUP BY p.username ORDER BY v DESC LIMIT 20
        """,
        ["k", "v"],
    ),
    "prod_user_summary": (
        """
        SELECT username AS k,
               SUM(COALESCE(chat_ai_code_lines,0) + COALESCE(inline_ai_code_lines,0)) AS v
        FROM {db}.v_productivity
        WHERE """ + _ENRICHED_WINDOW + """
        GROUP BY username ORDER BY v DESC LIMIT 20
        """,
        ["k", "v"],
    ),

    "prod_adoption_stages": (
        # Adoption-maturity funnel: stage 1 inline, stage 2 chat, agentic
        # (stage 3) is read from user_activity's auto_messages.
        """
        SELECT
          SUM(COALESCE(inline_suggestions_count, 0))  AS inline_suggestions,
          SUM(COALESCE(inline_acceptance_count, 0))   AS inline_accepts,
          SUM(COALESCE(chat_messages_sent, 0))        AS chat_messages,
          SUM(COALESCE(chat_ai_code_lines, 0) + COALESCE(inline_ai_code_lines, 0)) AS ai_code_lines
        FROM {db}.v_productivity
        WHERE """ + _ENRICHED_WINDOW,
        ["inline_suggestions", "inline_accepts", "chat_messages", "ai_code_lines"],
    ),
    "prod_agentic_kpis": (
        """
        SELECT SUM(COALESCE(auto_messages, 0)) AS auto_messages,
               SUM(total_messages)             AS total_messages
        FROM {db}.v_user_activity
        WHERE """ + _ACTIVITY_WINDOW,
        ["auto_messages", "total_messages"],
    ),

    # ----------------------------------------------------------- budget
    # Month-to-date figures use the calendar month, independent of `days`
    # (a burn-rate window makes no sense split across months). {days}
    # still bounds the daily-burn history chart.
    "budget_mtd": (
        """
        SELECT ROUND(SUM(credits_used), 2)          AS mtd_credits,
               ROUND(SUM(overage_credits_used), 2)  AS mtd_overage,
               MAX(overage_cap)                     AS overage_cap,
               COUNT(DISTINCT "date")               AS active_days,
               DAY(current_date)                    AS day_of_month,
               DAY(last_day_of_month(current_date)) AS days_in_month
        FROM {db}.v_user_activity
        WHERE dt >= date_format(date_trunc('month', current_date), '%Y/%m/%d')
          AND date("date") >= date_trunc('month', current_date)
        """,
        ["mtd_credits", "mtd_overage", "overage_cap", "active_days", "day_of_month", "days_in_month"],
    ),
    "budget_daily_burn": (
        """
        SELECT "date" AS d, ROUND(SUM(credits_used), 2) AS v
        FROM {db}.v_user_activity
        WHERE dt >= date_format(date_add('day', -{days}, current_date), '%Y/%m/%d')
          AND date("date") >= date_add('day', -{days}, current_date)
        GROUP BY "date" ORDER BY d
        """,
        ["d", "v"],
    ),
    "budget_by_user_mtd": (
        """
        SELECT COALESCE(user_email, userid) AS k, ROUND(SUM(credits_used), 2) AS v
        FROM {db}.v_user_activity
        WHERE dt >= date_format(date_trunc('month', current_date), '%Y/%m/%d')
          AND date("date") >= date_trunc('month', current_date)
        GROUP BY COALESCE(user_email, userid) ORDER BY v DESC LIMIT 20
        """,
        ["k", "v"],
    ),
    "budget_by_tier_mtd": (
        """
        SELECT subscription_tier AS k, ROUND(SUM(credits_used), 2) AS v
        FROM {db}.v_user_activity
        WHERE dt >= date_format(date_trunc('month', current_date), '%Y/%m/%d')
          AND date("date") >= date_trunc('month', current_date)
        GROUP BY subscription_tier ORDER BY v DESC
        """,
        ["k", "v"],
    ),
    "budget_overage_days": (
        """
        SELECT "date" AS d, ROUND(SUM(overage_credits_used), 2) AS v
        FROM {db}.v_user_activity
        WHERE dt >= date_format(date_add('day', -{days}, current_date), '%Y/%m/%d')
          AND date("date") >= date_add('day', -{days}, current_date)
          AND overage_credits_used > 0
        GROUP BY "date" ORDER BY d
        """,
        ["d", "v"],
    ),

    # ----------------------------------------------------------- adoption (usage tab)
    "usage_new_users_daily": (
        """
        SELECT "date" AS d, COUNT(DISTINCT userid) AS v
        FROM {db}.v_user_activity
        WHERE new_user = 'true' AND """ + _ACTIVITY_WINDOW + """
        GROUP BY "date" ORDER BY d
        """,
        ["d", "v"],
    ),
    "usage_auto_share_daily": (
        """
        SELECT "date" AS d,
               ROUND(100.0 * SUM(COALESCE(auto_messages,0))
                     / NULLIF(SUM(total_messages), 0), 1) AS v
        FROM {db}.v_user_activity
        WHERE """ + _ACTIVITY_WINDOW + """
        GROUP BY "date" ORDER BY d
        """,
        ["d", "v"],
    ),
    "quality_model_distribution": (
        """
        SELECT model_id AS k, COUNT(*) AS v
        FROM {db}.v_prompt_logs
        WHERE """ + _LOG_WINDOW + """
        GROUP BY model_id ORDER BY v DESC
        """,
        ["k", "v"],
    ),

    # ----------------------------------------------------------- dora
    # Snapshot table is tiny (<=120 days, a few repos) — no dt pruning
    # needed. Definitions ported from timwukp/dora-metrics-platform.
    "dora_kpis": (
        """
        SELECT COUNT(*)                                        AS merged_prs,
               ROUND(approx_percentile(time_to_merge_hours, 0.5), 1) AS median_merge_h,
               ROUND(approx_percentile(lead_time_hours, 0.5), 1)     AS median_lead_h,
               ROUND(approx_percentile(review_latency_hours, 0.5), 1) AS median_review_latency_h,
               SUM(CASE WHEN is_revert OR is_hotfix THEN 1 ELSE 0 END) AS failure_signals,
               SUM(CASE WHEN assisted_by <> 'none' THEN 1 ELSE 0 END)  AS ai_assisted
        FROM {db}.v_dora_prs
        WHERE merged_ts IS NOT NULL
          AND date(merged_date) >= date_add('day', -{days}, current_date)
        """,
        ["merged_prs", "median_merge_h", "median_lead_h", "median_review_latency_h", "failure_signals", "ai_assisted"],
    ),
    "dora_prs_merged_daily": (
        """
        SELECT merged_date AS d, COUNT(*) AS v
        FROM {db}.v_dora_prs
        WHERE merged_ts IS NOT NULL
          AND date(merged_date) >= date_add('day', -{days}, current_date)
        GROUP BY merged_date ORDER BY d
        """,
        ["d", "v"],
    ),
    "dora_time_to_merge_daily": (
        """
        SELECT merged_date AS d,
               ROUND(approx_percentile(time_to_merge_hours, 0.5), 1) AS v
        FROM {db}.v_dora_prs
        WHERE merged_ts IS NOT NULL
          AND date(merged_date) >= date_add('day', -{days}, current_date)
        GROUP BY merged_date ORDER BY d
        """,
        ["d", "v"],
    ),
    "dora_by_repo": (
        """
        SELECT repo AS k, COUNT(*) AS v
        FROM {db}.v_dora_prs
        WHERE merged_ts IS NOT NULL
          AND date(merged_date) >= date_add('day', -{days}, current_date)
        GROUP BY repo ORDER BY v DESC
        """,
        ["k", "v"],
    ),
    "dora_ai_share": (
        """
        SELECT assisted_by AS k, COUNT(*) AS v
        FROM {db}.v_dora_prs
        WHERE merged_ts IS NOT NULL
          AND date(merged_date) >= date_add('day', -{days}, current_date)
        GROUP BY assisted_by ORDER BY v DESC
        """,
        ["k", "v"],
    ),
    "dora_ai_vs_speed": (
        # The headline correlation: do AI-assisted PRs merge faster?
        """
        SELECT CASE WHEN assisted_by <> 'none' THEN 'AI-assisted' ELSE 'Unassisted' END AS k,
               ROUND(approx_percentile(time_to_merge_hours, 0.5), 1) AS v
        FROM {db}.v_dora_prs
        WHERE merged_ts IS NOT NULL
          AND date(merged_date) >= date_add('day', -{days}, current_date)
        GROUP BY 1 ORDER BY v
        """,
        ["k", "v"],
    ),
    "dora_recent_prs": (
        """
        SELECT merged_date, repo, number, title, author,
               ROUND(time_to_merge_hours, 1) AS merge_h, assisted_by
        FROM {db}.v_dora_prs
        WHERE merged_ts IS NOT NULL
          AND date(merged_date) >= date_add('day', -{days}, current_date)
        ORDER BY merged_ts DESC
        LIMIT 50
        """,
        ["merged_date", "repo", "number", "title", "author", "merge_h", "assisted_by"],
    ),
}

# Endpoint -> the named queries it runs (one Athena execution per query,
# executed concurrently by the handler).
ENDPOINTS = {
    "overview": ["overview_kpis", "overview_security_kpis",
                 "overview_daily_trends", "overview_security_trends"],
    "usage": [
        "usage_daily_active_users",
        "usage_daily_credits",
        "usage_daily_messages",
        "usage_by_client_type",
        "usage_new_users_daily",
        "usage_auto_share_daily",
    ],
    "security": [
        "security_keyword_alerts_daily",
        "security_after_hours_daily",
        "security_keyword_breakdown",
        "security_audit_trail",
    ],
    "quality": [
        "quality_avg_prompt_length",
        "quality_response_prompt_ratio",
        "quality_avg_response_length",
        "quality_trigger_type",
        "quality_model_distribution",
    ],
    "productivity": [
        "prod_ai_code_lines_daily",
        "prod_inline_acceptance_daily",
        "prod_output_mix",
        "prod_lines_per_credit",
        "prod_user_summary",
        "prod_adoption_stages",
        "prod_agentic_kpis",
    ],
    "budget": [
        "budget_mtd",
        "budget_daily_burn",
        "budget_by_user_mtd",
        "budget_by_tier_mtd",
        "budget_overage_days",
    ],
    "dora": [
        "dora_kpis",
        "dora_prs_merged_daily",
        "dora_time_to_merge_daily",
        "dora_by_repo",
        "dora_ai_share",
        "dora_ai_vs_speed",
        "dora_recent_prs",
    ],
    "cost": [
        "cost_by_tier",
        "cost_overage_trend",
        "cost_by_team",
        "cost_by_project",
        "cost_by_cost_center",
        "cost_top_users",
        "cost_mapping_coverage",
        "cost_allocation_table",
    ],
}

ALLOWED_DAYS = {7, 30, 90}


def build_sql(query_key: str, database: str, days: int) -> str:
    """Render a named query. Raises KeyError/ValueError on anything unexpected."""
    if days not in ALLOWED_DAYS:
        raise ValueError(f"days must be one of {sorted(ALLOWED_DAYS)}")
    if not database.replace("_", "").isalnum():
        raise ValueError("invalid database name")
    template, _cols = QUERIES[query_key]
    return template.format(db=database, days=days)
