# Testing

## Offline (no AWS needed)

```bash
python3 scripts/validate_offline.py   # 40+ checks: CFN security lint, SQL
                                      # placeholders, frontend consistency,
                                      # XSS/token-storage rules, deploy.sh syntax
python3 -m unittest discover tests    # unit tests: handler auth/routing/caching,
                                      # policy write authorization, query
                                      # whitelist integrity, SVG geometry
```

The SVG geometry test (`tests/test_arch_geometry.py`) parses the
architecture diagram in `frontend/arch.js` and asserts no connector crosses
another and none passes through an unrelated node — regenerate the diagram
freely; the test keeps the layout honest.

## Live smoke test

`scripts/deploy.sh` ends with one automatically:

- `GET /` through CloudFront → 200
- direct `execute-api` call → 401/403 (blocked without CloudFront + JWT)
- unauthenticated `/api/*` through CloudFront → 401 (JWT required)

## Real-browser e2e (recommended after UI changes)

API-level tests cannot see rendering bugs (a CSS rule once kept the login
card painted over the dashboard after successful auth — every offline check
passed). Walk the real flow with Puppeteer + headless Chrome:

1. Create a disposable Cognito user:

   ```bash
   aws cognito-idp admin-create-user --user-pool-id <pool> \
     --username e2e@example.com --message-action SUPPRESS \
     --user-attributes Name=email,Value=e2e@example.com Name=email_verified,Value=true
   aws cognito-idp admin-set-user-password --user-pool-id <pool> \
     --username e2e@example.com --password '<strong-pw>' --permanent
   # add to the admins group only if testing policy writes
   ```

2. Script the flow: open the site → click "Sign in with Cognito" → fill the
   Hosted UI (`input[name="username"]` / `input[name="password"]` — the
   classic UI renders duplicate desktop+mobile forms; use the visible one)
   → wait for redirect → assert `#app-view` is visible and `#panel` has
   children → click through each tab, screenshot, eyeball.

3. Delete the disposable user afterwards.

Expected timings: cached tabs ~1–2 s; a cold tab right after deployment can
take up to a warmer cycle (15 min) to become instant.
