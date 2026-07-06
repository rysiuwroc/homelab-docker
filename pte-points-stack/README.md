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

Pętla co `INTERVAL_MIN` (domyślnie 15 min):
1. reap + policzenie zajętości `points`, potem crawl API PT (`api-test.pte.nu`,
   nagłówek `API-Key`), rotując kursor po `PAGE_MIN..PAGE_MAX` (najstarsze =
   najwyższe `tpage`), z backoffem na 429; **crawl kończy się gdy uzbiera kandydatów
   na wolne miejsce do budżetu** (adaptacyjnie, cap `PAGES_MAX`);
2. filtruje: kategorie film/serial (`CATS`), `size < MAX_SIZE_GB`, `seeders` w
   `[MIN,MAX]` (domyślnie **2-2**: ≥2 żeby nie stallowało, ≤2 żeby trzymać `f<3`→×2),
   pomija już snatchowane (pola API `seeder`/`progress`) i lokalny stan;
3. rankuje po punkty/GB (`wiek·(1+GB)/GB` — preferuje stare i małe);
4. pobiera `.torrent` (`pte.nu/downrss/{rsskey}/{id}`) i dodaje do qBit
   (`qbittorrent:8080`, subnet-whitelist → bez hasła) w kategorii `points`, savepath
   na F:, **do `BUDGET_GB` bez limitu na cykl** (`BATCH=0`); gdy stalle zwolnią
   miejsce, kolejny cykl uzupełnia na bieżąco;
5. nuke'uje (z plikami) **utknięte** niedociągnięte graby (`stalledDL`/`metaDL`,
   bezczynne > `REAP_STALL_MIN`, domyślnie **20 min**) — nigdy ukończonych seedów ani
   `stalledUP` (bezczynny seed = cel). guard REAP w scratch-guardzie tyka tylko `ratio`.

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
`MIN_SEEDERS`/`MAX_SEEDERS`, `CATS`, `BATCH` (0=bez limitu), `INTERVAL_MIN`,
`PAGE_MIN/MAX`, `PAGES_MAX`, `PAGE_SLEEP_SEC`, `ADD_SLEEP_SEC`, `REAP_STALL_MIN`.
`DRY_RUN=1` = tylko loguje co by dodał (bez qBit).
