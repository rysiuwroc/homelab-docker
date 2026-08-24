#!/usr/bin/env sh
# Push the git copy of the monitoring configs to the docker host and reload
# only what changed.  The configs live in git (source of truth) but are bind-
# mounted from /home/rysiu/monitoring on .212, because Portainer materialises
# the git checkout under /data/compose/<stackId>/<commitSha>/ — a path that
# changes with every commit, so no bind can point at it.  See README.md.
#
#   ./apply-config.sh --check   compare repo vs host, change nothing (exit 3 on drift)
#   ./apply-config.sh           validate candidates in the live containers, upload, reload
#
# Override the target with MONITORING_HOST=user@host.
set -eu

HOST="${MONITORING_HOST:-rysiu@192.168.0.212}"
DEST=/home/rysiu/monitoring
CFG="$(CDPATH= cd -- "$(dirname -- "$0")/config" && pwd)"
FILES="prometheus.yml blackbox.yml loki-config.yaml alloy/config.alloy grafana/provisioning/datasources/prometheus.yml grafana/provisioning/datasources/loki.yml"

MODE=apply
[ "${1:-}" = "--check" ] && MODE=check

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
cd "$CFG"
md5sum -t $FILES > "$tmp/local"
ssh "$HOST" "cd $DEST && md5sum -t $FILES 2>/dev/null" > "$tmp/remote" || true

changed=""
for f in $FILES; do
  l=$(awk -v p="$f" '$2==p {print $1}' "$tmp/local")
  r=$(awk -v p="$f" '$2==p {print $1}' "$tmp/remote")
  if [ "$l" = "$r" ]; then
    echo "same   $f"
  else
    echo "DRIFT  $f (repo $l / host ${r:-absent})"
    changed="$changed $f"
  fi
done

stray=$(ssh "$HOST" "find $DEST -maxdepth 2 -type f \\( -name '*.bak*' -o -name '*.backup*' -o -name '*.before-*' -o -name 'docker-compose*' \\) -printf '%P\\n'" || true)
[ -z "$stray" ] || { echo "WARN unversioned sidecars on the host:"; echo "$stray" | sed 's/^/  /'; }

if [ "$MODE" = check ]; then
  [ -z "$changed" ] || exit 3
  exit 0
fi
[ -n "$changed" ] || { echo "host already matches the repo"; exit 0; }

# Validate each candidate inside its live container: the credential files the
# configs reference (ha_token, k3s/*) exist only there.
for f in $changed; do
  case "$f" in
    prometheus.yml)
      scp -q prometheus.yml "$HOST:/tmp/cand-prom.yml"
      ssh "$HOST" 'docker cp /tmp/cand-prom.yml prometheus:/tmp/cand.yml && docker exec prometheus promtool check config /tmp/cand.yml' ;;
    loki-config.yaml)
      scp -q loki-config.yaml "$HOST:/tmp/cand-loki.yaml"
      ssh "$HOST" 'docker cp /tmp/cand-loki.yaml loki:/tmp/cand.yaml && docker exec loki loki -verify-config -config.file=/tmp/cand.yaml' ;;
    alloy/config.alloy)
      scp -q alloy/config.alloy "$HOST:/tmp/cand.alloy"
      ssh "$HOST" 'docker cp /tmp/cand.alloy alloy:/tmp/cand.alloy && docker exec alloy alloy fmt /tmp/cand.alloy >/dev/null' ;;
  esac
done

tar -cf - $changed | ssh "$HOST" "tar -xf - -C $DEST"

grafana=0
for f in $changed; do
  case "$f" in
    prometheus.yml)     ssh "$HOST" 'curl -fsS -X POST localhost:9090/-/reload' ;;
    blackbox.yml)       ssh "$HOST" 'docker kill -s HUP blackbox-exporter' ;;
    loki-config.yaml)   ssh "$HOST" 'docker restart loki' ;;
    alloy/config.alloy) ssh "$HOST" 'curl -fsS -X POST localhost:12345/-/reload' ;;
    grafana/*)          grafana=1 ;;
  esac
done
[ "$grafana" = 0 ] || ssh "$HOST" 'docker restart grafana'   # Grafana reads provisioning only at boot
echo "applied:$changed"
