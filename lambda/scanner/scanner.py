"""
Kiro Telemetry Dashboard — governance scanner.

Scheduled (EventBridge) Lambda that runs Athena governance checks and
publishes findings to SNS. Extends the governance repo's design with a
budget-threshold check.

Checks:
  1. Sensitive-keyword prompts in the lookback window
  2. After-hours prompts in the lookback window
  3. Month-to-date credits above BUDGET_ALERT_PCT of the overage cap

Environment:
    DATABASE, WORKGROUP, SNS_TOPIC_ARN, OUTPUT_LOCATION
    LOOKBACK_HOURS      default 24 (daily schedule)
    BUDGET_ALERT_PCT    default 80
"""

import json
import logging
import os
import time

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DATABASE = os.environ["DATABASE"]
WORKGROUP = os.environ["WORKGROUP"]
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
OUTPUT_LOCATION = os.environ["OUTPUT_LOCATION"]
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "24"))
BUDGET_ALERT_PCT = float(os.environ.get("BUDGET_ALERT_PCT", "80"))

athena = boto3.client("athena")
sns = boto3.client("sns")


def _run(query: str):
    qid = athena.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
        ResultConfiguration={"OutputLocation": OUTPUT_LOCATION},
    )["QueryExecutionId"]
    while True:
        state = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1.5)
    if state != "SUCCEEDED":
        raise RuntimeError(f"Athena query {qid} ended in state {state}")
    rows = athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
    return [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows[1:]]


def check_sensitive():
    rows = _run(f"""
        SELECT username, keyword_triggered, COUNT(*) AS hits
        FROM {DATABASE}.v_prompt_logs
        WHERE sensitive_flag = 1
          AND from_iso8601_timestamp(ts) >= current_timestamp - INTERVAL '{LOOKBACK_HOURS}' HOUR
        GROUP BY username, keyword_triggered ORDER BY hits DESC LIMIT 20
    """)
    if rows:
        lines = [f"  {u}: '{k}' x{n}" for u, k, n in rows]
        return ("Sensitive-keyword prompts (last %dh):\n" % LOOKBACK_HOURS) + "\n".join(lines)
    return None


def check_after_hours():
    rows = _run(f"""
        SELECT username, COUNT(*) AS events
        FROM {DATABASE}.v_prompt_logs
        WHERE after_hours_flag = 1
          AND from_iso8601_timestamp(ts) >= current_timestamp - INTERVAL '{LOOKBACK_HOURS}' HOUR
        GROUP BY username ORDER BY events DESC LIMIT 20
    """)
    if rows:
        lines = [f"  {u}: {n} after-hours prompts" for u, n in rows]
        return ("After-hours activity (last %dh):\n" % LOOKBACK_HOURS) + "\n".join(lines)
    return None


def check_budget():
    rows = _run(f"""
        SELECT ROUND(SUM(credits_used), 1)          AS mtd,
               MAX(overage_cap)                     AS cap,
               ROUND(SUM(overage_credits_used), 1)  AS overage
        FROM {DATABASE}.v_user_activity
        WHERE date("date") >= date_trunc('month', current_date)
    """)
    if not rows or not rows[0][0]:
        return None
    mtd, cap, overage = (float(x or 0) for x in rows[0])
    findings = []
    if cap > 0 and mtd >= cap * BUDGET_ALERT_PCT / 100:
        findings.append(
            f"MTD credits {mtd:,.0f} have reached {100 * mtd / cap:.0f}% "
            f"of the overage cap ({cap:,.0f}). Threshold: {BUDGET_ALERT_PCT:.0f}%."
        )
    if overage > 0:
        findings.append(f"Overage credits consumed this month: {overage:,.1f}.")
    return "Budget alert:\n  " + "\n  ".join(findings) if findings else None


def lambda_handler(event, context):
    findings = []
    for name, fn in (("sensitive", check_sensitive),
                     ("after_hours", check_after_hours),
                     ("budget", check_budget)):
        try:
            f = fn()
            if f:
                findings.append(f)
        except Exception as exc:  # noqa: BLE001 — one failed check must not mute the rest
            logger.error("check %s failed: %s", name, exc)
            findings.append(f"[scanner error] check '{name}' failed: {str(exc)[:200]}")

    if findings:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject="Kiro governance findings",
            Message="\n\n".join(findings),
        )
        logger.info("published %d findings", len(findings))
    else:
        logger.info("no findings")
    return {"findings": len(findings)}
