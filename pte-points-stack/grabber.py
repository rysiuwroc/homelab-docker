#!/usr/bin/env python3
"""
pte-points-grabber - farmi punkty bonusowe PolishTrackera przez seedowanie wielu
malych, rzadkich, starszych filmow/seriali.

Wzor punktow PT nagradza: duzo torrentow (n), dlugi czas trzymania (w->4x),
male rozmiary (najlepszy stosunek punkty/GB), rzadkie ale zywe swarmy
(f<3 -> mnoznik x2) oraz wiek na trackerze (t). Ten serwis:

  1. crawluje API PT (od starszych stron w dol - najstarsze = najwyzsze tpage),
  2. filtruje: kategorie film/serial, size < MAX_SIZE_GB, seeders w [MIN,MAX],
     pomija juz snatchowane (pole API `seeder`/`progress`) i lokalnie zapisane,
  3. rankuje po punkty-na-GB (wiek * (1+GB)/GB) - preferuje stare i male,
  4. pobiera .torrent i dodaje do qBittorrent w dedykowanej kategorii seed-only
     na F: (media-f), do limitu BUDGET_GB, partiami (BATCH) co INTERVAL_MIN,
  5. reapuje martwe (0 seederow w swarmie) niedociagniete graby, zeby nie
     zasmiecac NVMe scratcha.

Tylko biblioteka standardowa. Sekrety (API-Key, rsskey) z env (Portainer stack).
"""
import io
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger("pte-points")
GB = 10**9


def env(k, d=None):
    return os.environ.get(k, d)


API_KEY = env("PTE_API_KEY")
RSSKEY = env("PTE_RSSKEY")
QBIT = env("QBIT_URL", "http://qbittorrent:8080").rstrip("/")
CATEGORY = env("QBIT_CATEGORY", "points")
SAVEPATH = env("QBIT_SAVEPATH", "/data/Downloads/torrents/points")
BUDGET_GB = float(env("BUDGET_GB", "500"))
MAX_SIZE_GB = float(env("MAX_SIZE_GB", "2"))
MIN_SEED = int(env("MIN_SEEDERS", "1"))
MAX_SEED = int(env("MAX_SEEDERS", "3"))
CATS = [int(x) for x in env("CATS", "6,7,9,11,12,14").split(",") if x.strip()]
BATCH = int(env("BATCH", "25"))
INTERVAL = int(env("INTERVAL_MIN", "240")) * 60
PAGE_MIN = int(env("PAGE_MIN", "150"))
PAGE_MAX = int(env("PAGE_MAX", "5000"))
PAGES_PER_CYCLE = int(env("PAGES_PER_CYCLE", "40"))
REAP_DEAD_HOURS = float(env("REAP_DEAD_HOURS", "6"))
PAGE_SLEEP = float(env("PAGE_SLEEP_SEC", "1.5"))
ADD_SLEEP = float(env("ADD_SLEEP_SEC", "2"))
STATE_DIR = env("STATE_DIR", "/state")
API_BASE = env("PTE_API_BASE", "https://api-test.pte.nu/api/v1/torrents")
DL_BASE = env("PTE_DL_BASE", "https://pte.nu/downrss")
DRY = env("DRY_RUN", "").lower() in ("1", "true", "yes")

STATE_FILE = os.path.join(STATE_DIR, "state.json")


# ---------------------------------------------------------------- state
def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        s.setdefault("grabbed", [])
        s.setdefault("cursor", PAGE_MIN)
        return s
    except Exception:
        return {"grabbed": [], "cursor": PAGE_MIN}


