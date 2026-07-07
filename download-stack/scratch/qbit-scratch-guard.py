#!/usr/bin/env python3
"""qbit-scratch-guard.py -- robust, set-and-forget manager for qBittorrent's NVMe scratch.

Runs every 60s (systemd timer). With NO human attention:
  * /mnt/scratch never fills   -> no ENOSPC, downloads never crash
  * never deadlocks on slow/dead swarms
  * frees dead weight WITHOUT touching anything recoverable
  * maximizes download speed when there is room

Mechanisms (keyed off scratch free space + torrent state):
  1. TIERS    -> qBit max_active_downloads (speed when free, throttle when tight).
  2. OVERFLOW -> when scratch is low, flip qBit temp_path to the 17 TB CIFS array so NEW
                 torrents download there instead (rate-capped). Existing torrents are NOT
                 moved (temp_path change only affects new adds); scratch drains. Flips back
                 when space recovers.
  3. FREEZE   -> if scratch is CRITICALLY low, PAUSE (never delete) the least-complete
                 scratch downloaders, keeping the few most-complete active so they finish
                 and free space fastest. Tagged + auto-resumed when space recovers.
  4. REAP     -> DELETE (with files) dead stalled 'ratio' downloads: category 'ratio' +
                 stalled/metaDL + progress<100% + num_seeds==0 (no source, can NEVER finish)
                 + inactive > REAP_STALL_HOURS. Deliberately narrow: never arr categories
                 (Cleanuparr handles those), never a torrent that has seeders or is a completed
                 seed. autobrr grabs on NEW IRC announces (never re-scans), so reaped dead
                 torrents do NOT get re-pulled.
  5. REAP-ORPHAN -> DELETE scratch /incomplete dirs matching NO live torrent (torrent removed
                 but temp data left, or stray leftover), older than ORPHAN_GRACE_HOURS.
                 Directories only; never a dir any torrent content_path points into.

TUNING: edit the constants below; no restart needed (next 60s tick picks them up).
TEST/OPS HOOKS:
  SCRATCH_GUARD_TEST_FREE_GB=<n>  -> pretend free space is n GB (test tiers/overflow/freeze).
  SCRATCH_GUARD_REAP_HOURS=<n>    -> override reap age this run (e.g. =1 for an immediate sweep).
  SCRATCH_GUARD_ORPHAN_GRACE_HOURS=<n> -> override orphan-reap grace this run.
"""
import json
import logging
import logging.handlers
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- Tunables -------------------------------------------------------------
SCRATCH_MNT  = "/mnt/scratch"                 # host mount watched for free space
SCRATCH_TEMP = "/scratch/incomplete"          # qBit (container) incomplete path on NVMe
CIFS_TEMP    = "/data/Downloads/incomplete"   # qBit (container) overflow path on CIFS (17 TB)
API          = "http://localhost:8088"        # qBit WebUI (no auth from localhost)
FROZEN_TAG   = "scratch-frozen"               # tag applied to guard-paused torrents
SCRATCH_INC_HOST = "/mnt/scratch/incomplete"  # host path of the container's /scratch/incomplete
ORPHAN_GRACE_HOURS = float(os.environ.get("SCRATCH_GUARD_ORPHAN_GRACE_HOURS", "24"))  # age before reaping untracked scratch dirs

# free_gb (must be EXCEEDED, top-down) -> max_active_downloads
TIERS = [(150, 20), (100, 15), (60, 10), (0, 6)]

OVERFLOW_ON_GB   = 60     # scratch below this -> new torrents go to CIFS
OVERFLOW_OFF_GB  = 90     # scratch above this -> new torrents back to NVMe scratch
CRITICAL_GB      = 35     # below this -> freeze least-complete scratch downloaders
UNFREEZE_GB      = 90     # above this -> resume all frozen torrents
KEEP_ACTIVE_CRIT = 3      # most-complete scratch torrents kept downloading when critical

REAP_CATEGORY    = "ratio"  # only reap this category (arr categories left to Cleanuparr)
REAP_STALL_HOURS = float(os.environ.get("SCRATCH_GUARD_REAP_HOURS", "6"))  # inactive age before reap

ACTIVE_DL = {"downloading", "forcedDL", "stalledDL", "metaDL", "forcedMetaDL"}
STALLED   = {"stalledDL", "metaDL"}
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


def dir_size(p):
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


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
            lg.critical("CRITICAL scratch free=%.1fGB (<%dGB): overflow->CIFS; %s; nothing recoverable deleted",
                        fg, CRITICAL_GB, action or "nothing to freeze")
        elif fg > UNFREEZE_GB:
            frozen = [t["hash"] for t in tor if has_tag(t, FROZEN_TAG)]
            if frozen:
                h = "|".join(frozen)
                api_post("/api/v2/torrents/start", {"hashes": h})
                api_post("/api/v2/torrents/removeTags", {"hashes": h, "tags": FROZEN_TAG})
                action = "resumed %d frozen" % len(frozen)

        # 4. reap DEAD stalled 'ratio' downloads: num_seeds==0 => no source, can NEVER finish.
        #    Narrow on purpose: only category 'ratio', only stalled+incomplete, only 0 seeders,
        #    only after REAP_STALL_HOURS inactive. Never arr, never torrents with sources, never
        #    completed seeds. autobrr acts on new announces => reaped torrents are not re-pulled.
        now = time.time()
        dead = [t["hash"] for t in tor
                if t.get("category") == REAP_CATEGORY
                and t.get("state") in STALLED
                and int(t.get("num_seeds", 0)) == 0
                and t.get("progress", 1.0) < 1.0
                and (now - t.get("last_activity", now)) > REAP_STALL_HOURS * 3600]
        if dead:
            api_post("/api/v2/torrents/delete", {"hashes": "|".join(dead), "deleteFiles": "true"})
            action = (action + " | " if action else "") + \
                "reaped %d dead stalled %s (0 seeds, >%.1fh)" % (len(dead), REAP_CATEGORY, REAP_STALL_HOURS)

        # 5. REAP-ORPHAN: scratch incomplete dirs that no live torrent points into (torrent
        #    deleted but temp data left, or stray leftover). Only directories, only older than
        #    ORPHAN_GRACE_HOURS, matched by content_path basename. `live` includes ANY torrent
        #    whose content_path is under /scratch/incomplete/ (downloading, stuck-completed, or
        #    mid-move), so the reaper never deletes data a torrent still references.
        try:
            live = {os.path.basename((t.get("content_path") or "").rstrip("/"))
                    for t in tor if (t.get("content_path") or "").startswith(SCRATCH_TEMP + "/")}
            reaped = 0
            reaped_gb = 0.0
            for name in os.listdir(SCRATCH_INC_HOST):
                p = os.path.join(SCRATCH_INC_HOST, name)
                if name in live or name == "lost+found" or not os.path.isdir(p):
                    continue
                if (now - os.path.getmtime(p)) < ORPHAN_GRACE_HOURS * 3600:
                    continue
                sz = dir_size(p)
                shutil.rmtree(p, ignore_errors=True)
                reaped += 1
                reaped_gb += sz / 1e9
                lg.warning("reaped orphan scratch dir %.1fGB %s (no live torrent, >%.0fh)",
                           sz / 1e9, name[:60], ORPHAN_GRACE_HOURS)
            if reaped:
                action = (action + " | " if action else "") + "reaped %d orphan dirs %.1fGB" % (reaped, reaped_gb)
        except Exception:
            lg.exception("orphan reap failed")

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
