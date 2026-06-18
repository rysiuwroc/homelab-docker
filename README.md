# homelab-docker

Docker stacks for the homelab, deployed on **`.212`** (Ubuntu, Hyper-V VM on `.69`) and managed by **Portainer via GitOps** (Stacks → Add stack → Repository, per folder).

## Layout

| Folder | Stack | Contents |
|---|---|---|
| `arr-stack/` | `arr-stack` | Sonarr, Radarr, Prowlarr, Bazarr (LinuxServer.io) |
| `download-stack/` | `download-stack` | qBittorrent, NZBGet |
| `infra-stack/` | `infra-stack` | (later) caddy / jellyseerr / uptime-kuma+proxy / mediamtx / slack-meter |

> Plex + FileFlows + Tautulli stay **native on `.69`** (GPU/NVENC — a consumer GeForce can't be reliably passed into a Hyper-V Linux guest). A future Proxmox migration may revisit GPU-on-Linux.

## Storage — the `/data` single-mount design (TRaSH/servarr)

All media + downloads live on **one ext4 filesystem** bind-mounted as `/data` in every app, so **hardlinks + instant atomic moves** work and **no remote path mappings** are needed.

```
/mnt/data            (dedicated ext4 disk on .212)
├── torrents/{movies,tv,prowlarr,incomplete}/   # qBittorrent
├── usenet/{complete/{movies,tv},intermediate}/ # NZBGet
└── media/{movies,tv}/                           # the library (Plex/FileFlows read this over SMB)
```

- Root folders: `/data/media/movies`, `/data/media/tv`
- `PUID=1000 PGID=1000 UMASK=022 TZ=Europe/Warsaw` on every container.
- Apps talk to each other by **container name** on `arr_net` (`http://sonarr:8989`, `http://prowlarr:9696`, `http://nzbget:6789`, `http://qbittorrent:8080`, …).

## Prerequisites (one-time, on .212)

```bash
docker network create arr_net          # shared bridge for the media stack
# /mnt/data must exist (dedicated ext4 disk) — see infra docs
```

## Secrets

**Never committed.** App API keys live inside each `/config` volume (migrated from Windows). Any consumer secrets (exportarr keys, etc.) are supplied via **Portainer stack environment variables** or files outside this repo. `.env`, `secrets/`, `*.key` are gitignored.

## Conventions

- Pin images to dated tags once validated; never downgrade an arr (DB migrations are one-way).
- Named volumes for `/config` (precious SQLite); absolute bind `/mnt/data` for the media filesystem.
