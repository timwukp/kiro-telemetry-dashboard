"""Regression tests for two metric-definition bugs.

1. The agentic-adoption share divided the user_report `auto_messages` column
   by `total_messages`. That column is Kiro's per-model message count for the
   Auto router, not "agent-automated messages", and is not a subset of
   total_messages, so the share rendered at 1572% on a live account.
2. DORA AI attribution read only Co-authored-by trailers, so PRs written in
   Kiro sessions (which leave no trailer) counted as unassisted.

Run with:  python3 -m unittest discover tests
"""

import json
import os
import pathlib
import re
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambda" / "api"))

os.environ.setdefault("DATABASE", "kiro_governance")
os.environ.setdefault("WORKGROUP", "kiro-governance")
os.environ.setdefault("ORIGIN_VERIFY_SECRET", "test-secret-0123456789abcdef0123456789abcdef")

import handler  # noqa: E402
import queries  # noqa: E402

DORA_SQL = (ROOT / "sql" / "30_dora.sql").read_text()
APP_JS = (ROOT / "frontend" / "app.js").read_text()


def sql(key):
    return " ".join(queries.build_sql(key, "kiro_governance", 30).split())


def view_body(name):
    """Text of one CREATE OR REPLACE VIEW statement in 30_dora.sql."""
    m = re.search(rf"CREATE OR REPLACE VIEW \$\{{DATABASE\}}\.{name} AS(.*?)(;|\Z)",
                  DORA_SQL, re.S)
    assert m, f"{name} not defined in sql/30_dora.sql"
    return " ".join(m.group(1).split())


class TestAgenticShare(unittest.TestCase):
    def test_stage3_does_not_divide_auto_model_count_by_total(self):
        q = sql("prod_agentic_kpis")
        self.assertNotIn("auto_messages", q)
        self.assertIn("client_type = 'KIRO_CLI'", q)
        self.assertEqual(queries.QUERIES["prod_agentic_kpis"][1],
                         ["cli_messages", "total_messages"])

    def test_cli_messages_is_a_subset_of_the_denominator(self):
        # numerator and denominator sum the same column over the same rows,
        # so the share is bounded by 100% by construction
        q = sql("prod_agentic_kpis")
        self.assertIn("THEN total_messages ELSE 0 END", q)
        self.assertIn("SUM(total_messages)", q)

    def test_auto_model_chart_is_a_count_not_a_share(self):
        self.assertNotIn("usage_auto_share_daily", queries.QUERIES)
        q = sql("usage_auto_model_messages_daily")
        self.assertIn("auto_messages", q)
        self.assertNotIn("total_messages", q)
        self.assertNotIn("100.0", q)
        self.assertIn("usage_auto_model_messages_daily", queries.ENDPOINTS["usage"])

    def test_frontend_no_longer_calls_it_agent_automated(self):
        self.assertNotIn("agent-automated", APP_JS)
        self.assertNotIn("usage_auto_share_daily", APP_JS)
        self.assertIn("Math.min(100", APP_JS)


class TestDoraAttribution(unittest.TestCase):
    DORA_KEYS = ("dora_kpis", "dora_prs_merged_daily", "dora_time_to_merge_daily",
                 "dora_by_repo", "dora_ai_share", "dora_ai_vs_speed", "dora_recent_prs")

    def test_dora_queries_read_the_attributed_view(self):
        for key in self.DORA_KEYS:
            q = sql(key)
            self.assertIn("v_dora_prs_attributed", q, key)
            self.assertNotIn("assisted_by", q, key)

    def test_view_combines_trailer_and_telemetry_evidence(self):
        v = view_body("v_dora_prs_attributed")
        for needle in ("'trailer'", "'kiro-telemetry'", "github_login",
                       "v_user_activity", "user_project", "total_messages > 0",
                       "COALESCE(d.first_commit_ts, d.created_ts)", "date(d.merged_ts)"):
            self.assertIn(needle, v)
        # trailer wins over telemetry, so an explicit tool name is never overwritten
        self.assertLess(v.index("WHEN d.assisted_by <> 'none' THEN 'trailer'"),
                        v.index("THEN 'kiro-telemetry'"))

    def test_view_scan_is_partition_bounded(self):
        v = view_body("v_dora_prs_attributed")
        self.assertRegex(v, r"a\.dt >= date_format\(date_add\('day', -120, current_date\)")

    def test_speed_comparison_shows_group_size(self):
        q = sql("dora_ai_vs_speed")
        self.assertIn("' (n='", q)
        self.assertIn("CAST(COUNT(*) AS varchar)", q)

    def test_recent_prs_expose_the_evidence_kind(self):
        self.assertEqual(queries.QUERIES["dora_recent_prs"][1][-2:], ["ai_tool", "ai_evidence"])
        self.assertIn("'Evidence'", APP_JS)


class TestGithubLoginMapping(unittest.TestCase):
    def _put(self, rows):
        s3 = mock.MagicMock()

        class NoSuchKey(Exception):
            pass
        s3.exceptions.NoSuchKey = NoSuchKey
        s3.get_object.side_effect = NoSuchKey()
        event = {
            "rawPath": "/api/policy",
            "requestContext": {"http": {"method": "PUT"},
                               "authorizer": {"jwt": {"claims": {
                                   "cognito:groups": "[admins]", "email": "admin@test"}}}},
            "headers": {"x-origin-verify": os.environ["ORIGIN_VERIFY_SECRET"]},
            "body": json.dumps({"mcp_allowlist": {}, "steering_files": [],
                                "org_mappings": {"rows": rows}}),
        }
        with mock.patch.object(handler, "_s3", s3):
            resp = handler.lambda_handler(event, None)
        csv = next((c.kwargs["Body"].decode() for c in s3.put_object.call_args_list
                    if c.kwargs["Key"].endswith("user-project.csv")), None)
        return resp, csv

    def test_csv_has_github_login_as_fifth_column(self):
        resp, csv = self._put([{"userid": "u-1", "team": "t", "project": "p",
                                "cost_center": "c", "github_login": "octo-cat"}])
        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(csv.splitlines()[0], "userid,team,project,cost_center,github_login")
        self.assertEqual(csv.splitlines()[1], "u-1,t,p,c,octo-cat")

    def test_github_login_is_optional(self):
        resp, csv = self._put([{"userid": "u-1", "team": "t", "project": "p", "cost_center": "c"}])
        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(csv.splitlines()[1], "u-1,t,p,c,")

    def test_invalid_github_login_rejected(self):
        for bad in ("-leading", "has space", "a" * 40, "x,y"):
            resp, _ = self._put([{"userid": "u-1", "team": "t", "project": "p",
                                  "cost_center": "c", "github_login": bad}])
            self.assertEqual(resp["statusCode"], 400, bad)

    def test_table_ddl_matches_csv_column_order(self):
        ddl = (ROOT / "sql" / "10_enriched_dependencies.sql").read_text()
        m = re.search(r"CREATE EXTERNAL TABLE \$\{DATABASE\}\.user_project \((.*?)\)", ddl, re.S)
        cols = [ln.split()[0] for ln in m.group(1).strip().splitlines()]
        self.assertEqual(tuple(cols), handler.MAPPING_COLUMNS)


if __name__ == "__main__":
    unittest.main()
