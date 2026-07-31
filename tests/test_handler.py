"""Unit tests for the API Lambda — run with:  python3 -m unittest discover tests
boto3 is mocked; no network or AWS credentials needed."""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda", "api"))

os.environ.setdefault("DATABASE", "kiro_governance")
os.environ.setdefault("WORKGROUP", "kiro-governance")
os.environ.setdefault("ORIGIN_VERIFY_SECRET", "test-secret-0123456789abcdef0123456789abcdef")

import handler  # noqa: E402
import queries  # noqa: E402

SECRET = os.environ["ORIGIN_VERIFY_SECRET"]


def make_event(path="/api/usage", days="30", secret=SECRET):
    event = {
        "rawPath": path,
        "headers": {"x-origin-verify": secret} if secret is not None else {},
        "queryStringParameters": {"days": days} if days is not None else None,
    }
    return event


class TestQueries(unittest.TestCase):
    def test_every_endpoint_query_exists(self):
        for tab, keys in queries.ENDPOINTS.items():
            for key in keys:
                self.assertIn(key, queries.QUERIES, f"{tab} references missing query {key}")

    def test_build_sql_renders_all(self):
        for key in queries.QUERIES:
            for days in sorted(queries.ALLOWED_DAYS):
                sql = queries.build_sql(key, "kiro_governance", days)
                self.assertNotIn("{db}", sql)
                self.assertNotIn("{days}", sql)
                self.assertIn("kiro_governance.", sql)

    def test_build_sql_rejects_bad_days(self):
        for bad in (0, 1, 31, 365, -7):
            with self.assertRaises(ValueError):
                queries.build_sql("usage_daily_credits", "kiro_governance", bad)

    def test_build_sql_rejects_injection_database(self):
        with self.assertRaises(ValueError):
            queries.build_sql("usage_daily_credits", "db; DROP TABLE x", 30)

    def test_no_select_star_and_all_windowed(self):
        """Cost guardrails: no SELECT *, every query has a time predicate —
        either the client-selected {days} window or a calendar-month window
        (month-to-date budget queries)."""
        for key, (template, _cols) in queries.QUERIES.items():
            self.assertNotIn("SELECT *", template.upper().replace("COUNT(*)", ""), key)
            windowed = ("date_add('day', -{days}" in template
                        or "date_trunc('month', current_date)" in template)
            self.assertTrue(windowed, f"{key} lacks a time-window predicate")

    def test_audit_trail_row_capped(self):
        template, _ = queries.QUERIES["security_audit_trail"]
        self.assertIn("LIMIT 200", template)


class TestHandlerAuth(unittest.TestCase):
    def test_missing_origin_header_rejected(self):
        resp = handler.lambda_handler(make_event(secret=None), None)
        self.assertEqual(resp["statusCode"], 403)

    def test_wrong_origin_secret_rejected(self):
        resp = handler.lambda_handler(make_event(secret="x" * 64), None)
        self.assertEqual(resp["statusCode"], 403)

    def test_unknown_endpoint_404(self):
        resp = handler.lambda_handler(make_event(path="/api/etc-passwd"), None)
        self.assertEqual(resp["statusCode"], 404)

    def test_nested_path_404(self):
        resp = handler.lambda_handler(make_event(path="/api/usage/../admin"), None)
        self.assertEqual(resp["statusCode"], 404)

    def test_bad_days_400(self):
        for bad in ("14", "abc", "-1", "9999999"):
            resp = handler.lambda_handler(make_event(days=bad), None)
            self.assertEqual(resp["statusCode"], 400, bad)

    def test_default_days_is_30(self):
        with mock.patch.object(handler, "_handle_tab", return_value={"days": 30, "results": {}}) as m:
            resp = handler.lambda_handler(make_event(days=None), None)
        self.assertEqual(resp["statusCode"], 200)
        m.assert_called_once_with("usage", 30)


