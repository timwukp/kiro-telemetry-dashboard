#!/usr/bin/env bash
# =====================================================================
# kiro-policy-sync — pull the org's Kiro policy from the dashboard API
# and install it via Kiro's officially supported file mechanism:
#   ~/.kiro/settings/mcp.json   (user-level MCP allowlist)
#   ~/.kiro/steering/*.md       (global steering files)
#
# Run manually, from cron/launchd, or distribute via MDM. Requires an
# authenticated dashboard session token (Cognito). Honest limits: Kiro
# has no admin API; a workspace-level .kiro/settings/mcp.json still
# overrides the user level — telemetry audit is the compensating control.
#
# Usage:
#   KTD_URL=https://<dashboard-domain> KTD_TOKEN=<access-token> ./kiro-policy-sync.sh
#   ./kiro-policy-sync.sh --dry-run     # show what would change
# =====================================================================
set -euo pipefail

KTD_URL=${KTD_URL:?set KTD_URL to the dashboard origin (https://...)}
KTD_TOKEN=${KTD_TOKEN:?set KTD_TOKEN to a valid Cognito access token}
DRY_RUN=${1:-}

KIRO_DIR="$HOME/.kiro"
MCP_FILE="$KIRO_DIR/settings/mcp.json"
STEER_DIR="$KIRO_DIR/steering"
MANAGED_MARK=".ktd-managed"      # steering files we own carry this suffix list

policy=$(curl -fsS -H "authorization: Bearer $KTD_TOKEN" "$KTD_URL/api/policy")
version=$(printf '%s' "$policy" | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])")

if [[ "$version" == "0" ]]; then
  echo "No policy published yet (v0) — nothing to install."
  exit 0
fi

echo "Fetched policy v$version"

# ---- mcp.json ----
new_mcp=$(printf '%s' "$policy" | python3 -c "
import json, sys
p = json.load(sys.stdin)
print(json.dumps({'mcpServers': p.get('mcp_allowlist', {})}, indent=2))
")
if [[ "$DRY_RUN" == "--dry-run" ]]; then
  echo "--- would write $MCP_FILE ---"; printf '%s\n' "$new_mcp"
else
  mkdir -p "$(dirname "$MCP_FILE")"
  if [[ -f "$MCP_FILE" ]] && ! cmp -s <(printf '%s\n' "$new_mcp") "$MCP_FILE"; then
    cp "$MCP_FILE" "$MCP_FILE.bak.$(date +%Y%m%d%H%M%S)"
    echo "Backed up existing mcp.json"
  fi
  printf '%s\n' "$new_mcp" > "$MCP_FILE"
  echo "Installed $MCP_FILE"
fi

# ---- steering files ----
printf '%s' "$policy" | python3 -c "
import json, os, sys
p = json.load(sys.stdin)
steer_dir = os.path.expanduser('$STEER_DIR')
manifest_path = os.path.join(steer_dir, '$MANAGED_MARK')
dry = '$DRY_RUN' == '--dry-run'
os.makedirs(steer_dir, exist_ok=True)

# remove files we previously managed but that were deleted from the policy
old = set()
if os.path.exists(manifest_path):
    old = set(open(manifest_path).read().split())
new_names = set()
for f in p.get('steering_files', []):
    name = os.path.basename(f['name'])          # defense in depth
    if not name.endswith('.md'):
        continue
    new_names.add(name)
    path = os.path.join(steer_dir, name)
    if dry:
        print(f'--- would write {path} ({len(f[\"content_md\"])} bytes)')
    else:
        open(path, 'w').write(f['content_md'])
        print(f'Installed {path}')
for stale in old - new_names:
    path = os.path.join(steer_dir, stale)
    if os.path.exists(path):
        if dry:
            print(f'--- would remove stale managed file {path}')
        else:
            os.remove(path)
            print(f'Removed stale {path}')
if not dry:
    open(manifest_path, 'w').write('\n'.join(sorted(new_names)))
"
echo "Done (policy v$version)."
