"""
Kiro Telemetry Dashboard — DORA GitHub sync.

Scheduled Lambda that pulls PR/review/CI data for the repos listed in the
policy registry (admin-editable via PUT /api/policy -> dora_repos) and
writes an overwrite snapshot of NDJSON rows to S3 for Athena.

Per-PR fields follow timwukp/dora-metrics-platform's PullRequest model:
created/merged/closed/first_commit/first_review/approved timestamps,
size stats, revert/hotfix classification, and AI-assistant detection
from Co-authored-by trailers (kiro / amazon-q / claude / copilot).

Environment:
    LOG_BUCKET        e.g. amazon-q-logging-...
    POLICY_PREFIX     kiro/policy/          (repo list source)
    DORA_PREFIX       kiro/dora/            (snapshot destination)
    GITHUB_TOKEN_PARAM  SSM SecureString name holding a read-only PAT
    LOOKBACK_DAYS     default 120 (covers the 90d dashboard window)

If the token parameter is missing the function logs and exits 0 so the
schedule stays healthy until a PAT is provisioned.
"""

import json
import logging
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LOG_BUCKET = os.environ["LOG_BUCKET"]
POLICY_PREFIX = os.environ.get("POLICY_PREFIX", "kiro/policy/")
DORA_PREFIX = os.environ.get("DORA_PREFIX", "kiro/dora/")
GITHUB_TOKEN_PARAM = os.environ.get("GITHUB_TOKEN_PARAM", "/kiro-dashboard/github-token")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "120"))
API = "https://api.github.com"
MAX_PAGES = 5          # per endpoint, 100/page — bounded like the platform's collector

_s3 = boto3.client("s3")
_ssm = boto3.client("ssm")

# --- ported from dora-metrics-platform pr_classifier.py (anchored regexes) ---
REVERT_RE = re.compile(r"^revert\b|^\s*revert\s*[\"“:]", re.I)
HOTFIX_RE = re.compile(r"^hotfix\b|/hotfix[-/]|^fix!:|^emergency\b", re.I)
AI_TRAILERS = (
    ("kiro", re.compile(r"co-authored-by:.*\bkiro\b", re.I)),
    ("amazon-q", re.compile(r"co-authored-by:.*amazon\s*q|co-authored-by:.*amazonq", re.I)),
    ("claude", re.compile(r"co-authored-by:.*\bclaude\b", re.I)),
    ("copilot", re.compile(r"co-authored-by:.*\bcopilot\b", re.I)),
)


def _gh(token: str, path: str):
    req = urllib.request.Request(
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "kiro-dashboard-dora-sync",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _detect_ai(messages) -> str:
    joined = "\n".join(messages).lower()
    for name, rx in AI_TRAILERS.items() if isinstance(AI_TRAILERS, dict) else AI_TRAILERS:
        if rx.search(joined):
            return name
    return "none"


def _get_repos() -> list:
    try:
        obj = _s3.get_object(Bucket=LOG_BUCKET, Key=POLICY_PREFIX.rstrip("/") + "/policy.json")
        policy = json.loads(obj["Body"].read())
        repos = policy.get("dora_repos", [])
        return [r for r in repos if re.fullmatch(r"[\w.-]+/[\w.-]+", r)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("no policy/dora_repos: %s", exc)
        return []


def _sync_repo(token: str, repo: str, cutoff: datetime) -> list:
    rows = []
    for page in range(1, MAX_PAGES + 1):
        prs = _gh(token, f"/repos/{repo}/pulls?state=all&sort=updated&direction=desc&per_page=100&page={page}")
        if not prs:
            break
        stop = False
        for pr in prs:
            updated = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00"))
            if updated < cutoff:
                stop = True
                break
            num = pr["number"]
            commits, reviews = [], []
            try:
                commits = _gh(token, f"/repos/{repo}/pulls/{num}/commits?per_page=100")
                reviews = _gh(token, f"/repos/{repo}/pulls/{num}/reviews?per_page=100")
            except urllib.error.HTTPError as e:
                logger.warning("%s#%s detail fetch failed: %s", repo, num, e)
            commit_dates = [c["commit"]["author"]["date"] for c in commits if c.get("commit", {}).get("author")]
            commit_msgs = [c["commit"]["message"] for c in commits]
            author_emails = [c["commit"]["author"].get("email", "") for c in commits
                             if c.get("commit", {}).get("author")]
            review_times = [r["submitted_at"] for r in reviews if r.get("submitted_at")]
            approvals = [r["submitted_at"] for r in reviews
                         if r.get("state") == "APPROVED" and r.get("submitted_at")]
            title_and_branch = f"{pr['title']}\n{pr['head'].get('ref', '')}"
            rows.append({
                "repo": repo,
                "number": num,
                "title": pr["title"][:200],
                "author": pr["user"]["login"] if pr.get("user") else "",
                "author_email": author_emails[0] if author_emails else "",
                "state": "merged" if pr.get("merged_at") else pr["state"],
                "base_ref": pr["base"].get("ref", ""),
                "created_at": pr["created_at"],
                "merged_at": pr.get("merged_at"),
                "closed_at": pr.get("closed_at"),
                "first_commit_at": min(commit_dates) if commit_dates else None,
                "first_review_at": min(review_times) if review_times else None,
                "approved_at": min(approvals) if approvals else None,
                "review_count": len(reviews),
                "approval_count": len(approvals),
                "commit_count": len(commits),
                "is_revert": bool(REVERT_RE.search(title_and_branch)),
                "is_hotfix": bool(HOTFIX_RE.search(title_and_branch)),
                "assisted_by": _detect_ai(commit_msgs),
            })
        if stop:
            break
    logger.info("%s: %d PRs", repo, len(rows))
    return rows


def lambda_handler(event, context):
    try:
        token = _ssm.get_parameter(Name=GITHUB_TOKEN_PARAM, WithDecryption=True)["Parameter"]["Value"]
    except _ssm.exceptions.ParameterNotFound:
        logger.warning("GitHub token parameter %s not set; skipping sync", GITHUB_TOKEN_PARAM)
        return {"synced": 0, "reason": "no token"}

    repos = _get_repos()
    if not repos:
        logger.warning("no dora_repos configured in the policy registry")
        return {"synced": 0, "reason": "no repos"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    all_rows = []
    for repo in repos:
        try:
            all_rows.extend(_sync_repo(token, repo, cutoff))
        except Exception as exc:  # noqa: BLE001 — one bad repo must not kill the sweep
            logger.error("repo %s failed: %s", repo, exc)

    body = "\n".join(json.dumps(r) for r in all_rows) + ("\n" if all_rows else "")
    _s3.put_object(
        Bucket=LOG_BUCKET,
        Key=DORA_PREFIX.rstrip("/") + "/pull_requests/snapshot.ndjson",
        Body=body.encode(),
        ContentType="application/x-ndjson",
    )
    logger.info("wrote %d PR rows for %d repos", len(all_rows), len(repos))
    return {"synced": len(all_rows), "repos": len(repos)}
