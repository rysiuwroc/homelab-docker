#!/usr/bin/env python3
"""
pte-points-grabber - farmi punkty bonusowe PolishTrackera przez seedowanie wielu
malych, rzadkich, starszych torrentow (filmy + gry/konsole - rozne rozmiary).

Wzor punktow PT (suma po n seedowanych torrentach):

  sum_i ( log(cbrt(n)/2) + (1 + s_i*1e-9) * (t_i/311e5)^((1/2)^k_i) ) * w_i

gdzie s=bajty (s*1e-9=GB), t=wiek na trackerze w SEKUNDACH (311e5 ~= 360 dni),
f=seedery, w=waga trzymania (1->4). Rzadkosc to WYKLADNIK (1/2)^k na wieku
(f>=3 -> liniowo k=0; f<3 -> pierwiastek k=1), a NIE mnoznik x2.

Do rankowania kandydatow w cyklu (maksymalizacja punktow pod stalym budzetem GB
= value/weight knapsack) log(cbrt(n)/2) i w sa wspolne (to samo n; swieze graby
z~0 -> w=1), wiec je pomijamy i dzielimy czesc torrentowa przez GB:

  score(s,t,f) = (1+GB)/GB * (t/311e5)^((1/2)^k)   # punkty na GB

Ten serwis:
  1. reapuje martwe/utkniete graby i liczy zajetosc kategorii `points`
     (nacisk budzetowy = used/BUDGET),
  2. crawluje API PT SAMOSTROJACO - per kategoria szuka wydajnych regionow osi
     `tpage` (bandit: eksploatuj najlepszy region, z p-stwem EPS eksploruj
     losowy/frontier zeby sledzic dryf; pierwszy raz rzadki zwiad landscape),
     limit PB_HARD stron/cykl, adaptacyjne opoznienie swiadome 429,
  3. progi AUTO: prog score = percentyl rezerwuaru zywych kandydatow skalowany
     naciskiem budzetu; min-size per kategoria = niski percentyl zywych
     rozmiarow (odsiewa mikro/smieciowy ogon wzgledem normy kategorii),
  4. dedup wzgledem trwalego stanu grabbera + realnej zawartosci qBit `points`
     (po znormalizowanej nazwie); rankuje po score i dobiera do BUDGET_GB,
  5. reapuje martwe (utkniete) niedociagniete graby, zeby nie zasmiecac scratcha.

Tylko biblioteka standardowa. Sekrety (API-Key, rsskey) z env (Portainer stack).
"""
import io
import json
import logging
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger("pte-points")


def env(k, d=None):
    return os.environ.get(k, d)


API_KEY = env("PTE_API_KEY")
RSSKEY = env("PTE_RSSKEY")
QBIT = env("QBIT_URL", "http://qbittorrent:8080").rstrip("/")
CATEGORY = env("QBIT_CATEGORY", "points")
SAVEPATH = env("QBIT_SAVEPATH", "/data/Downloads/torrents/points")
BUDGET_GB = float(env("BUDGET_GB", "500"))
MIN_SEED = int(env("MIN_SEEDERS", "2"))          # dolny prog zywotnosci (>=tyle seederow zeby sie dociagnelo)
CATS = [int(x) for x in env("CATS", "6,7,9,11,12,14").split(",") if x.strip()]
INTERVAL = int(env("INTERVAL_MIN", "15")) * 60   # krotki cykl: reap stalli + top-up wolnego miejsca
RATIO_CATEGORY = env("RATIO_CATEGORY", "ratio")  # kategoria autobrr (swieze release'y) - trzymana na TOPie kolejki
PRIO_TICK = float(env("PRIO_TICK_SEC", "10"))    # co ile s wynosic `ratio` na top (lekki call - moze nakurwiac)
REAP_STALL_MIN = float(env("REAP_STALL_MIN", "20"))  # nuke utknietych po tylu minutach bezczynnosci
PAGE_SLEEP = float(env("PAGE_SLEEP_SEC", "1.5"))  # poczatkowe/dolne opoznienie miedzy requestami; limiter uczy sie z 429
ADD_SLEEP = float(env("ADD_SLEEP_SEC", "1"))
STATE_DIR = env("STATE_DIR", "/state")
API_BASE = env("PTE_API_BASE", "https://api-test.pte.nu/api/v1/torrents")
DL_BASE = env("PTE_DL_BASE", "https://pte.nu/downrss")
DRY = env("DRY_RUN", "").lower() in ("1", "true", "yes")

