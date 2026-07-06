# pte-points-stack

Farmer punktów bonusowych **PolishTrackera**. Seeduje dużo małych, rzadkich,
starszych filmów/seriali w dedykowanej kategorii qBit `points` na F: (media-f),
seed-only (nie importowane do Plex/Jellyfin).

## Dlaczego tak (wzór punktów PT)

Punkty rosną z: liczbą torrentów `n`, czasem trzymania (waga `w` → do 4× po 360
dniach), **małym rozmiarem** (najlepszy stosunek punkty/GB — stała `1+GB` liczona
per torrent), **rzadkością** (`f<3` → mnożnik ×2, ale `f≥1` żeby się dociągnęło)
i wiekiem na trackerze `t`. Więc: **dużo małych rzadkich, trzymane wiecznie.**
To osobna gra niż ratio (autobrr łapie świeże na ratio; tu farmimy punkty).

## Jak działa `grabber.py`

Pętla co `INTERVAL_MIN`:
1. crawluje API PT (`api-test.pte.nu`, nagłówek `API-Key`), rotując kursor po
   stronach `PAGE_MIN..PAGE_MAX` (najstarsze = najwyższe `tpage`), z backoffem na 429;
2. filtruje: kategorie film/serial (`CATS`), `size < MAX_SIZE_GB`, `seeders` w
   `[MIN,MAX]`, pomija już snatchowane (pola API `seeder`/`progress`) i lokalny stan;
3. rankuje po punkty/GB (`wiek·(1+GB)/GB` — preferuje stare i małe);
4. pobiera `.torrent` (`pte.nu/downrss/{rsskey}/{id}`) i dodaje do qBit
   (`qbittorrent:8080`, subnet-whitelist → bez hasła) w kategorii `points`,
   savepath na F:, do `BUDGET_GB`, max `BATCH` na cykl;
5. nuke'uje (z plikami) **utknięte** niedociągnięte graby (`stalledDL`/`metaDL`,
   brak aktywności > `REAP_STALL_HOURS`) — nigdy ukończonych seedów ani `stalledUP`
   (bezczynny seed = cel). guard REAP w scratch-guardzie tyka tylko `ratio`, nie `points`.

Stan (zgrane id + kursor) w wolumenie `pte_points_state:/state`. Skrypt = tylko
biblioteka standardowa Pythona (obraz `python:3.13-slim`, bez builda), **bind-owany
z hosta** `/home/rysiu/pte-points/grabber.py` (relatywne bind-y repo nie działają w
git-stackach Portainera). Repo = źródło prawdy; zmiana skryptu = edytuj tu i `scp`
na host, potem redeploy stacku.

## Sekrety (NIE w gicie)

`PTE_API_KEY` (Profile → API na pte.nu) i `PTE_RSSKEY` (do URL pobierania) ustaw
w **Portainer → stack `pte-points-stack` → env**. Compose ma je jako `${...:-}`
(nie-mandatory — CI compose-config nie ma tych env), a **twardą walidację wymusza
`grabber.py`** (`exit(1)` gdy brak). Wartości w `private/homelab-access.md` bazy wiedzy.

## Strojenie

Wszystko przez env w compose/Portainer: `BUDGET_GB`, `MAX_SIZE_GB`,
`MIN_SEEDERS`/`MAX_SEEDERS`, `CATS`, `BATCH`, `INTERVAL_MIN`, `PAGE_MIN/MAX`,
`PAGES_PER_CYCLE`, `PAGE_SLEEP_SEC`, `REAP_STALL_HOURS`. `DRY_RUN=1` = tylko
loguje co by dodał (bez qBit).
