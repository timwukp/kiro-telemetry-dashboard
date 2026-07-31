"""
Kiro Telemetry Dashboard — API Lambda.

Routes (all behind the API Gateway JWT authorizer AND the CloudFront
origin-verify header):

    GET /api/{tab}?days={7|30|90}      tab in ENDPOINTS
    GET /api/health                    component freshness for the architecture view
    GET /api/policy                    policy registry (MCP allowlist + steering index)
    PUT /api/policy                    admin-only (Cognito `admins` group) update

Response (tabs): {"days": N, "results": {query_key: {"columns": [...], "rows": [[...]]}}}

Defense in depth:
  1. API Gateway JWT authorizer has already validated the Cognito token
     before this code runs.
  2. x-origin-verify must match ORIGIN_VERIFY_SECRET — blocks callers who
     discover the execute-api endpoint and try to go around CloudFront.
  3. Only named queries from queries.py run; `days` is the sole client
     input and is validated against a fixed set.
  4. In-memory TTL cache bounds Athena spend under repeated loads.
"""

import concurrent.futures
import hmac
import json
import logging
import os
import time

import boto3
from botocore.config import Config

from queries import ALLOWED_DAYS, ENDPOINTS, QUERIES, build_sql

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DATABASE = os.environ["DATABASE"]
WORKGROUP = os.environ["WORKGROUP"]
ORIGIN_VERIFY_SECRET = os.environ["ORIGIN_VERIFY_SECRET"]
LOG_BUCKET = os.environ.get("LOG_BUCKET", "")
POLICY_PREFIX = os.environ.get("POLICY_PREFIX", "kiro/policy/")
MAX_POLICY_BYTES = 64 * 1024     # a 64KB allowlist is already enormous

CACHE_TTL_SECONDS = 300          # warm-container cache; Athena is the cold path
CACHE_PREFIX = os.environ.get("CACHE_PREFIX", "kiro/cache/")
# S3 materialized cache (SPICE-equivalent): the warmer refreshes every
# 15 min; anything younger than this is served without touching Athena.
# Stale entries are STILL served (stale-while-revalidate) — the warmer,
# not the user, pays the Athena latency.
S3_CACHE_FRESH_SECONDS = 20 * 60
POLL_INTERVAL_SECONDS = 1.0
QUERY_TIMEOUT_SECONDS = 55       # < Lambda timeout (60) and APIGW timeout (29)*
# *APIGW cuts the connection at 29s; the cache means a timed-out first load
#  usually succeeds on retry once queries finish server-side.

_athena = boto3.client("athena", config=Config(
    retries={"max_attempts": 3, "mode": "standard"},
    max_pool_connections=20,
))
_s3 = boto3.client("s3")
_lambda = boto3.client("lambda")
_cache: dict = {}   # key -> (expires_epoch, payload)

DEFAULT_POLICY = {
    "version": 0,   # never persisted; first admin write becomes v1
    "updated_by": None,
    "updated_at": None,
    "mcp_allowlist": {},     # Kiro mcp.json "mcpServers" object shape
    "steering_files": [],    # [{"name": "...", "content_md": "..."}]
    "dora_repos": [],        # ["owner/repo", ...] tracked by the dora-sync Lambda
    "notes": "Distributed to developer machines via kiro-policy-sync.sh; "
             "workspace-level Kiro config can still override user-level — "
             "telemetry audit is the compensating control.",
}


def _claims(event: dict) -> dict:
    return ((event.get("requestContext") or {}).get("authorizer") or {}) \
        .get("jwt", {}).get("claims", {})


def _is_admin(event: dict) -> bool:
    """HTTP API JWT authorizer flattens cognito:groups to a string like
    '[admins]' or a JSON list; handle both."""
    groups = _claims(event).get("cognito:groups", "")
    if isinstance(groups, list):
        return "admins" in groups
    return "admins" in str(groups).strip("[]").replace('"', "").split()


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(body, default=str),
    }