STATE_FILE = os.path.join(STATE_DIR, "state.json")

GB = 10**9
PT_T_NORM = 311e5                # ~360 dni w sekundach = normalizator wieku t we wzorze PT
# --- crawl samostrojacy: stale wewnetrzne (NIE pokretla env) ---
REGION_W          = 50           # szerokosc kubelka tpage; arm = (kategoria, region)
BOOTSTRAP_TPAGES  = [50, 200, 500, 1000, 2000, 3000, 4000, 6000, 8000, 12000]  # rzadki zwiad landscape/kat
DEFAULT_MAX_REG   = 240          # gorna granica regionow do eksploracji zanim poznamy koniec (12000/50)
PB_HARD           = 60           # twardy limit stron/cykl (rate/ban + brak skanu calej bazy)
EPS_EXPLORE       = 0.2          # p-stwo eksploracji (reszta = eksploatacja najlepszego arma)
YIELD_ALPHA       = 0.3          # wspolczynnik EMA yield/score per arm
LOWYIELD_PATIENCE = 8            # tyle kolejnych stron ~0 akceptacji -> region wyczerpany
RES_SCORE_MAX     = 2000         # rozmiar rezerwuaru scorow (globalny)
RES_SIZE_MAX      = 1000         # rozmiar rezerwuaru rozmiarow per kategoria
P_LOW, P_HIGH     = 20.0, 80.0   # percentyl progu score: pusty budzet -> P_LOW, pelny -> P_HIGH
SCORE_WARMUP      = 200          # min. probek zanim wlaczymy dynamiczny prog score
SIZE_WARMUP       = 100          # min. probek zanim wlaczymy dynamiczny min-size per kat
SIZE_PCTL         = 10.0         # min-size = 10. percentyl rozmiarow zywych w kategorii
SIZE_BOOT_BYTES   = 10 * 10**6   # bootstrapowy min-size (10 MB) do czasu uzbierania probek
HARD_MAX_SIZE_GB  = 64.0         # sanity cap; prog score i tak odrzuca duze o niskiej wartosci
DELAY_MIN, DELAY_MAX = 1.0, 10.0 # granice adaptacyjnego opoznienia miedzy requestami (s)
DELAY_UP, DELAY_DOWN = 1.5, 0.95 # 429 -> delay*=UP ; czysty cykl -> delay*=DOWN


# ---------------------------------------------------------------- state
def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
    except Exception:
        s = {}
    g = s.get("grabbed")
    if isinstance(g, list):                      # migracja v1 (lista id) -> v2 (dict)
        s["grabbed"] = {str(i): {} for i in g}
    s.setdefault("grabbed", {})
    s.setdefault("regions", {})
    s.setdefault("size_res", {})
    s.setdefault("score_res", [])
    s.setdefault("delay", PAGE_SLEEP)
    s.setdefault("max_tpage", 0)
    s.setdefault("cat_cursor", {})
    return s


def save_state(s):
    for k in ("_had_429", "_used_frac"):         # nie zapisuj pol scratchowych
        s.pop(k, None)
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


def throttle(state):
    time.sleep(state["delay"] * random.uniform(0.75, 1.25))


def note_429(state, retry_after=None):
    state["_had_429"] = True
    if retry_after:
        try:
            time.sleep(min(float(retry_after), 60.0))
        except (TypeError, ValueError):
            pass
    state["delay"] = min(DELAY_MAX, state["delay"] * DELAY_UP)


def relax_delay(state):
    state["delay"] = max(DELAY_MIN, state["delay"] * DELAY_DOWN)


