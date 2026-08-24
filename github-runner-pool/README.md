# GitHub Actions runner pool

This stack runs exactly ten organization-scoped, universal GitHub Actions runners on the Ubuntu Docker host `192.168.0.212`:

| Container | GitHub runner | Persistent host root |
| --- | --- | --- |
| `github-runner-01` | `ci-linux-01` | `/opt/github-runner-pool/runners/01` |
| `github-runner-02` | `ci-linux-02` | `/opt/github-runner-pool/runners/02` |
| `github-runner-03` | `ci-linux-03` | `/opt/github-runner-pool/runners/03` |
| `github-runner-04` | `ci-linux-04` | `/opt/github-runner-pool/runners/04` |
| `github-runner-05` | `ci-linux-05` | `/opt/github-runner-pool/runners/05` |
| `github-runner-06` | `ci-linux-06` | `/opt/github-runner-pool/runners/06` |
| `github-runner-07` | `ci-linux-07` | `/opt/github-runner-pool/runners/07` |
| `github-runner-08` | `ci-linux-08` | `/opt/github-runner-pool/runners/08` |
| `github-runner-09` | `ci-linux-09` | `/opt/github-runner-pool/runners/09` |
| `github-runner-10` | `ci-linux-10` | `/opt/github-runner-pool/runners/10` |

All runners are in the `MRysiukiewicz` organization and `linux-docker-ci` group, with labels `self-hosted`, `linux`, `x64`, and `docker`. Jobs run as the unprivileged `runner` user. The host Docker socket is used for job containers; Docker-in-Docker is intentionally not used.

## GitHub credential

Create a GitHub **fine-grained** PAT for the organization only, with the shortest practical expiration. Grant the organization permission **Self-hosted runners: Read and write**. Do not grant repository contents, administration, workflow, package, or other permissions. Authorize the token for organization SSO if GitHub requires it. The PAT is used only inside the entrypoint to request short-lived registration/removal tokens; it is never a Compose environment variable.

On `192.168.0.212`, provision a root-owned file outside this repository:

```bash
sudo install -d -o root -g root -m 0700 /opt/github-runner-pool/secrets
sudo install -o root -g root -m 0600 /dev/null /opt/github-runner-pool/secrets/github-runner-api-token
sudoedit /opt/github-runner-pool/secrets/github-runner-api-token
sudo stat -c '%a %U:%G %n' /opt/github-runner-pool/secrets/github-runner-api-token
```

The file must contain exactly the PAT on one line. Do not put the PAT in Git, Portainer stack environment, shell command history, or logs.

Before the first deployment, provision the ten host-visible runner roots. They must exist before Docker starts the containers because the Compose bind mounts intentionally refuse to create missing paths. Keep the roots private; the entrypoint changes each root to the container's dedicated `runner` UID and rejects symlinked work/cache paths before any root-owned setup:

```bash
sudo install -d -o root -g root -m 0700 /opt/github-runner-pool/runners
for runner in $(seq -w 1 10); do
  sudo install -d -o root -g root -m 0700 "/opt/github-runner-pool/runners/$runner"
done
```

## Portainer Git-stack deployment

1. Copy `.env.example` to a deployment-host `.env` file and set `RUNNER_SHA256` to the 64-hex SHA-256 published for the exact `RUNNER_VERSION` release. Before the first deployment and every runner-version update, build the pinned image on the Docker host:
   ```bash
   docker build --build-arg RUNNER_VERSION="$RUNNER_VERSION" --build-arg RUNNER_SHA256="$RUNNER_SHA256" -t "github-runner-pool:$RUNNER_VERSION" github-runner-pool/
   ```
   The Dockerfile refuses missing or malformed build arguments and verifies the downloaded archive before extraction. Portainer then deploys only the already-built, pinned image; this avoids its remote-agent BuildKit limitation.
