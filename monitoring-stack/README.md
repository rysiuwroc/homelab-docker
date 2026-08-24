# monitoring-stack

Prometheus, Grafana, Loki, Alloy, cAdvisor, node-exporter and the exporter fleet on
`192.168.0.212`, deployed by Portainer stack `18` straight from `main`
(git auto-update polls every 5 minutes, so a merge to `main` *is* a redeploy).

## Host-resident files

Every path below is bind-mounted by `docker-compose.yml` and lives outside this
repository. They must exist before the stack starts; Docker will otherwise create a
directory in their place and the affected service will come up misconfigured.

| Host path | Mounted at | Used by |
| --- | --- | --- |
| `/home/rysiu/monitoring/prometheus.yml` | `/etc/prometheus/prometheus.yml` | prometheus |
| `/home/rysiu/monitoring/secrets/ha_token` | `/etc/prometheus/ha_token` | prometheus (Home Assistant) |
| `/home/rysiu/monitoring/secrets/k3s/` | `/etc/prometheus/k3s` | prometheus (ci-k3s-01 jobs) |
| `/home/rysiu/monitoring/blackbox.yml` | `/etc/blackbox_exporter/config.yml` | blackbox-exporter |
| `/home/rysiu/monitoring/loki-config.yaml` | `/etc/loki/local-config.yaml` | loki |
| `/home/rysiu/monitoring/alloy/config.alloy` | `/etc/alloy/config.alloy` | alloy |
| `/home/rysiu/monitoring/grafana/provisioning` | `/etc/grafana/provisioning` | grafana |
| `/home/rysiu/monitoring/secrets/{sonarr,radarr,prowlarr,bazarr}_apikey` | `/secret/apikey` | exportarr-* |

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

## Verifying the K3s jobs

```bash
docker inspect prometheus --format '{{json .Mounts}}' | tr ',' '\n' | grep -i k3s
docker exec prometheus md5sum /etc/prometheus/k3s/ci-k3s-01-ca.crt
curl -s 'localhost:9090/api/v1/targets?state=any' \
  | jq -r '.data.activeTargets[] | select(.labels.job|test("ci-k3s-01"))
           | "\(.labels.job) \(.health) [\(.lastError)]"'
```

All four `ci-k3s-01` jobs must report `up` with an empty error.
