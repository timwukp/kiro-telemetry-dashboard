#!/usr/bin/env python3
"""Offline validation for the Kiro Telemetry Dashboard — no AWS access needed.

Checks:
  1. CloudFormation templates parse as YAML and pass structural + security lint.
  2. SQL file placeholders are exactly the documented set.
  3. Lambda handler imports and unit tests pass.
  4. Frontend consistency: endpoints used by app.js exist in queries.py;
     query keys referenced by renderers exist; no forbidden browser APIs.
  5. deploy.sh passes bash -n syntax check.

Exit 0 = all pass. Any FAIL prints and exits 1.
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def check(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------- 1. CFN
import yaml


class CfnLoader(yaml.SafeLoader):
    pass


def _unknown(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


CfnLoader.add_multi_constructor("!", _unknown)

for template in ("cloudformation/20_backend.yaml", "cloudformation/30_frontend.yaml"):
    path = os.path.join(ROOT, template)
    try:
        doc = yaml.load(open(path), Loader=CfnLoader)
        check(f"{template}: parses", True)
    except Exception as exc:
        check(f"{template}: parses", False, str(exc))
        continue

    resources = doc.get("Resources", {})
    check(f"{template}: has resources", bool(resources))

    text = open(path).read()
    # security lint
    if "20_backend" in template:
        pool = resources.get("UserPool", {}).get("Properties", {})
        # Self-signup is allowed ONLY behind the domain-allowlist PreSignUp
        # trigger: the flag must be the conditional !If on SelfSignupEnabled
        # (never a bare `false`), and the trigger + permission must exist.
        signup_flag = pool.get("AdminCreateUserConfig", {}).get("AllowAdminCreateUserOnly")
        conditional = isinstance(signup_flag, (list, str)) and "SelfSignupEnabled" in str(signup_flag)
        check("backend: self-signup gated by domain condition",
              signup_flag is True or conditional, f"flag={signup_flag!r}")
        check("backend: presignup domain trigger defined",
              "PreSignupFunction" in resources
              and resources["PreSignupFunction"].get("Condition") == "SelfSignupEnabled"
              and "ALLOWED_EMAIL_DOMAINS" in str(resources["PreSignupFunction"]))
        check("backend: SPA client has no secret", "GenerateSecret: false" in text)
        check("backend: PKCE code flow only", re.search(r"AllowedOAuthFlows:\s*\[code\]", text) is not None)
        check("backend: JWT authorizer on route", "AuthorizationType: JWT" in text)
        check("backend: throttling configured", "ThrottlingRateLimit" in text)
        check("backend: advanced security enforced", "AdvancedSecurityMode: ENFORCED" in text)
        check("backend: reserved concurrency cap", "ReservedConcurrentExecutions" in text)
        check("backend: no wildcard Action in IAM", not re.search(r"Action:\s*['\"]?\*", text))
        # every IAM statement should scope resources; the only '*' allowed is
        # in an arn suffix (table/db/*, log-group:...*), never a bare '*'
        bare_star = re.search(r"Resource:\s*['\"]?\*['\"]?\s*$", text, re.M)
        check("backend: no bare Resource '*'", bare_star is None)
    if "30_frontend" in template:
        check("frontend: S3 public access blocked", "BlockPublicPolicy: true" in text)
        check("frontend: OAC signing always", "SigningBehavior: always" in text)
        check("frontend: HTTPS-only origin", "OriginProtocolPolicy: https-only" in text)
        check("frontend: HSTS present", "StrictTransportSecurity" in text)
        check("frontend: CSP present", "ContentSecurityPolicy" in text)
        check("frontend: TLS 1.2 minimum", "TLSv1.2_2021" in text)
        check("frontend: origin-verify header wired", "x-origin-verify" in text)
        check("frontend: deny insecure transport", "aws:SecureTransport" in text)

# ---------------------------------------------------------------- 2. SQL
sql_path = os.path.join(ROOT, "sql/10_enriched_dependencies.sql")
sql = open(sql_path).read()
placeholders = set(re.findall(r"\$\{(\w+)\}", sql))
expected = {"DATABASE", "LOG_BUCKET", "MAPPING_PREFIX", "IDENTITY_TABLE"}
check("sql: placeholders exactly as documented", placeholders == expected,
      f"got {placeholders}")
check("sql: enriched view defined", "CREATE OR REPLACE VIEW" in sql and "v_user_activity_enriched" in sql)
check("sql: user_project table defined", "CREATE EXTERNAL TABLE IF NOT EXISTS" in sql)
statements = [s.strip() for s in
              "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--")).split(";")
              if s.strip()]
check("sql: exactly 2 statements", len(statements) == 2, f"got {len(statements)}")

# ---------------------------------------------------------------- 3. tests
result = subprocess.run(
    [sys.executable, "-m", "unittest", "discover", "tests"],
    cwd=ROOT, capture_output=True, text=True,
)
check("lambda: unit tests pass", result.returncode == 0, result.stderr[-400:])

# ---------------------------------------------------------------- 4. frontend
sys.path.insert(0, os.path.join(ROOT, "lambda", "api"))
import queries  # noqa: E402

app_js = open(os.path.join(ROOT, "frontend/app.js")).read()
auth_js = open(os.path.join(ROOT, "frontend/auth.js")).read()
charts_js = open(os.path.join(ROOT, "frontend/charts.js")).read()
index_html = open(os.path.join(ROOT, "frontend/index.html")).read()

# renderers live inside `const RENDERERS = { ... };` — scope the scan there
_rblock = re.search(r"const RENDERERS = \{(.*?)\n  \};", app_js, re.S)
renderer_tabs = set(re.findall(r"^\s{4}(\w+)\((?:data)?\)", _rblock.group(1) if _rblock else "", re.M))
# architecture + policy use special (non-days) endpoints handled in-handler
SPECIAL_TABS = {"architecture", "policy", "intro"}
check("frontend: renderer per endpoint",
      renderer_tabs == set(queries.ENDPOINTS) | SPECIAL_TABS,
      f"renderers {renderer_tabs} vs endpoints {set(queries.ENDPOINTS)} + {SPECIAL_TABS}")
check("frontend: special tabs wired in index.html",
      all(f'data-tab="{t}"' in index_html for t in SPECIAL_TABS | set(queries.ENDPOINTS)))

used_keys = set(re.findall(r"rowsOf\(data,\s*'(\w+)'\)", app_js))
known_keys = set(queries.QUERIES)
check("frontend: every rowsOf key exists in queries.py", used_keys <= known_keys,
      f"unknown: {used_keys - known_keys}")
unused = {k for tab in queries.ENDPOINTS.values() for k in tab} - used_keys
check("frontend: every backend query is rendered", not unused, f"unrendered: {unused}")

# localStorage may hold ONLY the one-time PKCE flow keys (Safari drops
# sessionStorage across the Hosted-UI redirect); tokens must never touch it.
# Enforce semantically: every localStorage call site must be inside flowStore,
# and flowStore must only ever receive the two flow keys.
ls_calls = re.findall(r"localStorage\.(\w+)\(([^)]*)\)", auth_js)
check("frontend: localStorage only via flowStore (setItem/getItem/removeItem on k)",
      all(args.split(",")[0].strip() == "k" for _m, args in ls_calls),
      f"call sites: {ls_calls}")
flow_keys = set(re.findall(r"flowStore\.set\((\w+)", auth_js))
check("frontend: flowStore holds only PKCE flow keys",
      flow_keys <= {"VERIFIER_KEY", "STATE_KEY"}, f"keys: {flow_keys}")
check("frontend: tokens written to sessionStorage only",
      auth_js.count("setItem(TOKEN_KEY") >= 2
      and "localStorage.setItem(TOKEN_KEY" not in auth_js)
check("frontend: no localStorage in app.js/charts.js",
      "localStorage" not in app_js and "localStorage" not in charts_js)
check("frontend: PKCE S256", "'S256'" in auth_js or '"S256"' in auth_js)
check("frontend: state param verified", "state mismatch" in auth_js.lower() or "oauth state" in auth_js.lower())
check("frontend: code scrubbed from history", "history.replaceState" in auth_js)
# innerHTML is allowed ONLY for the tooltip sink in charts.js, and every
# data interpolation feeding it must be wrapped in esc(...) or fmt(...).
for fname, content in (("app.js", app_js), ("auth.js", auth_js)):
    check(f"frontend: no innerHTML in {fname}", "innerHTML" not in content)
tooltip_templates = re.findall(r"showTooltip\([^,]+,\s*`([^`]*)`", charts_js)
check("frontend: tooltip templates found", len(tooltip_templates) >= 3)
unescaped = [
    expr for tpl in tooltip_templates
    for expr in re.findall(r"\$\{([^}]+)\}", tpl)
    if not (expr.startswith("esc(") or expr.startswith("fmt(") or expr == "unit" or expr == "pct")
]
check("frontend: all tooltip interpolations escaped", not unescaped, f"raw: {unescaped}")
check("frontend: innerHTML only in showTooltip sink",
      charts_js.count("innerHTML") == 1 and "t.innerHTML = html" in charts_js)
check("frontend: no eval/Function anywhere",
      not re.search(r"\beval\(|new Function\(", app_js + auth_js + charts_js))
check("frontend: no inline scripts in index.html", "<script>" not in index_html)
check("frontend: no third-party script tags",
      not re.search(r'<script[^>]+src="(?!config\.js|auth\.js|charts\.js|arch\.js|intro\.js|policy\.js|app\.js)', index_html))

# tooltip innerHTML: only reachable with server-shaped strings; charts.table
# must use textContent for arbitrary data (audit trail prompt_text).
check("frontend: table cells use textContent", "td.textContent = " in charts_js.replace("cell ?? '—'", "cell"))

# ---------------------------------------------------------------- 5. deploy.sh
result = subprocess.run(["bash", "-n", os.path.join(ROOT, "scripts/deploy.sh")],
                        capture_output=True, text=True)
check("deploy.sh: bash syntax", result.returncode == 0, result.stderr)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("All offline checks passed.")
