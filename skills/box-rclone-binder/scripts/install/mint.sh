#!/usr/bin/env sh
# ccg-mint fallback: mint a Box access token via client_credentials and inject it BY REFERENCE.
# Secrets are read from the environment (injected from the secret backend), NEVER passed on argv.
# The token is written to a chmod-600 @file and handed to rclone as token=@file (not on argv).
set -eu

: "${BOX_BINDER_CLIENT_ID:?set from secret backend}"
: "${BOX_BINDER_CLIENT_SECRET:?set from secret backend}"
: "${BOX_BINDER_ENTERPRISE_ID:?set from secret backend}"
TOKEN_URL="${BOX_TOKEN_URL:-https://api.box.com/oauth2/token}"
TOKENFILE="${BOX_BINDER_TOKENFILE:-/etc/box-binder/access.json}"
REMOTE="${BOX_BINDER_REMOTE:-box}"

umask 077
tmp="$(mktemp "$(dirname "$TOKENFILE")/.bbtok.XXXXXX")"

# --data-urlencode keeps the secret out of the URL/argv of any child process.
curl -fsS -X POST "$TOKEN_URL" \
  --data-urlencode "grant_type=client_credentials" \
  --data-urlencode "client_id=${BOX_BINDER_CLIENT_ID}" \
  --data-urlencode "client_secret=${BOX_BINDER_CLIENT_SECRET}" \
  --data-urlencode "box_subject_type=enterprise" \
  --data-urlencode "box_subject_id=${BOX_BINDER_ENTERPRISE_ID}" \
  -o "$tmp"

chmod 600 "$tmp"
mv -f "$tmp" "$TOKENFILE"       # atomic, same dir
sync

# Inject by reference; @file form keeps the token out of argv/ps/history.
rclone config update "$REMOTE" "token=@${TOKENFILE}" --non-interactive >/dev/null