class TestPolicyRoutes(unittest.TestCase):
    def _event(self, method="GET", body=None, groups=None, path="/api/policy"):
        event = make_event(path=path)
        event["requestContext"] = {"http": {"method": method}}
        if groups is not None:
            event["requestContext"]["authorizer"] = {
                "jwt": {"claims": {"cognito:groups": groups, "email": "admin@test"}}
            }
        if body is not None:
            event["body"] = json.dumps(body)
        return event

    def test_policy_get_returns_default_when_missing(self):
        s3 = mock.MagicMock()
        class NoSuchKey(Exception):
            pass
        s3.exceptions.NoSuchKey = NoSuchKey
        s3.get_object.side_effect = NoSuchKey()
        with mock.patch.object(handler, "_s3", s3):
            resp = handler.lambda_handler(self._event(), None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["mcp_allowlist"], {})
        self.assertFalse(body["requester_is_admin"])

    def test_policy_put_requires_admin_group(self):
        resp = handler.lambda_handler(
            self._event(method="PUT", body={"mcp_allowlist": {}}), None)
        self.assertEqual(resp["statusCode"], 403)
        resp = handler.lambda_handler(
            self._event(method="PUT", body={"mcp_allowlist": {}}, groups="[users]"), None)
        self.assertEqual(resp["statusCode"], 403)

    def test_policy_put_admin_writes_with_audit(self):
        s3 = mock.MagicMock()
        class NoSuchKey(Exception):
            pass
        s3.exceptions.NoSuchKey = NoSuchKey
        s3.get_object.side_effect = NoSuchKey()
        payload = {"mcp_allowlist": {"aws-docs": {"command": "uvx", "args": ["awslabs.aws-documentation-mcp-server"]}},
                   "steering_files": [{"name": "security.md", "content_md": "# Rules"}]}
        with mock.patch.object(handler, "_s3", s3):
            resp = handler.lambda_handler(
                self._event(method="PUT", body=payload, groups="[admins]"), None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["version"], 1)
        self.assertEqual(body["updated_by"], "admin@test")
        # two writes: policy.json + the materialized user-project.csv
        keys = [c.kwargs["Key"] for c in s3.put_object.call_args_list]
        self.assertIn("kiro/policy/policy.json", keys)
        self.assertTrue(any(k.endswith("user-project/user-project.csv") for k in keys))

    def test_policy_put_rejects_csv_injection_in_mappings(self):
        payload = {"mcp_allowlist": {}, "steering_files": [],
                   "org_mappings": {"rows": [
                       {"userid": "u1", "team": 'evil",team', "project": "p", "cost_center": "c"}]}}
        resp = handler.lambda_handler(
            self._event(method="PUT", body=payload, groups="[admins]"), None)
        self.assertEqual(resp["statusCode"], 400)

    def test_policy_put_writes_mapping_csv(self):
        s3 = mock.MagicMock()
        class NoSuchKey(Exception):
            pass
        s3.exceptions.NoSuchKey = NoSuchKey
        s3.get_object.side_effect = NoSuchKey()
        payload = {"mcp_allowlist": {}, "steering_files": [],
                   "org_mappings": {"rows": [
                       {"userid": "u-1", "team": "Platform Engineering",
                        "project": "kiro-telemetry-dashboard", "cost_center": "CC-4501"}]}}
        with mock.patch.object(handler, "_s3", s3):
            resp = handler.lambda_handler(
                self._event(method="PUT", body=payload, groups="[admins]"), None)
        self.assertEqual(resp["statusCode"], 200)
        csv_call = next(c for c in s3.put_object.call_args_list
                        if c.kwargs["Key"].endswith("user-project.csv"))
        body = csv_call.kwargs["Body"].decode()
        self.assertIn("u-1,Platform Engineering,kiro-telemetry-dashboard,CC-4501", body)

    def test_policy_put_rejects_path_traversal_steering_name(self):
        payload = {"mcp_allowlist": {},
                   "steering_files": [{"name": "../evil.md", "content_md": "x"}]}
        resp = handler.lambda_handler(
            self._event(method="PUT", body=payload, groups="[admins]"), None)
        self.assertEqual(resp["statusCode"], 400)

    def test_policy_put_rejects_oversize(self):
        event = self._event(method="PUT", groups="[admins]")
        event["body"] = "x" * (handler.MAX_POLICY_BYTES + 1)
        resp = handler.lambda_handler(event, None)
        self.assertEqual(resp["statusCode"], 413)