def http_pt(state, url, headers=None, timeout=30, tries=4):
    """GET z throttlingiem + adaptacyjnym backoffem. delay uczy sie z 429."""
    delay = 8.0
    for i in range(tries):
        throttle(state)
        try:
            return http(url, headers=headers, timeout=timeout)
        except urllib.error.HTTPError as e:
            if (e.code == 429 or 500 <= e.code < 600) and i < tries - 1:
                ra = e.headers.get("Retry-After") if e.headers else None
                log.warning("PT %s -> HTTP %d, delay->%.1fs backoff %.0fs",
                            url.split("?")[0], e.code, state["delay"], delay)
                note_429(state, ra)
                time.sleep(delay)
                delay *= 2
            else:
                raise


# ---------------------------------------------------------------- PT API
def pt_page(state, cat, tpage):
    """Jedna strona jednej kategorii: ?search=&cat[]=<cat>&tpage=<n>. Zwraca liste torrentow."""
    url = "%s?search=&cat[]=%d&tpage=%d" % (API_BASE, cat, tpage)
    _, body, _ = http_pt(state, url, headers={"API-Key": API_KEY}, timeout=30)
    d = json.loads(body.decode("utf-8", "replace"))
    return d.get("torrents", []) or []


def pt_download(state, tid):
    _, body, hdr = http_pt(state, "%s/%s/%d" % (DL_BASE, RSSKEY, tid), timeout=60)
    if not body[:1] == b"d" and "bittorrent" not in hdr.get("Content-Type", ""):
        raise RuntimeError("not a torrent (ct=%s)" % hdr.get("Content-Type"))
    return body


