# monitoring-stack

Prometheus, Grafana, Loki, Alloy, blackbox-exporter, cAdvisor, node-exporter and the
exporter fleet on `192.168.0.212`, deployed by Portainer stack `18` straight from
`main` (git auto-update polls every 5 minutes, so a merge to `main` *is* a redeploy
of `docker-compose.yml`). The config content the containers read at runtime is a
separate concern — see *What lives where* below.

## What lives where

`docker-compose.yml` is the only file Portainer's AutoUpdate acts on directly.
Everything the containers read at runtime — scrape targets, log pipelines,
retention, datasources, credentials — is bind-mounted from
`/home/rysiu/monitoring/` on the host and lives outside this repository unless
noted otherwise below. Docker will silently create a directory in place of any
missing bind source, so every path here must exist before the stack starts.

| Host path | Mounted at | Used by | Source of truth |
| --- | --- | --- | --- |
| `/home/rysiu/monitoring/prometheus.yml` | `/etc/prometheus/prometheus.yml` | prometheus | git — `monitoring-stack/config/prometheus.yml` |
| `/home/rysiu/monitoring/blackbox.yml` | `/etc/blackbox_exporter/config.yml` | blackbox-exporter | git — `monitoring-stack/config/blackbox.yml` |
| `/home/rysiu/monitoring/loki-config.yaml` | `/etc/loki/local-config.yaml` | loki | git — `monitoring-stack/config/loki-config.yaml` |
| `/home/rysiu/monitoring/alloy/config.alloy` | `/etc/alloy/config.alloy` | alloy | git — `monitoring-stack/config/alloy/config.alloy` |
| `/home/rysiu/monitoring/grafana/provisioning/datasources/*.yml` | `/etc/grafana/provisioning/datasources/*.yml` | grafana | git — `monitoring-stack/config/grafana/provisioning/datasources/` |
| `/home/rysiu/monitoring/secrets/ha_token` | `/etc/prometheus/ha_token` | prometheus (Home Assistant) | host-only |
| `/home/rysiu/monitoring/secrets/k3s/` | `/etc/prometheus/k3s` | prometheus (ci-k3s-01 jobs) | host-only |
| `/home/rysiu/monitoring/secrets/{sonarr,radarr,prowlarr,bazarr}_apikey` | `/secret/apikey` | exportarr-* | host-only |

The five "git" rows are versioned here under `monitoring-stack/config/` and
pushed to the host explicitly with [`apply-config.sh`](./apply-config.sh) —
AutoUpdate never touches them, so a merge to `main` alone does **not** apply a
config change. Each host file carries a header comment pointing back here and
telling whoever is looking at it on the host not to edit it in place. The
`host-only` rows are secrets and are never committed (see below).

## Why host binds, not checkout binds

Portainer deploys this stack from a git checkout it materialises under
`/data/compose/<stackId>/<commitSha>/monitoring-stack` inside the Portainer
container itself — a new directory every commit (`docker inspect prometheus`
shows `com.docker.compose.project.config_files` pointing at one such path;
`/var/lib/docker/volumes/portainer_data/_data/compose/18/` has one directory
per SHA). A bind mount can't target a path that moves on every deploy, and a
*relative* repo bind wouldn't help either — it resolves inside the Portainer
container, not on the docker host, the same trap already documented in
`pte-points-stack/docker-compose.yml`. So `docker-compose.yml` keeps its
absolute `/home/rysiu/monitoring/...` binds, and git becomes the source of
truth for config *content* only, applied out-of-band by the script below.

## Host prerequisites, never in git

These exist only on `.212` and are never committed (`.gitignore` excludes
`secrets/`, `*_apikey`, `ha_token`, `*.env`):

- `/home/rysiu/monitoring/secrets/ha_token`
- `/home/rysiu/monitoring/secrets/k3s/{ci-k3s-01.token,ci-k3s-01-ca.crt}` — see *K3s scrape credentials* below
- `/home/rysiu/monitoring/secrets/sonarr_apikey`
- `/home/rysiu/monitoring/secrets/radarr_apikey`
- `/home/rysiu/monitoring/secrets/prowlarr_apikey`
- `/home/rysiu/monitoring/secrets/bazarr_apikey`
- `/home/rysiu/monitoring/secrets/grafana_admin_pw`
- `/home/rysiu/monitoring/secrets/adguard.env`
- the `ADGUARD_PASSWORD` Portainer stack environment variable

`secrets/` must stay readable only by `rysiu`; `secrets/k3s/` is narrower
still (`root:nogroup 0750` dir, `0440` files — see below).

## K3s scrape credentials

