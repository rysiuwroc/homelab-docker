# GitHub Actions runner pool

This stack runs exactly five organization-scoped, universal GitHub Actions runners on the Ubuntu Docker host `192.168.0.212`:

| Container | GitHub runner | Persistent volumes |
| --- | --- | --- |
| `github-runner-01` | `ci-linux-01` | `work`, `config`, `tool-cache` |
| `github-runner-02` | `ci-linux-02` | `work`, `config`, `tool-cache` |
| `github-runner-03` | `ci-linux-03` | `work`, `config`, `tool-cache` |
| `github-runner-04` | `ci-linux-04` | `work`, `config`, `tool-cache` |
| `github-runner-05` | `ci-linux-05` | `work`, `config`, `tool-cache` |

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

## Portainer Git-stack deployment

1. Copy `.env.example` to a deployment-host `.env` file and set `RUNNER_SHA256` to the 64-hex SHA-256 published for the exact `RUNNER_VERSION` release. Before the first deployment and every runner-version update, build the pinned image on the Docker host:
   ```bash
   docker build --build-arg RUNNER_VERSION="$RUNNER_VERSION" --build-arg RUNNER_SHA256="$RUNNER_SHA256" -t "github-runner-pool:$RUNNER_VERSION" github-runner-pool/
   ```
   The Dockerfile refuses missing or malformed build arguments and verifies the downloaded archive before extraction. Portainer then deploys only the already-built, pinned image; this avoids its remote-agent BuildKit limitation.
2. Set `DOCKER_GID` to the host socket group ID (`stat -c '%g' /var/run/docker.sock`). Keep the host secret path in `GITHUB_RUNNER_API_TOKEN_FILE`.
3. In Portainer, choose **Stacks → Add stack → Git repository**, select this repository and the `github-runner-pool/docker-compose.yml` compose path, and name the stack `github-runner-pool`.
4. Add the non-secret values from `.env` to the Portainer stack environment. Do not add a PAT value. The compose file bind-mounts the host file read-only at `/run/secrets/github-runner-api-token`.
5. Deploy the stack and confirm all five services register and become healthy. The image contains only the pinned runner and general Linux CI tooling; Node, .NET, Java, Go, and Rust SDKs are deliberately absent.

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

A healthy container has a live runner process and `/run/runner-ready`. On `TERM`, the entrypoint stops the runner, requests a short-lived removal token internally, and deregisters it. The per-runner config volume keeps the registration/configuration alongside the runner installation across ordinary restarts; work and tool-cache volumes are separate for every runner.

The entrypoint creates/reuses a group for `DOCKER_GID`, adds `runner` to it, and verifies the daemon before registration. This check prints no credential:

```bash
docker exec --user runner github-runner-01 id -Gn
docker exec --user runner github-runner-01 docker version --format '{{.Server.Version}}'
```

## Image update and rollback

Record the version/checksum pair used by every deployment. To update, set both `RUNNER_VERSION` and its matching `RUNNER_SHA256` in the deployment-host `.env`, run the pinned `docker build` command above on the Docker host, then use Portainer **Redeploy**. Never change one without the other. The config volumes are retained, while the image distribution is copied into each config volume on startup.

To roll back, rebuild the previously recorded version/checksum pair, restore those values in the Portainer stack environment, and redeploy. If a runner release has already changed the on-volume runner files, stop the stack before the rollback and redeploy the same config volumes; do not delete work volumes. If GitHub refuses an old runner binary, use the previous known-good release pair and re-register during the maintenance window.

## Adding a sixth runner

Do not reuse an existing runner's volumes or name. In a reviewed change, copy one service block to `github-runner-06`, set `RUNNER_NAME: ci-linux-06`, and add new `github-runner-06-work`, `github-runner-06-config`, and `github-runner-06-tool-cache` named volumes. Add the corresponding organization runner to the `linux-docker-ci` group and verify the GitHub Actions concurrency demand justifies the extra host capacity before deploying. The production manifest intentionally remains at five services until this procedure is completed and reviewed.

## Safe cache and Docker cleanup

Never prune while a runner or a job it started is active. First pause workflow dispatches, wait for all organization jobs to finish, and confirm no pool or job containers are running. Then stop all five services and verify the stop before touching volumes:

```bash
docker compose --env-file .env -f docker-compose.yml stop
docker ps --filter name='github-runner-' --filter status=running
docker compose --env-file .env -f docker-compose.yml rm --force
```

The second command must print no running runner container, and the GitHub Actions organization page must show no active job. Only then may you remove a disposable tool cache volume (never `work` or `config`) and optionally prune old Docker build cache:

```bash
docker volume rm github-runner-01-tool-cache github-runner-02-tool-cache \
  github-runner-03-tool-cache github-runner-04-tool-cache github-runner-05-tool-cache
docker builder prune --filter until=24h
```

Recreate the cache volumes with `up -d`. Do not use `docker system prune`, remove work/config volumes, or run a global image/container prune on a live host; those operations can destroy evidence or affect unrelated workloads.

## Security and fork restrictions

These runners have access to the host Docker socket, which is equivalent to high privilege on the Docker host. Configure `linux-docker-ci` access for all current and future repositories in the `MRysiukiewicz` organization as required, but use it only for trusted private repositories and never for arbitrary public or untrusted workflows. Require approval for fork pull requests and do not expose these runners or secrets to unreviewed fork code. Avoid `pull_request_target` workflows that check out untrusted fork code with privileged credentials. Review workflow changes before merging and keep job permissions least-privileged.

## CodeQL decision

The audited `MRysiukiewicz/iTelemettry` CodeQL workflow remains on GitHub-hosted `ubuntu-latest` and must not consume this self-hosted pool. This runner pool is infrastructure-only; keep CodeQL workload placement separate from it.
