# monitoring-stack (agenty hosta, `192.168.0.212`)

Agenty, ktore MUSZA stac na mierzonym hoscie.

## UWAGA: ten katalog nie jest dzis deployowany przez Portainera

Zmierzone 2026-09-06 w `portainer.db`: stack `18` ("monitoring") ma
**`AutoUpdate: null` i `GitConfig: null`** - nie jest powiazany z gitem, w
przeciwieznosci do stackow `16` (arr) i `17` (jellyseerr), ktore maja
`Interval: 5m`. Poprzednia wersja tego README twierdzila, ze merge do `main`
*jest* redeployem - **to nieprawda dla tego stacka**.

Co wiecej, zywe kontenery byly wdrozone z
`/data/compose/18/7d1c3f6b4473.../monitoring-stack/docker-compose.yml`, a tego
katalogu **juz nie ma**. Najnowszy materializowany checkout (`v54`) ma pliki
0-bajtowe. Kliknietie "Update the stack" w Portainerze wdrozylo by wiec pusty
albo niekompletny compose.

Dopoki to nie zostanie naprawione, agenty deployuje sie z hosta:

```sh
cd /home/rysiu/stacks/monitoring-agents && docker compose up -d --remove-orphans
```

Centrala monitoringu (Prometheus, Grafana, Loki, exportery) zostala przeniesiona na
`mon-01` (`192.168.0.30`, VM na Proxmoksie `.28`) - patrz [`../monitoring-central/`](../monitoring-central/).

## Dlaczego akurat te trzy zostaly

| Usluga | Dlaczego nie da sie przeniesc |
| --- | --- |
| `node-exporter` | montuje `/` hosta (`--path.rootfs=/host`), mierzy TEN system |
| `cadvisor` | czyta `/var/lib/docker` hosta, mierzy TUTEJSZE kontenery |
| `alloy` | zbiera logi dockera i journal z tego hosta **oraz** odbiera syslog z routera na `514/udp` |

Przeniesienie `alloy` wymagaloby przekonfigurowania routera `192.168.0.1`, zeby wysylal
syslog gdzie indziej. Nie robimy tego - agent zostaje przy zrodle logow.

## Co sie zmienilo przy podziale

- `cadvisor` **publikuje teraz port `8080`**. Wczesniej byl widoczny tylko w sieci
  compose'a pod nazwa `cadvisor:8080`, bo Prometheus stal obok. Teraz Prometheus
  scrape'uje go przez LAN z `192.168.0.30`.
- `alloy` pisze do Loki pod `http://192.168.0.30:3100`, nie `http://loki:3100`.
- Wolumeny `monitoring_{prom,grafana,loki}-data` nie sa juz tutaj uzywane. Zostaly na
  dysku `.212` jako kopia zapasowa migracji - mozna je usunac dopiero po potwierdzeniu,
  ze `mon-01` ma pelna historie.

## Config

Jedyny wersjonowany config to `config/alloy/config.alloy`, bind-mountowany z
`/home/rysiu/monitoring/alloy/config.alloy`. AutoUpdate go NIE dotyka - wypycha go
[`apply-config.sh`](./apply-config.sh).

```sh
./apply-config.sh --check   # porownaj repo z hostem, nic nie zmieniaj (exit 3 przy dryfie)
./apply-config.sh           # zwaliduj w zywym kontenerze, wyslij, przeladuj
```

Sekrety (`/home/rysiu/monitoring/secrets/`) nigdy nie trafiaja do repo.
