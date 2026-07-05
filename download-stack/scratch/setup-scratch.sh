#!/usr/bin/env bash
# setup-scratch.sh -- idempotent bootstrap of the qBittorrent NVMe "scratch" disk
# on the .212 docker host (Ubuntu VM). Safe to re-run: every step checks current
# state first and skips if already done. Assumes:
#   * the VHDX is already created + attached to the VM as a SCSI disk
#     (see ../create-vhdx.ps1, run on the .69 Hyper-V host BEFORE this script)
#   * this script is run FROM this directory (or with its full path) so the
#     sibling files (qbit-scratch-guard.py, the two systemd units) resolve
#     relative to it
#   * qBittorrent (download-stack) is already up and its WebUI is reachable at
#     http://localhost:8088 with no auth from localhost
#
# Usage: sudo ./setup-scratch.sh   (re-execs itself under sudo if not root)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOUNT_POINT="/mnt/scratch"
FS_LABEL="qbit-scratch"
QBIT_USER="rysiu"
QBIT_UID=1000
QBIT_GID=1000
GUARD_SRC="$SCRIPT_DIR/qbit-scratch-guard.py"
GUARD_DST="/home/${QBIT_USER}/qbit-scratch-guard.py"
LOG_DIR="/home/${QBIT_USER}/logs"
SERVICE_SRC="$SCRIPT_DIR/qbit-scratch-guard.service"
TIMER_SRC="$SCRIPT_DIR/qbit-scratch-guard.timer"
QBIT_API="http://localhost:8088"

if [[ $EUID -ne 0 ]]; then
  echo "==> Re-executing under sudo (need root for mkfs/fstab/systemd)..."
  exec sudo -E bash "$0" "$@"
fi

echo "==> [1/8] Locating the scratch disk..."
EXISTING_DEV="$(blkid -L "$FS_LABEL" 2>/dev/null || true)"
if [[ -n "$EXISTING_DEV" ]]; then
  echo "    Filesystem labeled '$FS_LABEL' already exists on $EXISTING_DEV -- skipping mkfs."
  TARGET_DEV="$EXISTING_DEV"
else
  echo "    No '$FS_LABEL' filesystem found yet; scanning for a ~450G unformatted disk..."
  CANDIDATE=""
  while read -r name type size fstype; do
    [[ "$type" == "disk" ]] || continue
    [[ "$name" == "sda" ]] && continue          # never touch the boot disk
    [[ -n "$fstype" ]] && continue              # already has a filesystem -> never touch
    if [[ "$size" =~ ^([0-9]+(\.[0-9]+)?)G$ ]]; then
      num="${BASH_REMATCH[1]}"
      if awk -v n="$num" 'BEGIN{exit !(n>=400 && n<=500)}'; then
        CANDIDATE="/dev/$name"
        break
      fi
    fi
  done < <(lsblk -dn -o NAME,TYPE,SIZE,FSTYPE)

  if [[ -z "$CANDIDATE" ]]; then
    echo "    ERROR: no unformatted ~450G disk found (excluding sda). Refusing to guess." >&2
    echo "    lsblk output for reference:" >&2
    lsblk -o NAME,TYPE,SIZE,FSTYPE >&2
    exit 1
  fi

  # Belt-and-suspenders: re-verify with blkid that the candidate truly has no
  # filesystem/partition-table before we ever touch it. NEVER format a disk
  # that blkid reports anything for.
  if blkid "$CANDIDATE" >/dev/null 2>&1; then
    echo "    ERROR: $CANDIDATE has a filesystem/signature per blkid -- refusing to format it." >&2
    blkid "$CANDIDATE" >&2
    exit 1
  fi

  echo "    Found unformatted candidate: $CANDIDATE -- formatting as ext4 (label=$FS_LABEL, -m 0)..."
  mkfs.ext4 -F -L "$FS_LABEL" -m 0 "$CANDIDATE"
  TARGET_DEV="$CANDIDATE"
fi

UUID="$(blkid -s UUID -o value "$TARGET_DEV")"
echo "    Target device: $TARGET_DEV (UUID=$UUID)"

echo "==> [2/8] Ensuring mount point $MOUNT_POINT exists..."
mkdir -p "$MOUNT_POINT"

echo "==> [3/8] Ensuring /etc/fstab entry..."
FSTAB_LINE="UUID=$UUID $MOUNT_POINT ext4 defaults,nofail 0 2"
if grep -q "UUID=$UUID" /etc/fstab; then
  echo "    fstab already has an entry for UUID=$UUID -- leaving it alone."
else
  echo "    Appending: $FSTAB_LINE"
  echo "$FSTAB_LINE" >> /etc/fstab
fi

echo "==> [4/8] Mounting $MOUNT_POINT..."
if mountpoint -q "$MOUNT_POINT"; then
  echo "    Already mounted."
else
  mount "$MOUNT_POINT"
fi

echo "==> [5/8] Preparing directory layout + ownership..."
mkdir -p "$MOUNT_POINT/incomplete"
chown -R "${QBIT_UID}:${QBIT_GID}" "$MOUNT_POINT"

echo "==> [6/8] Installing guard script + log dir..."
install -m 0755 -o "$QBIT_UID" -g "$QBIT_GID" "$GUARD_SRC" "$GUARD_DST"
mkdir -p "$LOG_DIR"
chown "${QBIT_UID}:${QBIT_GID}" "$LOG_DIR"

echo "==> [7/8] Installing + enabling systemd unit + timer..."
install -m 0644 "$SERVICE_SRC" /etc/systemd/system/qbit-scratch-guard.service
install -m 0644 "$TIMER_SRC" /etc/systemd/system/qbit-scratch-guard.timer
systemctl daemon-reload
systemctl enable --now qbit-scratch-guard.timer

echo "==> [8/8] Setting qBittorrent preferences via WebUI API ($QBIT_API)..."
curl -fsS -X POST "$QBIT_API/api/v2/app/setPreferences" \
  --data-urlencode 'json={"temp_path_enabled":true,"temp_path":"/scratch/incomplete","dl_limit":56250000,"up_limit":16875000,"queueing_enabled":true,"max_active_uploads":-1}' \
  -o /dev/null
echo "    qBit prefs set (temp_path=/scratch/incomplete, dl_limit=56250000, up_limit=16875000, queueing_enabled=true, max_active_uploads=-1)."

echo "==> Done. Status:"
df -h "$MOUNT_POINT"
systemctl is-active qbit-scratch-guard.timer