2. Set `DOCKER_GID` to the host socket group ID (`stat -c '%g' /var/run/docker.sock`). Keep the host secret path in `GITHUB_RUNNER_API_TOKEN_FILE`.
3. In Portainer, choose **Stacks → Add stack → Git repository**, select this repository and the `github-runner-pool/docker-compose.yml` compose path, and name the stack `github-runner-pool`.
4. Add the non-secret values from `.env` to the Portainer stack environment. Do not add a PAT value. The compose file bind-mounts the host file read-only at `/run/secrets/github-runner-api-token`.
5. Deploy the stack and confirm all ten services register and become healthy. Each host root keeps that runner's installation, work directory, action runtime, and tool cache at the same absolute path that the host Docker daemon sees for job and service containers. The image contains only the pinned runner and general Linux CI tooling; Node, .NET, Java, Go, and Rust SDKs are deliberately absent.

For CI interpolation checks, use the token-free example (the placeholder checksum is only a config-time value; it is not a usable image build):

```bash
docker compose --env-file .env.example -f github-runner-pool/docker-compose.yml config --quiet
```

## Start, stop, logs, and health

Use the Portainer stack controls for normal start/stop/redeploy. On the host, equivalent commands from this directory are:

```bash
docker compose --env-file .env -f docker-compose.yml up -d
docker compose --env-file .env -f docker-compose.yml stop
docker compose --env-file .env -f docker-compose.yml logs --tail=100 -f github-runner-01
docker compose --env-file .env -f docker-compose.yml ps
docker inspect --format '{{json .State.Health}}' github-runner-01
```

A healthy container has a live runner process and `/run/runner-ready`. On `TERM`, the entrypoint stops the runner, requests a short-lived removal token internally, and deregisters it. Each runner root retains its configuration, work, action runtime, and tool cache across ordinary restarts. The identical host/container path is required for Docker job and service containers.

The entrypoint creates/reuses a group for `DOCKER_GID`, adds `runner` to it, and verifies the daemon before registration. This check prints no credential:

```bash
docker exec --user runner github-runner-01 id -Gn
docker exec --user runner github-runner-01 docker version --format '{{.Server.Version}}'
```

## Image update and rollback

Record the version/checksum pair used by every deployment. To update, set both `RUNNER_VERSION` and its matching `RUNNER_SHA256` in the deployment-host `.env`, run the pinned `docker build` command above on the Docker host, then use Portainer **Redeploy**. Never change one without the other. The image distribution is copied into each persistent runner root on startup.

To roll back, rebuild the previously recorded version/checksum pair, restore those values in the Portainer stack environment, and redeploy. If a runner release has already changed files in a runner root, stop the stack before the rollback and redeploy without deleting the roots. If GitHub refuses an old runner binary, use the previous known-good release pair and re-register during the maintenance window.

## Adding an eleventh runner

Do not reuse an existing runner root or name. In a reviewed change, copy one service block to `github-runner-11`, set `RUNNER_NAME: ci-linux-11`, and give it the matching `/opt/github-runner-pool/runners/11` host root for `RUNNER_ROOT`, `RUNNER_WORKDIR`, and `RUNNER_TOOL_CACHE`. Create that host directory with owner `1001:1001` before deployment. Add the corresponding organization runner to the `linux-docker-ci` group and verify the GitHub Actions concurrency demand justifies the extra host capacity before deploying. The production manifest intentionally remains at ten services until this procedure is completed and reviewed.

## Safe cache and Docker cleanup

Never prune while a runner or a job it started is active. First pause workflow dispatches, wait for all organization jobs to finish, and confirm no pool or job containers are running. Then stop all ten services and verify the stop before touching the persistent runner roots:

```bash
docker compose --env-file .env -f docker-compose.yml stop
docker ps --filter name='github-runner-' --filter status=running
docker compose --env-file .env -f docker-compose.yml rm --force
```

