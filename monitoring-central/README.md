# monitoring-central (`mon-01`, `192.168.0.30`)

Centrala monitoringu: Prometheus, Grafana, Loki, exportery `arr`, adguard-exporter,
blackbox-exporter, plus wlasny `node-exporter`/`cadvisor` mierzacy sama `mon-01`.

Stoi na VM `mon-01` pod Proxmoksem `192.168.0.28`. Wczesniej caly monitoring dzialal na
`192.168.0.212` - czyli monitoring padal razem z monitorowanym hostem (awaria 2026-07-05).

**Ten katalog nie jest deployowany przez Portainera.** `mon-01` uruchamia go bezposrednio:

```sh
cd /home/rysiu/monitoring && docker compose up -d
```

## Podzial hostow

| Host | Co tam stoi | Dlaczego |
| --- | --- | --- |
| `mon-01` (`.30`) | prometheus, grafana, loki, exportarr-*, adguard, blackbox | centrala - nie moze umierac razem z mierzonym hostem |
| `.212` | node-exporter, cadvisor, alloy | agenty - musza byc na mierzonym hoscie |

Exportery `arr` moglyby stac gdziekolwiek: celuja w `http://192.168.0.212:PORT` po IP,
nie po nazwie kontenera.

## Targety zalezne od hosta

Po podziale trzy zadania w `config/prometheus.yml` musialy zmienic adres, inaczej
zbieralyby dane z zlego hosta pod stara etykieta:

| Job | Bylo | Jest | Etykieta |
| --- | --- | --- | --- |
| `cadvisor` | `cadvisor:8080` | `192.168.0.212:8080` | `host: dockerhost` |
| `alloy` | `alloy:12345` | `192.168.0.212:12345` | `host: docker-host` |
| `loki` | `loki:3100` | `loki:3100` (bez zmian) | `host: mon-01` |

Doszly `node-mon01` i `cadvisor-mon01` dla samej `mon-01`.

## Config i sekrety

Bind-mounty z `/home/rysiu/monitoring/`, tak samo jak na `.212`. Wersjonowane pliki
wypycha [`apply-config.sh`](./apply-config.sh) (domyslny host: `rysiu@192.168.0.30`).

`ADGUARD_PASSWORD` czytany jest z `/home/rysiu/monitoring/.env` (`0600`, poza repo) -
wczesniej byl trzymany w zmiennych srodowiskowych stacka w Portainerze.

Sekrety w `/home/rysiu/monitoring/secrets/` nigdy nie trafiaja do repo. Katalog `k3s/`
musi byc `root:nogroup 0750` z plikami `0440` - Prometheus dziala jako `nobody:nogroup`.