def _origin_verified(event: dict) -> bool:
    header = (event.get("headers") or {}).get("x-origin-verify", "")
    return hmac.compare_digest(header, ORIGIN_VERIFY_SECRET)


def _run_query(query_key: str, days: int) -> dict:
    """Execute one named query synchronously and shape the result."""
    sql = build_sql(query_key, DATABASE, days)
    execution = _athena.start_query_execution(
        QueryString=sql,
        WorkGroup=WORKGROUP,
        QueryExecutionContext={"Database": DATABASE},
    )
    qid = execution["QueryExecutionId"]

    deadline = time.time() + QUERY_TIMEOUT_SECONDS
    while True:
        state = _athena.get_query_execution(QueryExecutionId=qid)
        status = state["QueryExecution"]["Status"]["State"]
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "CANCELLED"):
            reason = state["QueryExecution"]["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"{query_key}: {status}: {reason[:200]}")
        if time.time() > deadline:
            _athena.stop_query_execution(QueryExecutionId=qid)
            raise TimeoutError(f"{query_key}: timed out after {QUERY_TIMEOUT_SECONDS}s")
        time.sleep(POLL_INTERVAL_SECONDS)

    columns, rows = [], []
    paginator = _athena.get_paginator("get_query_results")
    for page in paginator.paginate(QueryExecutionId=qid):
        meta = page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
        if not columns:
            columns = [c["Name"] for c in meta]
        for row in page["ResultSet"]["Rows"]:
            values = [f.get("VarCharValue") for f in row["Data"]]
            rows.append(values)
    # Athena returns the header as row 0 of the first page
    if rows and rows[0] == columns:
        rows = rows[1:]
    return {"columns": columns, "rows": rows}


def _s3_cache_key(tab: str, days: int) -> str:
    return f"{CACHE_PREFIX.rstrip('/')}/{tab}-{days}.json"


def _s3_cache_get(tab: str, days: int):
    """Return (payload, age_seconds) from the materialized cache, or None."""
    try:
        obj = _s3.get_object(Bucket=LOG_BUCKET, Key=_s3_cache_key(tab, days))
        payload = json.loads(obj["Body"].read())
        age = time.time() - obj["LastModified"].timestamp()
        return payload, age
    except Exception:  # noqa: BLE001 — missing/denied cache is just a miss
        return None


def _s3_cache_put(tab: str, days: int, payload: dict):
    try:
        _s3.put_object(
            Bucket=LOG_BUCKET, Key=_s3_cache_key(tab, days),
            Body=json.dumps(payload, default=str).encode(),
            ContentType="application/json",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache write failed for %s/%s: %s", tab, days, exc)


def _run_tab_queries(tab: str, days: int) -> dict:
    """Run the tab's Athena queries (the expensive path)."""
    query_keys = ENDPOINTS[tab]
    results, errors = {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(query_keys))) as pool:
        futures = {pool.submit(_run_query, k, days): k for k in query_keys}
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as exc:  # noqa: BLE001 - degrade per-panel, not per-page
                logger.error("query %s failed: %s", key, exc)
                errors[key] = str(exc)[:300]

    payload = {"days": days, "results": results}
    if errors:
        payload["errors"] = errors
    return payload


def _handle_tab(tab: str, days: int) -> dict:
    """Serve from cache layers: warm-container dict -> S3 materialized
    cache (SPICE-equivalent, refreshed by the warmer) -> live Athena."""
    cache_key = (tab, days)
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    s3_hit = _s3_cache_get(tab, days)
    if s3_hit:
        payload, age = s3_hit
        payload["cache_age_seconds"] = int(age)
        # Serve even if stale — the warmer refreshes on schedule, so the
        # user never pays Athena latency once the cache exists.
        _cache[cache_key] = (now + min(CACHE_TTL_SECONDS, 60), payload)
        return payload

    payload = _run_tab_queries(tab, days)
    if not payload.get("errors"):
        _cache[cache_key] = (now + CACHE_TTL_SECONDS, payload)
        _s3_cache_put(tab, days, payload)
    return payload


def _warm(event: dict) -> dict:
    """EventBridge warm event: refresh the S3 materialized cache for every
    tab x days combination. Runs inside this Lambda (300s headroom is the
    warmer's, not a user's)."""
    warmed, failed = [], []
    combos = [(tab, days) for tab in ENDPOINTS for days in sorted(ALLOWED_DAYS)]
    for tab, days in combos:
        try:
            payload = _run_tab_queries(tab, days)
            if payload.get("errors"):
                failed.append(f"{tab}:{days}:{list(payload['errors'])}")
            else:
                _s3_cache_put(tab, days, payload)
                warmed.append(f"{tab}:{days}")
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{tab}:{days}:{str(exc)[:100]}")
    logger.info("warmed %d combos, %d failed: %s", len(warmed), len(failed), failed[:5])
    return {"warmed": len(warmed), "failed": failed}


def _policy_key() -> str:
    return POLICY_PREFIX.rstrip("/") + "/policy.json"


def _get_policy() -> dict:
    try:
        obj = _s3.get_object(Bucket=LOG_BUCKET, Key=_policy_key())
        return json.loads(obj["Body"].read())
    except _s3.exceptions.NoSuchKey:
        return dict(DEFAULT_POLICY)


def _put_policy(event: dict) -> dict:
    if not _is_admin(event):
        return _response(403, {"error": "admins group required"})
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    if len(raw) > MAX_POLICY_BYTES:
        return _response(413, {"error": f"policy exceeds {MAX_POLICY_BYTES} bytes"})
    try:
        incoming = json.loads(raw)
    except json.JSONDecodeError:
        return _response(400, {"error": "body must be JSON"})

    allow = incoming.get("mcp_allowlist")
    steering = incoming.get("steering_files", [])
    dora_repos = incoming.get("dora_repos", [])
    if not isinstance(allow, dict):
        return _response(400, {"error": "mcp_allowlist must be an object (Kiro mcpServers shape)"})
    import re as _re
    if not isinstance(dora_repos, list) or not all(
        isinstance(r, str) and _re.fullmatch(r"[\w.-]+/[\w.-]+", r) for r in dora_repos
    ):
        return _response(400, {"error": "dora_repos must be a list of 'owner/repo' strings"})
    if not isinstance(steering, list) or not all(
        isinstance(f, dict) and isinstance(f.get("name"), str) and isinstance(f.get("content_md"), str)
        and f["name"].endswith(".md") and "/" not in f["name"] and ".." not in f["name"]
        for f in steering
    ):
        return _response(400, {"error": "steering_files must be [{name: '*.md', content_md: str}]"})

    claims = _claims(event)
    current = _get_policy()
    policy = {
        "version": int(current.get("version", 0)) + 1,
        "updated_by": claims.get("email") or claims.get("username") or claims.get("sub"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mcp_allowlist": allow,
        "steering_files": steering,
        "dora_repos": dora_repos,
        "notes": DEFAULT_POLICY["notes"],
    }
    _s3.put_object(
        Bucket=LOG_BUCKET, Key=_policy_key(),
        Body=json.dumps(policy, indent=2).encode(),
        ContentType="application/json",
    )
    logger.info("policy v%s updated by %s", policy["version"], policy["updated_by"])
    return _response(200, policy)


def _health() -> dict:
    """Lightweight component status for the architecture view. S3-only
    checks (fast, no Athena) + this function's own metadata."""
    out = {}
    try:
        for name, prefix in (
            ("prompt_logs", "kiro/prompt-log/"),
            ("user_reports", "kiro/user-activity-metrics/"),
            ("identity_mapping", "kiro/user-mapping/"),
            ("policy_registry", POLICY_PREFIX),
        ):
            resp = _s3.list_objects_v2(Bucket=LOG_BUCKET, Prefix=prefix, MaxKeys=1)
            newest = None
            if resp.get("KeyCount"):
                # cheap freshness: probe a page and take max LastModified
                page = _s3.list_objects_v2(Bucket=LOG_BUCKET, Prefix=prefix, MaxKeys=1000)
                newest = max(o["LastModified"] for o in page.get("Contents", [])).isoformat()
            out[name] = {"ok": bool(resp.get("KeyCount")), "latest": newest}
    except Exception as exc:  # noqa: BLE001
        out["s3_error"] = str(exc)[:200]
    try:
        cfg = _lambda.get_function_configuration(FunctionName="kiro-dashboard-api")
        out["api_lambda"] = {"ok": True, "version": cfg.get("Version"),
                             "modified": cfg.get("LastModified")}
    except Exception:  # noqa: BLE001
        out["api_lambda"] = {"ok": True}   # we are obviously running
    out["database"] = DATABASE
    out["workgroup"] = WORKGROUP
    return _response(200, out)


def lambda_handler(event, context):
    # EventBridge warmer invocation (no HTTP context, trusted principal —
    # invoke permission is scoped to the warmer rule in CloudFormation).
    if event.get("ktd_warm") is True:
        return _warm(event)

    if not _origin_verified(event):
        logger.warning("rejected request without valid x-origin-verify")
        return _response(403, {"error": "forbidden"})

    raw_path = event.get("rawPath", "")
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "GET")
    parts = [p for p in raw_path.split("/") if p]

    if len(parts) == 2 and parts[0] == "api" and parts[1] == "health":
        return _health()
    if len(parts) == 2 and parts[0] == "api" and parts[1] == "policy":
        if method == "PUT":
            return _put_policy(event)
        policy = _get_policy()
        policy["requester_is_admin"] = _is_admin(event)
        return _response(200, policy)

    # Expected: /api/{tab}
    if len(parts) != 2 or parts[0] != "api" or parts[1] not in ENDPOINTS:
        return _response(404, {"error": "unknown endpoint", "endpoints": sorted(ENDPOINTS)})
    tab = parts[1]

    params = event.get("queryStringParameters") or {}
    try:
        days = int(params.get("days", "30"))
    except ValueError:
        return _response(400, {"error": "days must be an integer"})
    if days not in ALLOWED_DAYS:
        return _response(400, {"error": f"days must be one of {sorted(ALLOWED_DAYS)}"})

    try:
        payload = _handle_tab(tab, days)
    except Exception as exc:  # noqa: BLE001
        logger.exception("unhandled error for tab %s", tab)
        return _response(500, {"error": "internal error"})

    # Column-level authorization: raw prompt text is investigator-only.
    # Non-admins still see the audit rows (who/when/which keyword) but the
    # prompt_text column is masked server-side — never rely on UI hiding.
    if tab == "security" and not _is_admin(event):
        payload = _redact_prompt_text(payload)

    return _response(200, payload)


def _redact_prompt_text(payload: dict) -> dict:
    """Mask the prompt_text column in security_audit_trail for non-admins.
    Returns a shallow-copied payload so cached objects stay unredacted for
    admin requests served from the same warm container."""
    audit = (payload.get("results") or {}).get("security_audit_trail")
    if not audit or "prompt_text" not in audit.get("columns", []):
        return payload
    idx = audit["columns"].index("prompt_text")
    masked_rows = [
        row[:idx] + ["[admins only]"] + row[idx + 1:]
        for row in audit["rows"]
    ]
    redacted = dict(payload)
    redacted["results"] = dict(payload["results"])
    redacted["results"]["security_audit_trail"] = {
        "columns": audit["columns"],
        "rows": masked_rows,
    }
    redacted["prompt_text_redacted"] = True
    return redacted