The second command must print no running runner container, and the GitHub Actions organization page must show no active job. Only then may you clear disposable per-runner tool caches (never the runner roots, work directories, or runner configuration) and optionally prune old Docker build cache:

```bash
for runner in $(seq -w 1 10); do
  sudo rm -rf --one-file-system "/opt/github-runner-pool/runners/$runner/_tool"
done
docker builder prune --filter until=24h
```

Recreate the tool-cache directories by starting the stack with `up -d`. Do not use `docker system prune`, remove runner roots, or run a global image/container prune on a live host; those operations can destroy evidence or affect unrelated workloads.

## ARC package proxy cache

`nexus-package-cache.yaml` deploys an internal, persistent Nexus NuGet v3 proxy for ARC runners. It is not exposed outside the `arc-runners` namespace. The bootstrap sidecar accepts the Nexus Community EULA, creates the `nuget-proxy` repository, grants the anonymous client only read/browse access to that repository, and confirms a real `.nupkg` download before the Service becomes ready.

Before applying the manifest, provision the administrator password as a Kubernetes Secret. Generate it on the K3s host; do not print, commit, or place its value in shell history:

```bash
umask 077
password_file=$(mktemp)
openssl rand -base64 36 | tr -d '\n' >"$password_file"
sudo k3s kubectl create secret generic -n arc-runners nexus-admin-credentials \
  --from-file=admin-password="$password_file"
rm -f "$password_file"
```

Deploy and verify the proxy:

```bash
sudo k3s kubectl apply -f github-runner-pool/nexus-package-cache.yaml
sudo k3s kubectl rollout status deployment/nexus-package-cache -n arc-runners --timeout=420s
sudo k3s kubectl get pods -n arc-runners -l app=nexus-package-cache
```

The admin password remains only in the Kubernetes Secret and is used when future package proxy repositories need provisioning. Do not delete `nexus-data` except when intentionally discarding the package cache; doing so removes all cached packages and triggers the bootstrap again.

## ARC runner image prewarm

`arc-runner-values.yaml` prewarms the exact `.NET API tests` job-container and service-container image references — `registry-mcr-images.arc-runners.svc.cluster.local:5000/dotnet/sdk:10.0` and `postgres:17-alpine` — in parallel in each new runner's private DinD store before that runner registers. Its DinD graph uses a per-runner node-backed `emptyDir` mounted at `/var/lib/docker`, avoiding nested writable-layer copy-on-write while retaining ephemeral runner isolation. The prewarm is best-effort: a registry failure is logged but never prevents runner registration; GitHub Actions will then pull the image in the job as usual.

The value file explicitly targets the existing `arc-gha-rs-controller` ServiceAccount in `arc-systems`, so Helm does not rely on controller discovery. Apply a reviewed update with the pinned chart version:
Template changes apply only to replacement runner pods. Before an upgrade or rollout, confirm no runner is busy and never delete a busy pod: its pod-scoped DinD store is ephemeral and deleting it interrupts the job. After the Helm upgrade, replace only idle runner pods to apply the change and recreate their prewarm cache.


```bash
sudo env KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm upgrade linux-docker-ci \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set \
  --version 0.14.2 --namespace arc-runners \
  --values github-runner-pool/arc-runner-values.yaml --wait --timeout 180s
```

After an update, verify that the replacement runner pods are ready and review the prewarm log from one runner:

```bash
sudo k3s kubectl wait -n arc-runners --for=condition=Ready pod \
  -l app.kubernetes.io/component=runner --timeout=240s
runner=$(sudo k3s kubectl get pod -n arc-runners -l app.kubernetes.io/component=runner \
  -o jsonpath='{.items[0].metadata.name}')
sudo k3s kubectl logs -n arc-runners "$runner" -c prewarm-ci-images
```

## Host overload protection (ci-k3s-01)

