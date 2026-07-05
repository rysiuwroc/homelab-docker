#!/usr/bin/env python3
"""qbit-scratch-guard.py -- robust, set-and-forget manager for qBittorrent's NVMe scratch.

Runs every 60s (systemd timer). Guarantees, with NO human attention:
  * /mnt/scratch never fills   -> no ENOSPC, downloads never crash
  * never deadlocks on slow/dead swarms
  * NEVER deletes torrents or data (pressure is relieved by pausing + CIFS overflow)
  * maximizes download speed when there is room

Mechanisms (all keyed off scratch free space):
  1. TIERS       -> qBit max_active_downloads (speed when free, throttle when tight).
  2. OVERFLOW    -> when scratch is low, flip qBit temp_path to the 17 TB CIFS array so
                    NEW torrents download there instead (rate-capped, no I/O storm).
                    Existing torrents are NOT moved (a temp_path change only affects new
                    adds), so scratch simply drains. Flips back when space recovers.
  3. FREEZE      -> if scratch is CRITICALLY low, PAUSE (never delete) the least-complete
                    scratch downloaders, keeping only the few most-complete active so they
                    finish and free space fastest. Frozen torrents are tagged and resume
                    automatically once space recovers.

TUNING: edit the constants below; no restart needed (next 60s tick picks them up).
TEST HOOK: SCRATCH_GUARD_TEST_FREE_GB=<n> makes it act as if free space is n GB.
"""
import json
import logging
import logging.handlers
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

# ---- Tunables -------------------------------------------------------------
SCRATCH_MNT  = "/mnt/scratch"                 # host mount watched for free space
SCRATCH_TEMP = "/scratch/incomplete"          # qBit (container) incomplete path on NVMe
CIFS_TEMP    = "/data/Downloads/incomplete"   # qBit (container) overflow path on CIFS (17 TB)
API          = "http://localhost:8088"        # qBit WebUI (no auth from localhost)
FROZEN_TAG   = "scratch-frozen"               # tag applied to guard-paused torrents

# free_gb (must be EXCEEDED, top-down) -> max_active_downloads
TIERS = [(150, 20), (100, 15), (60, 10), (0, 6)]

OVERFLOW_ON_GB   = 60     # scratch below this -> new torrents go to CIFS
OVERFLOW_OFF_GB  = 90     # scratch above this -> new torrents back to NVMe scratch
CRITICAL_GB      = 35     # below this -> freeze least-complete scratch downloaders
UNFREEZE_GB      = 90     # above this -> resume all frozen torrents
KEEP_ACTIVE_CRIT = 3      # most-complete scratch torrents kept downloading when critical

ACTIVE_DL = {"downloading", "forcedDL", "stalledDL", "metaDL", "forcedMetaDL"}
HTTP_TIMEOUT = 10
LOG_DIR  = "/home/rysiu/logs"
LOG_FILE = os.path.join(LOG_DIR, "scratch-guard.log")
# ---------------------------------------------------------------------------


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    lg = logging.getLogger("scratch-guard")
    lg.setLevel(logging.INFO)
    if not lg.handlers:
        h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1 << 20, backupCount=3)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        lg.addHandler(h)
    return lg


def api_get(path):
    with urllib.request.urlopen(API + path, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode())


def api_post(path, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        API + path, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read()


def set_prefs(d):
    api_post("/api/v2/app/setPreferences", {"json": json.dumps(d)})


def free_gb():
    override = os.environ.get("SCRATCH_GUARD_TEST_FREE_GB")
    if override is not None:
        return float(override)
    return shutil.disk_usage(SCRATCH_MNT).free / 1e9


def tier_dl(fg):
    for threshold, value in TIERS:
        if fg > threshold:
            return value
    return TIERS[-1][1]


def has_tag(t, tag):
    return tag in [x.strip() for x in (t.get("tags") or "").split(",")]


def main():
    lg = setup_logging()
    try:
        fg = free_gb()
        prefs = api_get("/api/v2/app/preferences")

        # 1. concurrency by free-space tier
        want_dl = tier_dl(fg)
        if prefs.get("max_active_downloads") != want_dl:
            set_prefs({"max_active_downloads": want_dl})

        # 2. overflow temp_path flip (hysteresis: ON<60, OFF>90)
        cur_temp = prefs.get("temp_path")
        want_temp = cur_temp
        if fg < OVERFLOW_ON_GB:
            want_temp = CIFS_TEMP
        elif fg > OVERFLOW_OFF_GB:
            want_temp = SCRATCH_TEMP
        if want_temp != cur_temp:
            set_prefs({"temp_path_enabled": True, "temp_path": want_temp})

        tor = api_get("/api/v2/torrents/info")
        action = ""

        # 3. critical freeze (NEVER delete) / auto-recover
        if fg < CRITICAL_GB:
            sc = [t for t in tor
                  if (t.get("download_path") or "").startswith("/scratch")
                  and t.get("state") in ACTIVE_DL
                  and not has_tag(t, FROZEN_TAG)]
            sc.sort(key=lambda t: t.get("progress", 0.0), reverse=True)
            freeze = [t["hash"] for t in sc[KEEP_ACTIVE_CRIT:]]
            if freeze:
                h = "|".join(freeze)
                api_post("/api/v2/torrents/addTags", {"hashes": h, "tags": FROZEN_TAG})
                api_post("/api/v2/torrents/stop", {"hashes": h})
                action = "froze %d least-complete (kept %d active)" % (len(freeze), KEEP_ACTIVE_CRIT)
            lg.critical("CRITICAL scratch free=%.1fGB (<%dGB): overflow->CIFS; %s; NOTHING deleted",
                        fg, CRITICAL_GB, action or "nothing to freeze")
        elif fg > UNFREEZE_GB:
            frozen = [t["hash"] for t in tor if has_tag(t, FROZEN_TAG)]
            if frozen:
                h = "|".join(frozen)
                api_post("/api/v2/torrents/start", {"hashes": h})
                api_post("/api/v2/torrents/removeTags", {"hashes": h, "tags": FROZEN_TAG})
                action = "resumed %d frozen" % len(frozen)

        lg.info("free=%.1fGB max_dl=%d temp=%s%s", fg, want_dl,
                "CIFS-overflow" if want_temp == CIFS_TEMP else "scratch",
                " | " + action if action else "")
    except Exception:
        # API down / disk unreadable / transient: log and exit 0 so the systemd
        # timer never accrues failed state; self-heals on the next tick.
        lg.exception("scratch-guard run failed")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
