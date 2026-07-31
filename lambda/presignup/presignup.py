"""
Kiro Telemetry Dashboard — Cognito PreSignUp trigger.

Self-signup is open ONLY to the corporate email domain(s) in
ALLOWED_EMAIL_DOMAINS (comma-separated, e.g. "amazon.com"). Everyone else
is rejected before a user record is ever created. Cognito's email
verification code remains the proof-of-mailbox-ownership: you cannot sign
up with a colleague's address unless you can read their inbox.

Admin-created users (admin_create_user) do not pass through this trigger,
so the operator can still add external reviewers deliberately.
"""

import os

ALLOWED = [d.strip().lower() for d in
           os.environ.get("ALLOWED_EMAIL_DOMAINS", "amazon.com").split(",") if d.strip()]


def lambda_handler(event, context):
    email = (event.get("request", {}).get("userAttributes", {}) or {}).get("email", "")
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""

    if domain not in ALLOWED:
        # Message surfaces on the hosted UI signup form.
        raise Exception(  # noqa: TRY002 — Cognito shows str(exception) to the user
            f"Self-signup is limited to corporate email domains ({', '.join(ALLOWED)}). "
            "Contact the dashboard admin for access."
        )

    # Domain OK: let the normal email-verification flow proceed.
    # (Do NOT auto-confirm/auto-verify — the emailed code is the mailbox proof.)
    return event
