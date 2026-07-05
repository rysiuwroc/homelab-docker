# qBittorrent scratch disk + guard

Local NVMe "scratch" disk for qBittorrent's incomplete downloads, plus a
systemd-timer guard that keeps it from ever filling up. Added 2026-07-05
after a CIFS-array incident caused by torrent random-write I/O; incomplete
downloads now land on this dedicated disk instead of the 17TB CIFS array,
which only ever sees finished, sequentially-written files.

## Topology

```
.69 (Hyper-V host)                .212 (Ubuntu docker host, VM)
  D:\qbit-scratch.vhdx    --SCSI-->  /dev/sdb (ext4, label qbit-scratch)
  (450GB dynamic)                       -> /mnt/scratch (host mount, fstab UUID, nofail)
                                            -> bind-mounted into the qbittorrent
                                               container as /scratch
                                               (download-stack/docker-compose.yml)
                                         qbit-scratch-guard.timer (every 60s)
                                            -> qbit-scratch-guard.py
                                               watches /mnt/scratch free space,
                                               drives qBit via its WebUI API
```

The `/mnt/scratch:/scratch` bind mount is declared in
[`download-stack/docker-compose.yml`](../docker-compose.yml) and is already
in git — it comes back automatically on any `download-stack` redeploy. What
is **not** automatic is the host-side disk (VHDX + partition + mount) and the
guard (systemd units + script) — that's what this directory captures, so a
full VM/host rebuild is a script run, not an afternoon of memory-reconstruction.

**The scratch disk's contents (in-flight incomplete downloads) are NOT
backed up.** It's treated as transient scratch space by design — if it's
lost, in-progress downloads just restart. Only the *setup* (this directory)
needs to survive a disaster, not the data on the disk.

## Disaster recovery — full rebuild from scratch

Run in order:

1. **On .69** (elevated PowerShell): recreate + attach the VHDX.
   ```powershell
   cd path\to\homelab-docker\download-stack\scratch
   .\create-vhdx.ps1
   ```
   Creates `D:\qbit-scratch.vhdx` (450GB dynamic) if missing, and attaches it
   to the `Ubuntu LTS - Docker Host` VM as a SCSI disk if not already attached.
   Idempotent — safe to re-run.

2. **On .212**: format/mount the disk and (re)install the guard.
   ```bash
   scp -r download-stack/scratch rysiu@192.168.0.212:~/scratch-setup
   ssh rysiu@192.168.0.212
   cd ~/scratch-setup
   sudo ./setup-scratch.sh
   ```
   Finds the new ~450G unformatted disk (never touches `sda`, the boot disk,
   and refuses to format anything `blkid` already recognizes), formats it
   ext4 (label `qbit-scratch`, `-m 0`, only if unformatted), adds the fstab
   entry, mounts it at `/mnt/scratch`, creates `incomplete/`, chowns to
   `1000:1000`, installs `qbit-scratch-guard.py` to `/home/rysiu/`, installs
   + enables the systemd service/timer, and pushes the required qBittorrent
   WebUI preferences (`temp_path=/scratch/incomplete`, rate limits, queueing).
   Idempotent — every step checks current state first.

3. **Redeploy `download-stack`** from git via Portainer (or `docker compose up
   -d` on .212) so the container picks up the `/mnt/scratch:/scratch` bind
   mount from `docker-compose.yml`. If qBittorrent was already running with
   the volume mounted, no redeploy is needed — the guard's API calls in step 2
   already take effect live.

## What the guard does (`qbit-scratch-guard.py`, runs every 60s via the timer)

All decisions are keyed off free space on `/mnt/scratch`. **It never deletes
torrents or data** — pressure is relieved only by throttling, redirecting new
downloads elsewhere, or pausing (never removing) the least-complete ones.

- **Tiers** (`max_active_downloads`, by free space): `>150GB` → 40 concurrent,
  `100–150GB` → 25, `60–100GB` → 15, `<60GB` → 10. Maximizes throughput when
  there's room, throttles concurrency as space tightens.
- **Overflow**: below 60GB free, new torrents' `temp_path` flips from
  `/scratch/incomplete` to the CIFS array (`/data/Downloads/incomplete`,
  17TB, rate-capped — no repeat of the original I/O-storm incident). Existing
  in-flight torrents are unaffected (a `temp_path` change only applies to new
  adds), so scratch simply drains. Flips back to scratch once free space
  recovers above 90GB (hysteresis avoids flapping).
- **Critical freeze**: below 35GB free, the least-complete active scratch
  downloaders are tagged `scratch-frozen` and paused (not deleted), keeping
  only the 3 closest-to-finishing torrents actively draining space. Frozen
  torrents auto-resume (tag removed) once free space recovers above 90GB.
- Any error (qBit API down, disk unreadable, transient) is logged and
  swallowed — the script always exits 0 so the systemd timer never
  accumulates failed-unit state; it just self-heals on the next 60s tick.

Log: `/home/rysiu/logs/scratch-guard.log` (rotated, 1MB x 3 backups).

## Files in this directory

| File | Purpose |
| --- | --- |
| `qbit-scratch-guard.py` | The guard script itself (exact copy from `.212`). |
| `qbit-scratch-guard.service` | systemd oneshot unit that runs the guard. |
| `qbit-scratch-guard.timer` | systemd timer, fires the service every 60s. |
| `setup-scratch.sh` | Idempotent .212 bootstrap: disk, mount, guard, qBit prefs. |
| `create-vhdx.ps1` | Idempotent .69 bootstrap: VHDX creation + VM attachment. |
