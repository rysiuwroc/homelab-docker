#!/usr/bin/with-contenv bash
# Bazarr has no compose/env var for `general.concurrent_jobs`; it only lives in config.yaml
# inside the bazarr_config volume. Left at the LSIO default (4), Bazarr can run multiple
# ffsubsync/search jobs in parallel against the shared RAID0 media disk (mounted over CIFS
# from HomeServer .69) - this saturated the array (queue depth 20+, 180-320ms read latency)
# and stalled Plex/Jellyfin playback (2026-08-25 incident).
#
# LSIO runs every /custom-cont-init.d/*.sh on EVERY container start (not just first boot), so
# the file patch below self-heals a *restart* after some other process reset config.yaml.
#
# On a genuinely fresh volume (first-ever boot) config.yaml does not exist yet when this
# script runs, so there is nothing to patch here - and even if we raced to create it,
# Bazarr reads settings into memory once at startup, so a file edit made after that point
# would not affect the already-running process. A background watcher below instead enforces
# the setting through Bazarr's own /api/system/settings endpoint (the same code path the
# Settings UI's Save button uses) once the app is actually listening, which updates the
# live in-memory config, not just the file.
CONFIG=/config/config/config.yaml

if [ -f "$CONFIG" ] && grep -q '^  concurrent_jobs:' "$CONFIG"; then
  sed -i 's/^  concurrent_jobs:.*/  concurrent_jobs: 1/' "$CONFIG"
  echo "[bazarr-init] concurrent_jobs pinned to 1 in config.yaml"
fi

(
  attempt=0
  while [ "$attempt" -lt 120 ]; do
    attempt=$((attempt + 1))
    sleep 1

    [ -f "$CONFIG" ] || continue

    apikey=$(awk '
      /^auth:/ { in_auth=1; next }
      /^[^ ]/  { in_auth=0 }
      in_auth && /^  apikey:/ { print $2; exit }
    ' "$CONFIG")
    [ -n "$apikey" ] || continue

    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
      -X POST "http://127.0.0.1:6767/api/system/settings" \
      -H "X-API-KEY: ${apikey}" \
      -d "settings-general-concurrent_jobs=1")

    if [ "$code" = "204" ]; then
      echo "[bazarr-init] concurrent_jobs enforced via API (attempt ${attempt})"
      exit 0
    fi
  done
  echo "[bazarr-init] WARNING: gave up enforcing concurrent_jobs via API after ${attempt}s"
) &
disown
