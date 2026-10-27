# homelab-docker

Docker stacks for the homelab. Most run on **`.212`** (`docker-host`, Ubuntu VM 101 on the Proxmox host `pve` `192.168.0.28`) and are managed by **Portainer via GitOps** (Stacks → Add stack → Repository, per folder).

## Layout

| Folder | Where | Contents |
|---|---|---|
| `arr-stack/` | `.212` (Portainer) | Sonarr, Radarr, Prowlarr, Bazarr, Autobrr, Cleanuparr |
| `download-stack/` | `.212` (Portainer) | qBittorrent, NZBGet, scratch guard |
| `jellyseerr-stack/`, `caddy-stack/`, `mediamtx-stack/`, `backrest-stack/`, `pte-points-stack/` | `.212` (Portainer) | requests, public HTTPS, RTSP, restic UI, PT points farmer |
| `monitoring-stack/` | `.212` (Portainer) | agents only: node-exporter, cAdvisor, Alloy |
| `monitoring-central/` | `mon-01` `.30` (plain `docker compose`, not Portainer) | Prometheus, Grafana, Loki, exporters, **Uptime Kuma** |

> Plex, FileFlows, Tautulli and Samba run natively in LXC 103 `homeserver` (`.69`) on the Proxmox host, with the RTX 2070 passed through. Media lives on the ZFS pool `tank` on `pve` and reaches `.212` over NFSv4 (`/tank/f/Plex` → `/mnt/cifs/media-f` → `/data` in containers).

## Storage — the `/data` single-mount design (TRaSH/servarr)

All media + downloads live on **one ext4 filesystem** bind-mounted as `/data` in every app, so **hardlinks + instant atomic moves** work and **no remote path mappings** are needed.

```
/data                (= /tank/f/Plex on pve, NFSv4 to .212 at /mnt/cifs/media-f)
├── Downloads/torrents/{movies,tv,prowlarr,points}/  # qBittorrent (incomplete on /scratch)
├── Movies/
└── TV Series/                                        # the library (Plex/FileFlows read the same dataset in LXC 103)
```

- Root folders: `/data/Movies`, `/data/TV Series`
- `PUID=1000 PGID=1000 UMASK=022 TZ=Europe/Warsaw` on every container.
- Apps talk to each other by **container name** on `arr_net` (`http://sonarr:8989`, `http://prowlarr:9696`, `http://nzbget:6789`, `http://qbittorrent:8080`, …).

## Prerequisites (one-time, on .212)

```bash
docker network create arr_net          # shared bridge for the media stack
# /mnt/cifs/media-f (NFS from pve) and /mnt/scratch must be mounted — see the KB migration entry
```

## Secrets

**Never committed.** App API keys live inside each `/config` volume (migrated from Windows). Any consumer secrets (exportarr keys, etc.) are supplied via **Portainer stack environment variables** or files outside this repo. `.env`, `secrets/`, `*.key` are gitignored.

## Conventions

- Pin images to dated tags once validated; never downgrade an arr (DB migrations are one-way).
- Named volumes for `/config` (precious SQLite); absolute bind `/mnt/cifs/media-f` for the media filesystem.