class TestPromptTextRedaction(unittest.TestCase):
    PAYLOAD = {
        "days": 30,
        "results": {
            "security_audit_trail": {
                "columns": ["log_date", "username", "keyword_triggered", "after_hours_flag", "prompt_text"],
                "rows": [["2026-07-31", "alice", "token", "0", "my gitlab token rotation chat"]],
            },
            "security_keyword_breakdown": {"columns": ["k", "v"], "rows": [["token", "3"]]},
        },
    }

    def test_non_admin_gets_masked_prompt_text(self):
        with mock.patch.object(handler, "_handle_tab", return_value=self.PAYLOAD):
            event = make_event(path="/api/security")
            resp = handler.lambda_handler(event, None)
        body = json.loads(resp["body"])
        row = body["results"]["security_audit_trail"]["rows"][0]
        self.assertEqual(row[4], "[admins only]")
        self.assertTrue(body["prompt_text_redacted"])
        # other panels untouched
        self.assertEqual(body["results"]["security_keyword_breakdown"]["rows"], [["token", "3"]])

    def test_admin_sees_full_prompt_text(self):
        with mock.patch.object(handler, "_handle_tab", return_value=self.PAYLOAD):
            event = make_event(path="/api/security")
            event["requestContext"] = {
                "http": {"method": "GET"},
                "authorizer": {"jwt": {"claims": {"cognito:groups": "[admins]"}}},
            }
            resp = handler.lambda_handler(event, None)
        body = json.loads(resp["body"])
        row = body["results"]["security_audit_trail"]["rows"][0]
        self.assertIn("gitlab token", row[4])
        self.assertNotIn("prompt_text_redacted", body)

    def test_redaction_does_not_mutate_cached_payload(self):
        with mock.patch.object(handler, "_handle_tab", return_value=self.PAYLOAD):
            handler.lambda_handler(make_event(path="/api/security"), None)
        # original object (as cached) must be untouched for admin reuse
        self.assertIn("gitlab token",
                      self.PAYLOAD["results"]["security_audit_trail"]["rows"][0][4])


class TestHandlerExecution(unittest.TestCase):
    def setUp(self):
        handler._cache.clear()

    def _athena_mock(self, fail_keys=()):
        athena = mock.MagicMock()
        counter = {"n": 0}

        def start(QueryString, WorkGroup, QueryExecutionContext):
            counter["n"] += 1
            qid = f"q{counter['n']}"
            failing = any(f"{k}:" not in QueryString and False for k in fail_keys)
            return {"QueryExecutionId": qid}

        athena.start_query_execution.side_effect = start
        athena.get_query_execution.return_value = {
            "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
        }
        page = {
            "ResultSet": {
                "ResultSetMetadata": {"ColumnInfo": [{"Name": "d"}, {"Name": "v"}]},
                "Rows": [
                    {"Data": [{"VarCharValue": "d"}, {"VarCharValue": "v"}]},   # header row
                    {"Data": [{"VarCharValue": "2026-07-01"}, {"VarCharValue": "42"}]},
                ],
            }
        }
        paginator = mock.MagicMock()
        paginator.paginate.return_value = [page]
        athena.get_paginator.return_value = paginator
        return athena

    def test_success_path_strips_header_row(self):
        with mock.patch.object(handler, "_athena", self._athena_mock()):
            resp = handler.lambda_handler(make_event(path="/api/usage"), None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["days"], 30)
        for key in queries.ENDPOINTS["usage"]:
            self.assertIn(key, body["results"])
            self.assertEqual(body["results"][key]["rows"], [["2026-07-01", "42"]])

    def test_response_is_cached(self):
        athena = self._athena_mock()
        with mock.patch.object(handler, "_athena", athena):
            handler.lambda_handler(make_event(path="/api/usage"), None)
            first_calls = athena.start_query_execution.call_count
            handler.lambda_handler(make_event(path="/api/usage"), None)
        self.assertEqual(athena.start_query_execution.call_count, first_calls)

    def test_partial_failure_degrades_not_500(self):
        athena = self._athena_mock()
        athena.get_query_execution.side_effect = [
            {"QueryExecution": {"Status": {"State": "FAILED", "StateChangeReason": "boom"}}},
        ] + [{"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}] * 50
        with mock.patch.object(handler, "_athena", athena):
            resp = handler.lambda_handler(make_event(path="/api/usage"), None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertTrue(body.get("errors"))
        # failed responses must NOT be cached
        self.assertEqual(len(handler._cache), 0)

    def test_no_client_input_reaches_sql(self):
        """The SQL sent to Athena must contain no client-supplied string."""
        athena = self._athena_mock()
        evil = make_event(path="/api/usage", days="30")
        evil["queryStringParameters"]["days"] = "30"
        evil["headers"]["x-evil"] = "'; DROP VIEW v_user_activity; --"
        with mock.patch.object(handler, "_athena", athena):
            handler.lambda_handler(evil, None)
        for call in athena.start_query_execution.call_args_list:
            self.assertNotIn("DROP", call.kwargs["QueryString"])


if __name__ == "__main__":
    unittest.main()
