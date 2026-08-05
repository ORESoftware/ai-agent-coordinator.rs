from __future__ import annotations

from pathlib import Path

publisher = Path("scripts/publish_shared_auth_client_artifacts.sh")
text = publisher.read_text()
old = '''stage='validate-owner-identity'
owner_login="$(GH_TOKEN="$owner_token" gh api user --jq '.login' 2>/dev/null)"
test "$owner_login" = "$expected_owner"
membership="$(
  GH_TOKEN="$owner_token" gh api "/user/memberships/orgs/shared-auth" \\
    --jq '.role + ":" + .state' 2>/dev/null
)"
test "$membership" = 'admin:active'
'''
new = '''stage='validate-owner-login'
owner_login="$(GH_TOKEN="$owner_token" gh api user --jq '.login' 2>/dev/null)"
test "$owner_login" = "$expected_owner"

stage='validate-shared-auth-membership'
membership="$(
  GH_TOKEN="$owner_token" gh api "/user/memberships/orgs/shared-auth" \\
    --jq '.role + ":" + .state' 2>/dev/null
)"
test "$membership" = 'admin:active'
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected one owner identity block, found {count}")
publisher.write_text(text.replace(old, new, 1))

for temporary in [
    Path("scripts/patch_shared_auth_owner_identity_stages.py"),
    Path(".github/workflows/patch-shared-auth-owner-identity-stages.yml"),
]:
    if temporary.exists():
        temporary.unlink()