`ci-k3s-01` (`192.168.0.66`, Ubuntu 24.04, k3s v1.36.2+k3s1, 16 vCPU, Hyper-V dynamic memory) is the ARC/K3s host for the `linux-docker-ci` scale set. These six files keep K3s, SSH and node-exporter alive while up to six CI jobs saturate memory and I/O. They are host-resident: no stack redeploy ships them, so a host rebuild must reinstall them from this directory.

Install with `sudo install -D -o root -g root -m 0644 <repo file> <host path>` (all six live host files are `0644 root:root`).

| Repo file | Host path | Activation |
| --- | --- | --- |
| `host/k3s-overload-protection.yaml` | `/etc/rancher/k3s/config.yaml` | `sudo systemctl restart k3s` — **interrupts every running job** |
| `host/k3s-overload-protection.conf` | `/etc/systemd/system/k3s.service.d/99-overload-protection.conf` | `sudo systemctl daemon-reload && sudo systemctl restart k3s` — **interrupts every running job** |
| `host/ssh-overload-protection.conf` | `/etc/systemd/system/ssh.service.d/99-overload-protection.conf` | `sudo systemctl daemon-reload && sudo systemctl restart ssh` (keep a second session open; existing sessions survive) |
| `host/node-exporter-overload-protection.conf` | `/etc/systemd/system/prometheus-node-exporter.service.d/99-overload-protection.conf` | `sudo systemctl daemon-reload && sudo systemctl restart prometheus-node-exporter` |
| `host/system-slice-overload-protection.conf` | `/etc/systemd/system/system.slice.d/99-overload-protection.conf` | `sudo systemctl daemon-reload`, then confirm `/sys/fs/cgroup/system.slice/memory.min` is `637534208`; if it did not change, `sudo systemctl set-property system.slice MemoryMin=608M MemoryLow=1216M` |
| `host/prometheus-node-exporter.defaults` | `/etc/default/prometheus-node-exporter` | `sudo systemctl restart prometheus-node-exporter` (brief scrape gap; the `node-ci-k3s-01` target on `.212` flaps for one interval) |

`/etc/rancher/k3s/config.yaml` is the **whole** k3s config file on this host, not a fragment, so a future k3s setting is added to this repo file rather than appended on the host.

Why the numbers are what they are:

- `system-reserved=cpu=500m,memory=512Mi` — carves the non-Kubernetes host out of allocatable: sshd, node-exporter, journald, the SSH sessions used to diagnose an incident.
- `kube-reserved=cpu=500m,memory=1Gi` — k3s server is one process holding apiserver, kine/etcd, scheduler and controller-manager, plus containerd and kubelet; 1Gi is that resident set with headroom, so the scheduler cannot pack pods into memory the control plane is already using.
- `eviction-hard=memory.available<1Gi,…` — kubelet must pick the victim (a runner pod) before the kernel OOM killer picks one by heuristic. Supplying `eviction-hard` **replaces** the kubelet defaults, so the `nodefs`/`imagefs`/inode thresholds are restated verbatim; dropping them would silently disable disk eviction.
- `eviction-soft=memory.available<1500Mi` with `eviction-soft-grace-period=memory.available=2m` — a 500Mi band above the hard floor where a transient CI spike (a `dotnet test` or `docker build` peak) has two minutes to resolve itself before costing a job. `eviction-max-pod-grace-period=30` bounds the graceful kill; the 2-minute grace is a soft-eviction property only, hard eviction is immediate.
- `eviction-pressure-transition-period=30s` — stops `MemoryPressure` from flapping and thereby stops the scheduler from oscillating between accepting and rejecting runners.
- `OOMScoreAdjust`: SSH `-1000` (OOM-immune — remote login is the only way back into the host, and `oom_score_adj` is inherited by forked children, so the login shell and job of an interactive session inherit `-1000` even though their cgroup is `user.slice`); k3s `-999` (one notch below, so in a truly unrecoverable state the kernel reaps k3s before it reaps the way in); node-exporter `-900` (telemetry must outlive runner pods but must never outrank the control plane).
- `CPUWeight=10000` on k3s and SSH against the default `100` — roughly a hundredfold share under contention, enough for the apiserver to answer and a shell to echo while 16 vCPU are pinned. node-exporter gets `1000`: a scrape is cheap. `IOWeight=1000` (the maximum) on all three because the measured failure mode was I/O starvation, not CPU.
- `system.slice` `MemoryMin=608M` / `MemoryLow=1216M` — on cgroup v2 a child's memory protection is clamped by every ancestor, so without a slice-level budget the per-service `MemoryMin` values are silently ineffective. 608M is exactly the sum of the protected children (512 + 64 + 32) and 1216M exactly the sum of their `MemoryLow` (1024 + 128 + 64); adding a protected service means raising both sums.
- node-exporter `ARGS` — binds the LAN address so Prometheus on `.212` scrapes it directly as job `node-ci-k3s-01` (a path that survives a K3s or apiserver outage); `--collector.disable-defaults` plus a narrow collector list keeps the exporter cheap exactly when the host is overloaded; `pressure` is the PSI collector that overload diagnosis depends on; `--collector.systemd.unit-include` restricts the systemd collector to `k3s|ssh|prometheus-node-exporter` instead of enumerating every unit each scrape. Deleting or drifting this file silently changes the metric set that the overload dashboards and analyses stand on.

