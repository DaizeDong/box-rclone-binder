# Anti-patterns (box-binder hard-rejects or refuses these)

1. **Shared rotating refresh_token across hosts.** Copying an OAuth `rclone.conf` to N machines;
   the first to refresh breaks the rest with `invalid_grant`. -> `assert_no_shared_refresh_token`
   hard-fails (signal S8). Use server auth, or oauth-broker (single master).
2. **OAuth for unattended multi-host.** 60-day activity window + browser reconnect is not
   automatable. Default to jwt/ccg.
3. **Assuming `--box-client-credentials` just works.** It may omit `box_subject_id` -> HTTP 400.
   Prove CCG with `doctor`, else use ccg-mint.
4. **`rclone about box:` for health.** Box does not support it. Use `lsd`/`lsjson` with timeouts.
5. **Token/secret on argv or in a connection string.** Leaks via `ps`/history/argv. Use `@file`
   or env-var injection (`mint.sh` and the broker both do `token=@file`).
6. **Old rclone + JWT on a >1h upload.** Second-cycle re-mint could fatal (#7214). Pin a recent
   rclone (>= #8313 fix) and cover >2 token lifetimes in health probing.
7. **Cross-fs `rename`, or rename without fsync.** Non-atomic / can leave half a JSON on power
   loss. Temp must be same-volume; fsync file + dir (signals S9).
8. **Hardcoding `config.json`/private key/secret/password into the repo or an image layer; `-vv`
   or `rclone config dump` to a log.** All leak tokens. gitignored + scrubbed + never `-vv`.
9. **Missing `--ask-password=false`** on an encrypted conf -> silent hang waiting for input.
10. **Writing/deleting LIVE Box content to "test".** Binding + health are read-only probes only.
11. **Self-heal that deletes and recreates the remote.** Amplifies the blast radius. Re-mint ->
    re-probe -> INFO/CRITICAL instead.
12. **All hosts probing/refreshing at the same instant** -> 429. Add jitter + backoff.
