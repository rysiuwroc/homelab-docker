# pte-points-stack

Farmer punktów bonusowych **PolishTrackera**. Seeduje dużo małych, rzadkich,
starszych torrentów (filmy/seriale + gry/konsole) w dedykowanej kategorii qBit
`points` na F: (media-f), seed-only (nie importowane do Plex/Jellyfin).

## Dlaczego tak (wzór punktów PT)

Realny wzór PT (suma po `n` zasiadanych torrentach):

`Σ ( log(∛n/2) + (1 + s·1e-9)·(t/311e5)^((1/2)^k) ) · w`

gdzie `s`=rozmiar w bajtach, `t`=wiek na trackerze w sekundach (`311e5`≈360 dni),
`f`=seedery, `w`=waga trzymania (1→4 po ~360 dniach), `k=0` gdy `f≥3`, `k=1` gdy
`f<3`. **Rzadkość to WYKŁADNIK `(1/2)^k` na wieku** (`f≥3` → wiek liniowo, `f<3` →
pierwiastek z wieku), **nie mnożnik ×2** (jak błędnie podawała starsza wersja
tego dokumentu).

Do rankingu w kodzie wyprowadzony jest punkty-na-GB:

`score(s,t,f) = (1+GB)/GB · (t/311e5)^((1/2)^k)`

`log(∛n/2)` i `w` są pominięte — w jednym cyklu są wspólne dla wszystkich
kandydatów (to samo `n`; świeżo zgrany torrent ma `z≈0` → `w=1`), więc nie
zmieniają rankingu. Wniosek: **dużo małych, starych, żywych torrentów, trzymane
wiecznie.** To osobna gra niż ratio (autobrr łapie świeże na ratio; tu farmimy
punkty).

## Jak działa `grabber.py`

Pętla co `INTERVAL_MIN` (domyślnie 15 min):
1. reap utkniętych + policzenie zajętości kategorii `points` → nacisk budżetu
   (`used/BUDGET_GB`);
2. **adaptacyjny crawl** — per-kategoria wyszukiwanie wydajnych regionów osi
   `tpage` (bandit: eksploatuj region o najlepszym yieldzie, z prawdopodobieństwem
   `ε` eksploruj losowy/graniczny region żeby śledzić dryf trackera; przy
   pierwszym uruchomieniu rzadki zwiad całego landscape'u), twardy limit
   `PB_HARD` stron/cykl, adaptacyjne opóźnienie świadome 429 (rośnie na 429 wg
   `Retry-After`, maleje po czystych cyklach);
3. **progi auto** — próg score to percentyl rezerwuaru żywych kandydatów,
   skalowany naciskiem budżetu (pusty budżet → niski percentyl, bierz szeroko;
   pełny → wysoki percentyl, tylko top wypełnia zwolnione miejsce); min-size per
   kategoria to niski percentyl żywych rozmiarów w tej właśnie kategorii (odsiewa
   mikro/śmieciowy ogon względem normy kategorii — retro ROM to sens w grach,
   śmieć w filmach);
4. dedup względem trwałego stanu grabbera (raz zgrany id nigdy nie wraca) +
   realnej zawartości qBit `points` (po znormalizowanej nazwie); ranking po
   score, dobór torrentów do `BUDGET_GB`, pobranie `.torrent`
   (`pte.nu/downrss/{rsskey}/{id}`) i dodanie do qBit (`qbittorrent:8080`,
   subnet-whitelist → bez hasła) w kategorii `points`, savepath na F:;
5. nuke'uje (z plikami) **utknięte** niedociągnięte graby (`stalledDL`/`metaDL`,
   bezczynne > `REAP_STALL_MIN`, domyślnie **20 min**) — nigdy ukończonych
   seedów ani `stalledUP` (bezczynny seed = cel). Guard REAP w scratch-guardzie
   tyka tylko `ratio`.

Stan (schema v2, auto-migruje z v1 listy id) w wolumenie `pte_points_state:/state`
trzyma: zgrane id (+ rozmiar/score/nazwa/kategoria), nauczone regiony `tpage`
per kategoria, rezerwuary score/rozmiarów, bieżące opóźnienie i kursory skanu
per kategoria. Skrypt = tylko biblioteka standardowa Pythona (obraz
`python:3.13-slim`, bez builda), **bind-owany z hosta**
`/home/rysiu/pte-points/grabber.py` (relatywne bind-y repo nie działają w
git-stackach Portainera). Repo = źródło prawdy; zmiana skryptu = edytuj tu i
`scp` na host, potem redeploy stacku.

## Sekrety (NIE w gicie)

`PTE_API_KEY` (Profile → API na pte.nu) i `PTE_RSSKEY` (do URL pobierania) ustaw
w **Portainer → stack `pte-points-stack` → env**. Compose ma je jako `${...:-}`
(nie-mandatory — CI compose-config nie ma tych env), a **twardą walidację wymusza
`grabber.py`** (`exit(1)` gdy brak). Wartości w `private/homelab-access.md` bazy wiedzy.

## Strojenie

Operacyjne przez env w compose/Portainer: `BUDGET_GB` (jedyny twardy limit —
suma GB w kategorii `points`), `MIN_SEEDERS` (dolny próg żywotności, żeby się
dociągnęło — nie knob punktowy), `CATS` (kategorie do farmienia: filmy/seriale
i gry/konsole), `INTERVAL_MIN`, `ADD_SLEEP_SEC`, `REAP_STALL_MIN`,
`PAGE_SLEEP_SEC` (początkowe/dolne opóźnienie między requestami — limiter uczy
się realnego tempa z odpowiedzi 429), `DRY_RUN=1` (tylko loguje co by dodał,
bez qBit).

Zakres stron (`tpage`), próg score i limity rozmiaru pliku są **auto**
(samostrojące w kodzie na podstawie próbek na żywo) — celowo brak ręcznego
strojenia tych trzech.
