# Security & Compliance

## Data protection

- Telemetry stays in your account: S3 (log bucket) → Athena → the dashboard.
  Nothing leaves except what a signed-in dashboard user views.
- Dashboard access requires Cognito sign-in; self-signup is restricted to the
  domains in `AllowedSignupDomains` (enforced by the presignup Lambda).
- `v_prompt_logs` truncates prompt text to 500 characters; raw logs in S3
  keep the full text — scope S3 read access accordingly.

## Handling sensitive content

- The `v_prompt_logs` view flags prompts containing `password`, `secret`,
  `credential`, `api_key`, `private_key`, `token` (`sensitive_flag`, and
  `keyword_triggered` with the matched keyword). This is **detection**, not
  prevention — pair it with a developer "prompt hygiene" policy.
- The daily scanner Lambda (`kiro-dashboard-scanner`, 02:30 UTC) publishes
  alerts; the Security tab charts them and `security_audit_trail` lists the
  individual flagged prompts.

## Sensitive-keyword review SOP

| Cadence | Action | Owner |
|---------|--------|-------|
| Initial | Define the baseline keyword list | Security |
| Monthly | Review false-positive rate; tune the list | Security + DevOps |
| Quarterly | Add domain/locale-specific terms | Compliance |
| On incident | Add new keywords discovered during an incident | SOC |

The keyword logic is centralized in the `v_prompt_logs` definition in
`sql/20_v2_tables.sql` — update the `CASE` expressions there and re-create
the view; the API queries, scanner, and charts all inherit the change.

When a flag needs investigation, start with runbook query **R2** in
`sql/40_governance_runbook.sql` (full interaction history for one user).

## IAM notes

- The API Lambda's Athena access is scoped to the `kiro-governance`
  workgroup, which enforces a per-query scan cap
  (`cloudformation/01_foundation.yaml`).
- `kiro-user-mapping-lambda-role` needs `identitystore:ListUsers` (not
  resource-scopable) plus `s3:PutObject` on the mapping prefix only
  (`cloudformation/02_identity_sync.yaml`).

## Reporting a vulnerability

Do not open public issues for security problems. Follow your organization's
internal vulnerability-reporting process.
