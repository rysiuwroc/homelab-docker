#!/usr/bin/with-contenv bash
# Bazarr has no compose/env var for `general.concurrent_jobs`; it only lives in config.yaml
# inside the bazarr_config volume. Left at the LSIO default (4), Bazarr can run multiple
# ffsubsync/search jobs in parallel against the shared RAID0 media disk (mounted over CIFS
# from HomeServer .69) - this saturated the array (queue depth 20+, 180-320ms read latency)
# and stalled Plex/Jellyfin playback (2026-08-25 incident).
#
# LSIO runs every /custom-cont-init.d/*.sh on EVERY container start (not just first boot), so
# this self-heals after a config reset/upgrade instead of relying on someone remembering to
# reapply the setting by hand.
CONFIG=/config/config/config.yaml

if [ -f "$CONFIG" ]; then
  if grep -q '^  concurrent_jobs:' "$CONFIG"; then
    sed -i 's/^  concurrent_jobs:.*/  concurrent_jobs: 1/' "$CONFIG"
  else
    sed -i '/^general:/a\  concurrent_jobs: 1' "$CONFIG"
  fi
  echo "[bazarr-init] concurrent_jobs pinned to 1"
else
  # First-ever boot: Bazarr hasn't generated config.yaml yet, so there's nothing to patch here.
  # It will self-correct on the next container start (this script reruns every time).
  echo "[bazarr-init] config.yaml not present yet (first boot) - will patch on next restart"
fi