def age_seconds(added):
    """Wiek torrenta na trackerze w sekundach (pole API `added` -> teraz) = t we wzorze PT."""
    s = str(added).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        dt = datetime.fromisoformat(s[:19] + "+00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def score(sz, added, seeders):
    """Punkty-na-GB wg wzoru PT: (1+GB)/GB * (t/311e5)^((1/2)^k), k=0 gdy f>=3 else 1.
    log(cbrt(n)/2) i wage w pomijamy - dla kandydatow w cyklu sa wspolne (to samo n; z~0)."""
    gb = sz / GB
    k = 0 if seeders >= 3 else 1
    age_factor = (age_seconds(added) / PT_T_NORM) ** ((1 / 2) ** k)
    return (1 + gb) / max(gb, 0.01) * age_factor


# ---------------------------------------------------------------- pure helpers
def pctl(vals, p):
    """Percentyl p (0-100) z listy; interpolacja liniowa. [] -> 0.0."""
    if not vals:
        return 0.0
    xs = sorted(vals)
    k = (len(xs) - 1) * (p / 100.0)
    lo = int(k); hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def push_res(res, v, cap):
    """Dopisz probke do rezerwuaru trzymajac ostatnie `cap` sztuk (bounded memory)."""
    res.append(v)
    if len(res) > cap:
        del res[:len(res) - cap]


def norm_name(name):
    return re.sub(r"\s+", " ", str(name).strip().lower())


# ---------------------------------------------------------------- auto thresholds
def min_score_dyn(state, used_frac):
    """Dynamiczny prog score: percentyl rezerwuaru rosnie z zapelnieniem budzetu.
    Pusty budzet -> P_LOW (bierz szeroko, zapelnij); pelny -> P_HIGH (tylko top)."""
    res = state["score_res"]
    if len(res) < SCORE_WARMUP:
        return 0.0
    p = P_LOW + (P_HIGH - P_LOW) * max(0.0, min(1.0, used_frac))
    return pctl(res, p)


def min_size_bytes(state, cat):
    """Auto min-size per kategoria: SIZE_PCTL percentyl rozmiarow ZYWYCH torrentow
    w tej kategorii (odsiewa mikro/smieciowy ogon wzgledem normy kategorii)."""
    res = state["size_res"].get(str(cat), [])
    if len(res) < SIZE_WARMUP:
        return SIZE_BOOT_BYTES
    return pctl(res, SIZE_PCTL)


# ---------------------------------------------------------------- region search
def region_of(tpage):
    return tpage // REGION_W


def next_start(state, cat):
    """Gdzie zaczac skan tej kategorii: kontynuuj zapisany kursor; brak -> reset."""
    cur = state["cat_cursor"].get(str(cat))
    return cur if cur is not None else _reset_start(state, cat)


def _reset_start(state, cat):
    """Punkt startu po resecie: najlepszy region tej kat (eksploatacja) albo losowy
    region (eksploracja z p-stwem EPS_EXPLORE - sledzi dryf i nowe strefy)."""
    prefix = "%d:" % cat
    arms = {k: v for k, v in state["regions"].items() if k.startswith(prefix)}
    top = (state.get("max_tpage") // REGION_W) if state.get("max_tpage") else DEFAULT_MAX_REG
    if not arms or random.random() < EPS_EXPLORE:
        return random.randint(0, max(1, top)) * REGION_W
    best = max(arms, key=lambda k: arms[k]["ey"] * arms[k]["es"])
    return int(best.split(":")[1]) * REGION_W


def ingest_page(state, page, cat, tpage, cands, grabbed, held_names):
    """Przetwarza strone: aktualizuje rezerwuary + statystyki arma, dopisuje
    zaakceptowanych kandydatow. Zwraca liczbe zaakceptowanych (yield strony)."""
    key = "%d:%d" % (cat, region_of(tpage))
    reg = state["regions"].setdefault(key, {"ey": 0.0, "es": 0.0, "pages": 0, "last": 0.0})
    floor = min_score_dyn(state, state["_used_frac"])
    accepted = 0; ssum = 0.0
    for t in page:
        try:
            tid, sz, sd = int(t["id"]), int(t["size"]), int(t["seeders"])
        except Exception:
            continue
        nid = str(tid)
        if nid in grabbed or t.get("seeder") or int(t.get("progress") or 0) >= 100:
            continue
        if norm_name(t.get("name", "?")) in held_names:
            continue
        if sd < MIN_SEED or sz > HARD_MAX_SIZE_GB * GB:
            continue
        push_res(state["size_res"].setdefault(str(cat), []), sz, RES_SIZE_MAX)  # zywy rozmiar
        if sz < min_size_bytes(state, cat):
            continue
        sc = score(sz, t.get("added"), sd)
        push_res(state["score_res"], sc, RES_SCORE_MAX)                          # zywy+rozmiar-ok
        if sc < floor:
            continue
        cands.append((sc, tid, sz, str(t.get("name", "?")), cat))
        accepted += 1; ssum += sc
    reg["ey"] = (1 - YIELD_ALPHA) * reg["ey"] + YIELD_ALPHA * accepted
    reg["es"] = (1 - YIELD_ALPHA) * reg["es"] + YIELD_ALPHA * (ssum / accepted if accepted else 0.0)
    reg["pages"] += 1; reg["last"] = time.time()
    return accepted


# ---------------------------------------------------------------- crawl
def crawl(state, needed_gb, grabbed, held_names):
    """Adaptacyjnie zbiera kandydatow. Pierwszy raz: rzadki zwiad (bootstrap) kazdej
    kategorii + ustawienie kursora na najlepszy region. Potem: skanuj kazda kategorie
    od jej kursora (kursor wedruje do przodu miedzy cyklami; reset do najlepszego /
    losowego regionu po koncu listy albo serii pustych stron). Limit PB_HARD stron/
    cykl dzielony rowno miedzy kategorie, z wczesnym stopem gdy uzbieramy na budzet."""
    cands = []; fetched = 0
    if not state["regions"]:                       # BOOTSTRAP: rzadki zwiad landscape
        for cat in CATS:
            for tp in BOOTSTRAP_TPAGES:
                if fetched >= PB_HARD:
                    break
                page = _fetch(state, cat, tp); fetched += 1
                ingest_page(state, page or [], cat, tp, cands, grabbed, held_names)
                if page is not None and len(page) == 0:
                    state["max_tpage"] = max(state.get("max_tpage", 0), tp); break
        for cat in CATS:
            state["cat_cursor"][str(cat)] = _reset_start(state, cat)
        return cands
    per_cat = max(1, PB_HARD // max(1, len(CATS)))
    for cat in CATS:
        if fetched >= PB_HARD or (sum(c[2] for c in cands) / GB) >= needed_gb * 1.3:
            break
        tp = next_start(state, cat); low = 0; steps = 0
        while steps < per_cat and fetched < PB_HARD and (sum(c[2] for c in cands) / GB) < needed_gb * 1.3:
            page = _fetch(state, cat, tp); fetched += 1; steps += 1
            n = ingest_page(state, page or [], cat, tp, cands, grabbed, held_names)
            if page is not None and len(page) == 0:   # koniec listy -> reset kursora
                state["max_tpage"] = max(state.get("max_tpage", 0), tp)
                tp = _reset_start(state, cat); low = 0
            else:
                low = low + 1 if n == 0 else 0
                tp += 1
                if low >= LOWYIELD_PATIENCE:           # region wyczerpany -> reset
                    tp = _reset_start(state, cat); low = 0
            state["cat_cursor"][str(cat)] = tp
    return cands


def _fetch(state, cat, tp):
    """pt_page z lapaniem bledow (None gdy blad, [] gdy pusto/koniec)."""
    try:
        return pt_page(state, cat, tp)
    except Exception as e:
        log.warning("page cat=%d tpage=%d error: %s", cat, tp, e)
        return None


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


def reap_stalled(torrents):
    """Kasuje (z plikami) niedociagniete graby ktore UTKNELY: stan stalledDL/metaDL
    i brak aktywnosci dluzej niz REAP_STALL_MIN. NIGDY nie tyka ukonczonych seedow
    ani stalledUP (bezczynny seed bez leecherow = nasz cel - zostaje). guard REAP w
    scratch-guardzie tyka tylko kategorie `ratio`, wiec `points` domykamy tutaj."""
    now = time.time()
    dead = [
        t["hash"]
        for t in torrents
        if t.get("state") in ("stalledDL", "metaDL")
        and float(t.get("progress", 0)) < 1.0
        and (now - int(t.get("last_activity", now))) > REAP_STALL_MIN * 60
    ]
    if dead:
        qpost("torrents/delete", {"hashes": "|".join(dead), "deleteFiles": "true"})
        log.info("reaped %d stalled points torrents", len(dead))
    return set(dead)


_last_prio_n = -1


def enforce_ratio_prio():
    """`ratio` ZAWSZE na top kolejki qBit: niedociagniete torrenty kategorii `ratio`
    (autobrr, swieze release'y) wynosimy na gore -> gdy `max_active_downloads` zapcha
    sie `pointsami`, qBit wywlaszcza (queuedDL) najnizszy aktywny `points` i puszcza
    `ratio`. Nowe torrenty qBit dodaje na DOL, wiec swiezy `ratio` reaktywnie wynosimy
    (topPrio jest 'lepki' - raz podniesiony zostaje nad pozniej dodanymi `points`).
    Wymaga wlaczonego kolejkowania w qBit (Preferences -> Queueing)."""
    global _last_prio_n
    if DRY:
        return
    body = qget("torrents/info", {"category": RATIO_CATEGORY})
    tors = json.loads(body.decode("utf-8", "replace")) if body else []
    hashes = [t["hash"] for t in tors if float(t.get("progress", 1)) < 1.0]
    if hashes:
        qpost("torrents/topPrio", {"hashes": "|".join(hashes)})
    if len(hashes) != _last_prio_n:
        log.info("prio: %d niedociagnietych `ratio` -> top kolejki", len(hashes))
        _last_prio_n = len(hashes)


def priority_enforcer():
    """Osobny watek daemon: co PRIO_TICK s wynosi `ratio` na top - niezaleznie od
    crawla (ktory potrafi trwac minuty). Swiezy release z autobrr wskakuje na gore
    w <=PRIO_TICK s, nawet w srodku crawla `points`."""
    while True:
        try:
            enforce_ratio_prio()
        except Exception as e:
            log.warning("prio enforce failed: %s", e)
        time.sleep(PRIO_TICK)


# ---------------------------------------------------------------- cycle
def cycle(state):
    state["_had_429"] = False
    if not DRY:
        ensure_category()
        tors = points_torrents()
        reaped = reap_stalled(tors)
        tors = [t for t in tors if t["hash"] not in reaped]
        used_gb = sum(int(t.get("size", 0)) for t in tors) / GB
        held_names = {norm_name(t.get("name", "")) for t in tors}
    else:
        tors, used_gb, held_names = [], 0.0, set()
    state["_used_frac"] = (used_gb / BUDGET_GB) if BUDGET_GB else 1.0
    log.info("points: %d torrents, %.1f/%.0f GB used (delay=%.1fs)",
             len(tors), used_gb, BUDGET_GB, state["delay"])
    if used_gb >= BUDGET_GB:
        relax_delay(state); save_state(state)
        log.info("budget reached - skip grab"); return

    grabbed = state["grabbed"]
    needed = BUDGET_GB - used_gb
    cands = crawl(state, needed, grabbed, held_names)
    cands.sort(reverse=True)
    floor = min_score_dyn(state, state["_used_frac"])
    log.info("candidates=%d floor=%.2f score_res=%d regions=%d free=%.0fGB",
             len(cands), floor, len(state["score_res"]), len(state["regions"]), needed)

    added = 0
    for sc, tid, sz, name, cat in cands:
        if used_gb >= BUDGET_GB:
            break
        if used_gb + sz / GB > BUDGET_GB:
            continue
        if str(tid) in grabbed:
            continue
        if DRY:
            log.info("[DRY] +%d %.2fGB score=%.1f cat=%d %s", tid, sz / GB, sc, cat, name[:55])
            grabbed[str(tid)] = {"size": sz, "score": round(sc, 3), "name": name, "cat": cat}
            used_gb += sz / GB; added += 1; continue
        try:
            fb = pt_download(state, tid)
            st, rbody = qadd(fb, "%d.torrent" % tid)
            if st == 200:
                try:
                    ok = bool(json.loads(rbody.decode() or "{}").get("added_torrent_ids"))
                except Exception:
                    ok = b"Ok" in rbody
                grabbed[str(tid)] = {"size": sz, "score": round(sc, 3), "name": name, "cat": cat}
                if ok:
                    used_gb += sz / GB; added += 1
                    log.info("added %d %.2fGB score=%.1f cat=%d %s | %.1f/%.0f GB",
                             tid, sz / GB, sc, cat, name[:45], used_gb, BUDGET_GB)
                else:
                    log.info("qbit already had %d", tid)
            else:
                log.warning("qbit add %d -> HTTP %s %s", tid, st, rbody[:80])
        except Exception as e:
            log.warning("grab %d failed: %s", tid, e)
        time.sleep(ADD_SLEEP * random.uniform(0.75, 1.25))

    if not state["_had_429"]:
        relax_delay(state)
    save_state(state)
    log.info("cycle done: +%d torrents, %.1f/%.0f GB, delay=%.1fs", added, used_gb, BUDGET_GB, state["delay"])


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not API_KEY or not RSSKEY:
        log.error("PTE_API_KEY and PTE_RSSKEY are required")
        raise SystemExit(1)
    log.info("start: cats=%s min_seeders=%d budget=%.0fGB interval=%dmin "
             "self-tuning(score/min-size/pages) delay0=%.1fs dry=%s",
             CATS, MIN_SEED, BUDGET_GB, INTERVAL // 60, PAGE_SLEEP, DRY)
    state = load_state()
    if DRY:
        cycle(state)
        return
    threading.Thread(target=priority_enforcer, daemon=True).start()
    while True:
        try:
            cycle(state)
        except Exception as e:
            log.exception("cycle error: %s", e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