The jobs `kube-state-metrics-ci-k3s-01`, `kubelet-cadvisor-ci-k3s-01` and
`arc-listener-ci-k3s-01` scrape the CI cluster (`ci-k3s-01`, `192.168.0.66:6443`)
through the kube-apiserver proxy. They authenticate with the
`prometheus-external-scrape` ServiceAccount token and verify the K3s server CA:

```
/home/rysiu/monitoring/secrets/k3s/          root:nogroup 0750
├── ci-k3s-01.token                          root:nogroup 0440
└── ci-k3s-01-ca.crt                         root:nogroup 0440
```

The container runs as uid/gid `65534` (`nobody`/`nogroup`); group `nogroup` on the
host is also gid `65534`, which is why `0750`/`0440` plus a `:ro` bind is readable.
`node-ci-k3s-01` scrapes `:9100` directly and needs no credentials.

Never commit these files. Recreate them from the cluster (run on `ci-k3s-01`, then
copy to `.212`; nothing is printed to the terminal):

```bash
umask 077
tmp=$(mktemp -d)
sudo k3s kubectl -n monitoring get secret prometheus-external-scrape-token \
  -o jsonpath='{.data.token}' | base64 -d > "$tmp/ci-k3s-01.token"
sudo k3s kubectl -n monitoring get secret prometheus-external-scrape-token \
  -o jsonpath='{.data.ca\.crt}' | base64 -d > "$tmp/ci-k3s-01-ca.crt"
scp "$tmp"/ci-k3s-01.* rysiu@192.168.0.212:/tmp/
rm -rf "$tmp"
```

On `192.168.0.212`:

```bash
sudo install -d -o root -g nogroup -m 0750 /home/rysiu/monitoring/secrets/k3s
sudo install -o root -g nogroup -m 0440 /tmp/ci-k3s-01.token \
  /home/rysiu/monitoring/secrets/k3s/ci-k3s-01.token
sudo install -o root -g nogroup -m 0440 /tmp/ci-k3s-01-ca.crt \
  /home/rysiu/monitoring/secrets/k3s/ci-k3s-01-ca.crt
shred -u /tmp/ci-k3s-01.token /tmp/ci-k3s-01-ca.crt
docker restart prometheus
```

The CA is also available as `/var/lib/rancher/k3s/server/tls/server-ca.crt` on
`ci-k3s-01`; it is the same file.

### Verifying the K3s jobs

```bash
docker inspect prometheus --format '{{json .Mounts}}' | tr ',' '\n' | grep -i k3s
docker exec prometheus md5sum /etc/prometheus/k3s/ci-k3s-01-ca.crt
curl -s 'localhost:9090/api/v1/targets?state=any' \
  | jq -r '.data.activeTargets[] | select(.labels.job|test("ci-k3s-01"))
           | "\(.labels.job) \(.health) [\(.lastError)]"'
```

All four `ci-k3s-01` jobs must report `up` with an empty error.

## Apply / check procedure

```sh
./monitoring-stack/apply-config.sh          # validate candidates in the live containers, upload, reload
./monitoring-stack/apply-config.sh --check  # compare repo vs host, change nothing; exit 3 on drift
```

Target a different host with `MONITORING_HOST=user@host ./apply-config.sh`.

`--check` also warns about any unversioned `*.bak*`/`*.backup*`/
`*.before-*`/`docker-compose*` sidecar files it finds directly under the
host directory — those should be folded into a new attic archive (see
below) and removed, not left to drift silently next to the real configs.

Each changed file is validated inside its live container before being
uploaded (`promtool check config`, `loki -verify-config`, `alloy fmt`), then
reloaded with the narrowest mechanism that config supports:

| file | reload |
|---|---|
| `prometheus.yml` | `POST localhost:9090/-/reload` (`--web.enable-lifecycle`) |
| `blackbox.yml` | `docker kill -s HUP blackbox-exporter` |
| `loki-config.yaml` | `docker restart loki` — no runtime reload exists, so this is a real (short) ingestion gap |
| `alloy/config.alloy` | `POST localhost:12345/-/reload` |
| `grafana/provisioning/datasources/*` | `docker restart grafana` — provisioning is read only at boot |

## Attic

`/home/rysiu/monitoring-config-attic-20260824T2240Z.tar.gz` on `.212` holds
the 11 unversioned sidecar files that used to sit next to the real configs
(hand-made `*.bak*`/`*.backup*`/`*.before-*` copies of `prometheus.yml`,
`loki-config.yaml` and `config.alloy`, plus the dead pre-GitOps
`docker-compose.portainer.yml*` and `docker-compose.yml.pre-portainer-cli.bak`
copies nothing reads anymore). They are archived, not deleted outright, in
case any of them turns out to hold a since-lost fix; the archive lives
outside `/home/rysiu/monitoring/` so it is never picked up by `apply-config.sh`'s
sidecar warning or mistaken for a live config.
