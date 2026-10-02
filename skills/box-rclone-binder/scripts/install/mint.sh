#!/usr/bin/env sh
# Standalone CCG helper. Form bytes travel through a protected file and curl stdin.
# The access-only JSON is consumed by the runtime through rclone's environment.
set -eu
: "${BOX_BINDER_CLIENT_ID:?set from secret backend}"
: "${BOX_BINDER_CLIENT_SECRET:?set from secret backend}"
: "${BOX_BINDER_ENTERPRISE_ID:?set from secret backend}"
TOKEN_URL="${BOX_TOKEN_URL:-https://api.box.com/oauth2/token}"
TOKENFILE="${BOX_BINDER_TOKENFILE:-/etc/box-binder/access.json}"
umask 077
form="$(mktemp "$(dirname "$TOKENFILE")/.bbform.XXXXXX")"
tmp=""
trap 'rm -f "$form" "$tmp"' EXIT HUP INT TERM
tmp="$(mktemp "$(dirname "$TOKENFILE")/.bbtok.XXXXXX")"
python3 -c 'import os,sys,urllib.parse; sys.stdout.write(urllib.parse.urlencode({
"grant_type":"client_credentials", "client_id":os.environ["BOX_BINDER_CLIENT_ID"],
"client_secret":os.environ["BOX_BINDER_CLIENT_SECRET"],
"box_subject_type":os.environ.get("BOX_BINDER_SUB_TYPE","enterprise"),
"box_subject_id":os.environ["BOX_BINDER_ENTERPRISE_ID"]}))' > "$form"
if ! cat "$form" | curl -fsS --connect-timeout 15 --max-time 30 -X POST "$TOKEN_URL" \
    -H 'Content-Type: application/x-www-form-urlencoded' --data-binary @- -o "$tmp" 2>/dev/null; then
    printf '%s\n' 'Box token request failed' >&2
    exit 1
fi
if ! python3 - "$tmp" <<'PY'
import datetime, json, sys
path = sys.argv[1]
try:
    with open(path, encoding='utf-8') as stream:
        value = json.load(stream)
    token = value.get('access_token')
    seconds = value.get('expires_in')
    if not isinstance(token, str) or not token or isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
        raise ValueError
    expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    blob = {'access_token': token, 'token_type': value.get('token_type', 'bearer'), 'expiry': expiry.isoformat()}
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump(blob, stream)
except Exception:
    sys.exit(1)
PY
then
    printf '%s\n' 'Box token response was invalid' >&2
    exit 1
fi
chmod 600 "$tmp"
mv -f "$tmp" "$TOKENFILE"
sync