Verify the repo matches the host (byte-identical, read-only):

```bash
diff <(ssh rysiu@192.168.0.66 'sudo cat /etc/rancher/k3s/config.yaml') github-runner-pool/host/k3s-overload-protection.yaml
diff <(ssh rysiu@192.168.0.66 'sudo cat /etc/systemd/system/k3s.service.d/99-overload-protection.conf') github-runner-pool/host/k3s-overload-protection.conf
diff <(ssh rysiu@192.168.0.66 'sudo cat /etc/systemd/system/ssh.service.d/99-overload-protection.conf') github-runner-pool/host/ssh-overload-protection.conf
diff <(ssh rysiu@192.168.0.66 'sudo cat /etc/systemd/system/prometheus-node-exporter.service.d/99-overload-protection.conf') github-runner-pool/host/node-exporter-overload-protection.conf
diff <(ssh rysiu@192.168.0.66 'sudo cat /etc/systemd/system/system.slice.d/99-overload-protection.conf') github-runner-pool/host/system-slice-overload-protection.conf
diff <(ssh rysiu@192.168.0.66 'sudo cat /etc/default/prometheus-node-exporter') github-runner-pool/host/prometheus-node-exporter.defaults
```

If a shell lacks process substitution, compare checksums instead: the repo files must hash to `98ad67a3a4ade55b858b343752ef9f91`, `cb49dff28fb877ae4df5c674b7bbb2c2`, `c1f9a67ebcdea5a41f58dc6307fdfc1e`, `58f2dceb0b5bdc381b69734de3c842f7`, `755a5c4ba912208f4acde8ad80a019d9`, `c7f4ca2660183a82dc38c31402d785b0` respectively (`md5sum github-runner-pool/host/*`). A mismatch on the last one is almost always the doubled backslash or a CRLF.

## Security and fork restrictions

These runners have access to the host Docker socket, which is equivalent to high privilege on the Docker host. Configure `linux-docker-ci` access for all current and future repositories in the `MRysiukiewicz` organization as required, but use it only for trusted private repositories and never for arbitrary public or untrusted workflows. Require approval for fork pull requests and do not expose these runners or secrets to unreviewed fork code. Avoid `pull_request_target` workflows that check out untrusted fork code with privileged credentials. Review workflow changes before merging and keep job permissions least-privileged.

## CodeQL decision

The audited `MRysiukiewicz/iTelemettry` CodeQL workflow remains on GitHub-hosted `ubuntu-latest` and must not consume this self-hosted pool. This runner pool is infrastructure-only; keep CodeQL workload placement separate from it.