def save_state(s):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------- http
def http(url, headers=None, data=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(), dict(r.headers)


def http_pt(url, headers=None, timeout=30, tries=4):
    """GET z backoffem na 429/5xx - PT rate-limituje crawl."""
    delay = 8.0
    for i in range(tries):
        try:
            return http(url, headers=headers, timeout=timeout)
        except urllib.error.HTTPError as e:
            if (e.code == 429 or 500 <= e.code < 600) and i < tries - 1:
                log.warning("PT %s -> HTTP %d, backoff %.0fs", url.split("?")[0], e.code, delay)
                time.sleep(delay)
                delay *= 2
            else:
                raise


# ---------------------------------------------------------------- PT API
def pt_page(tpage):
    cats = "".join("&cat[]=%d" % c for c in CATS)
    url = "%s?search=%s&tpage=%d" % (API_BASE, cats, tpage)
    _, body, _ = http_pt(url, headers={"API-Key": API_KEY}, timeout=30)
    return json.loads(body.decode("utf-8", "replace"))


def pt_download(tid):
    _, body, hdr = http_pt("%s/%s/%d" % (DL_BASE, RSSKEY, tid), timeout=60)
    if not body[:1] == b"d" and "bittorrent" not in hdr.get("Content-Type", ""):
        raise RuntimeError("not a torrent (ct=%s)" % hdr.get("Content-Type"))
    return body


def age_years(added):
    s = str(added).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        dt = datetime.fromisoformat(s[:19] + "+00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / (365.25 * 86400))


def score(sz, added):
    """Punkty-na-GB proxy: wiek * (1+GB)/GB. Mnoznik x2 (f<3) i w sa wspolne
    dla wszystkich kandydatow (wszystkie male+rzadkie), wiec je pomijamy."""
    gb = sz / GB
    return age_years(added) * (1 + gb) / max(gb, 0.01)


# ---------------------------------------------------------------- qBittorrent
def qget(path, params=None, timeout=30):
    url = QBIT + "/api/v2/" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    _, body, _ = http(url, timeout=timeout)
    return body


def qpost(path, params, timeout=30):
    url = QBIT + "/api/v2/" + path
    st, body, _ = http(url, data=urllib.parse.urlencode(params).encode(), timeout=timeout)
    return st, body


def qadd(fb, fn):
    boundary = "----ptepoints%d" % int(time.time() * 1000)
    buf = io.BytesIO()

    def w(s):
        buf.write(s.encode() if isinstance(s, str) else s)

    for k, v in (("category", CATEGORY), ("savepath", SAVEPATH), ("paused", "false")):
        w("--%s\r\n" % boundary)
        w('Content-Disposition: form-data; name="%s"\r\n\r\n' % k)
        w(str(v))
        w("\r\n")
    w("--%s\r\n" % boundary)
    w('Content-Disposition: form-data; name="torrents"; filename="%s"\r\n' % fn)
    w("Content-Type: application/x-bittorrent\r\n\r\n")
    w(fb)
    w("\r\n--%s--\r\n" % boundary)
    st, rbody, _ = http(
        QBIT + "/api/v2/torrents/add",
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary},
        data=buf.getvalue(),
        timeout=60,
    )
    return st, rbody


def ensure_category():
    try:
        qpost("torrents/createCategory", {"category": CATEGORY, "savePath": SAVEPATH})
    except urllib.error.HTTPError:
        pass  # 409 = already exists
    try:
        qpost("torrents/editCategory", {"category": CATEGORY, "savePath": SAVEPATH})
    except urllib.error.HTTPError:
        pass


def points_torrents():
    body = qget("torrents/info", {"category": CATEGORY})
    return json.loads(body.decode("utf-8", "replace")) if body else []


def reap_dead(torrents):
    """Usuwa (z plikami) niedociagniete graby ktore maja 0 seederow w swarmie
    dluzej niz REAP_DEAD_HOURS - martwe, nigdy sie nie dociagna, zajmuja scratch."""
    now = time.time()
    dead = [
        t["hash"]
        for t in torrents
        if float(t.get("progress", 0)) < 1.0
        and int(t.get("num_complete", -1)) == 0
        and (now - int(t.get("added_on", now))) > REAP_DEAD_HOURS * 3600
    ]
    if dead:
        qpost("torrents/delete", {"hashes": "|".join(dead), "deleteFiles": "true"})
        log.info("reaped %d dead (0-seed) points torrents", len(dead))
    return set(dead)


# ---------------------------------------------------------------- cycle
def cycle(state):
    if not DRY:
        ensure_category()
        tors = points_torrents()
        reaped = reap_dead(tors)
        tors = [t for t in tors if t["hash"] not in reaped]
        cur_gb = sum(int(t.get("size", 0)) for t in tors) / GB
    else:
        tors, cur_gb = [], 0.0
    log.info("points: %d torrents, %.1f/%.0f GB used", len(tors), cur_gb, BUDGET_GB)
    if cur_gb >= BUDGET_GB:
        log.info("budget reached - skipping grab this cycle")
        return

    grabbed = set(state["grabbed"])
    cands = []
    p = state.get("cursor", PAGE_MIN)
    for _ in range(PAGES_PER_CYCLE):
        try:
            d = pt_page(p)
        except Exception as e:
            log.warning("page %d error: %s", p, e)
            d = {}
        for t in d.get("torrents", []) or []:
            try:
                tid, sz, sd = int(t["id"]), int(t["size"]), int(t["seeders"])
            except Exception:
                continue
            if tid in grabbed or t.get("seeder") or int(t.get("progress") or 0) >= 100:
                continue
            if sz > MAX_SIZE_GB * GB or not (MIN_SEED <= sd <= MAX_SEED):
                continue
            cands.append((score(sz, t.get("added")), tid, sz, str(t.get("name", "?"))))
        p = p + 1 if p < PAGE_MAX else PAGE_MIN
        time.sleep(PAGE_SLEEP)
    state["cursor"] = p
    cands.sort(reverse=True)
    log.info("scanned %d pages, %d fresh candidates", PAGES_PER_CYCLE, len(cands))

    added = 0
    for sc, tid, sz, name in cands:
        if added >= BATCH or cur_gb >= BUDGET_GB:
            break
        if cur_gb + sz / GB > BUDGET_GB:
            continue  # too big for remaining budget; keep filling with smaller
        if DRY:
            log.info("[DRY] +%d %.2fGB score=%.1f %s", tid, sz / GB, sc, name[:55])
            grabbed.add(tid)
            cur_gb += sz / GB
            added += 1
            continue
        try:
            fb = pt_download(tid)
            st, rbody = qadd(fb, "%d.torrent" % tid)
            if st == 200:
                grabbed.add(tid)
                try:
                    ok = bool(json.loads(rbody.decode() or "{}").get("added_torrent_ids"))
                except Exception:
                    ok = b"Ok" in rbody  # stary qBit zwracal tekst "Ok."
                if ok:
                    cur_gb += sz / GB
                    added += 1
                    log.info("added %d %.2fGB score=%.1f %s | %.1f/%.0f GB",
                             tid, sz / GB, sc, name[:50], cur_gb, BUDGET_GB)
                else:
                    log.info("qbit already had %d", tid)
            else:
                log.warning("qbit add %d -> HTTP %s %s", tid, st, rbody[:80])
        except Exception as e:
            log.warning("grab %d failed: %s", tid, e)
        time.sleep(ADD_SLEEP)

    state["grabbed"] = sorted(grabbed)
    if not DRY:
        save_state(state)
    log.info("cycle done: +%d torrents, %.1f/%.0f GB", added, cur_gb, BUDGET_GB)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not API_KEY or not RSSKEY:
        log.error("PTE_API_KEY and PTE_RSSKEY are required")
        raise SystemExit(1)
    log.info(
        "start: cats=%s size<%.1fGB seeders=%d-%d budget=%.0fGB batch=%d interval=%dmin dry=%s",
        CATS, MAX_SIZE_GB, MIN_SEED, MAX_SEED, BUDGET_GB, BATCH, INTERVAL // 60, DRY,
    )
    state = load_state()
    while True:
        try:
            cycle(state)
        except Exception as e:
            log.exception("cycle error: %s", e)
        if DRY:
            break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
