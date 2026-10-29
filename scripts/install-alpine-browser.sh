#!/bin/sh
# Station Midi: isolated Alpine ARM64 browser runtime in /data, no system fw edits.
set -eu
export PATH="/data/opt/entware/bin:/data/opt/entware/sbin:/opt/bin:/opt/sbin:$PATH"
PROJECT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ROOT=${MIDI_ALPINE_ROOT:-/data/opt/midi-browser/alpine}
case "$ROOT" in /) echo 'Use a private Alpine directory' >&2; exit 2 ;; /*) ;; *) echo 'MIDI_ALPINE_ROOT must be absolute' >&2; exit 2 ;; esac
BASE=$(dirname -- "$ROOT")
VERS=3.23.7
FILE="alpine-minirootfs-$VERS-aarch64.tar.gz"
URL="https://dl-cdn.alpinelinux.org/alpine/v3.23/releases/aarch64/$FILE"

[ "$(uname -m)" = aarch64 ] || { echo 'Expected aarch64' >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo 'Root needed for isolated chroot installer' >&2; exit 1; }
for x in curl tar gzip sha256sum chroot mount; do command -v "$x" >/dev/null || { echo "Missing $x" >&2; exit 1; }; done
# Avoid filling the same partition used by Quasar and voice data.
AVAIL_KB=$(df -Pk /data | awk 'END {print $4}')
[ "$AVAIL_KB" -gt 700000 ] || { echo 'Need at least 700MB free on /data before installation' >&2; exit 1; }
mkdir -p "$BASE" "$ROOT"
if [ ! -x "$ROOT/sbin/apk" ]; then
    echo 'Downloading Alpine minirootfs and SHA256 from the same official release...'
    ( cd "$BASE"
      curl -fsSL "$URL" -o "$FILE.part"
      curl -fsSL "$URL.sha256" -o "$FILE.sha256"
      mv "$FILE.part" "$FILE"
      sha256sum -c "$FILE.sha256"
      # BusyBox tar on Station Midi lacks -z: decode gzip as a separate stage.
      gzip -dc "$FILE" | tar -xf - -C "$ROOT"
    )
fi
printf '%s\n' 'https://dl-cdn.alpinelinux.org/alpine/v3.23/main' 'https://dl-cdn.alpinelinux.org/alpine/v3.23/community' > "$ROOT/etc/apk/repositories"
# Read-only environment diagnostics and private runtime mountpoints only.
cp /etc/resolv.conf "$ROOT/etc/resolv.conf"
mkdir -p "$ROOT/proc" "$ROOT/dev/shm" "$ROOT/dev/pts" "$ROOT/home/chatgpt" "$ROOT/run/user/1000" "$ROOT/tmp"
chmod 1777 "$ROOT/tmp"
# chroot gets private device directory with only minimal non-network device nodes.
[ -e "$ROOT/dev/null" ] || mknod -m 666 "$ROOT/dev/null" c 1 3
[ -e "$ROOT/dev/zero" ] || mknod -m 666 "$ROOT/dev/zero" c 1 5
[ -e "$ROOT/dev/random" ] || mknod -m 666 "$ROOT/dev/random" c 1 8
[ -e "$ROOT/dev/urandom" ] || mknod -m 666 "$ROOT/dev/urandom" c 1 9
[ -e "$ROOT/dev/tty" ] || mknod -m 666 "$ROOT/dev/tty" c 5 0
[ -e "$ROOT/dev/fd" ] || ln -s /proc/self/fd "$ROOT/dev/fd"
MIDI_ALPINE_ROOT="$ROOT" sh "$PROJECT/scripts/prepare-alpine-runtime.sh"
chroot "$ROOT" /sbin/apk update
chroot "$ROOT" /sbin/apk add --no-cache chromium xvfb x11vnc openbox ca-certificates
chroot "$ROOT" /bin/sh -c 'id chatgpt >/dev/null 2>&1 || adduser -D -u 1000 -h /home/chatgpt -s /bin/sh chatgpt'
mkdir -p "$ROOT/home/chatgpt/profile" "$ROOT/home/chatgpt/.config" "$ROOT/home/chatgpt/.cache" "$ROOT/tmp/.X11-unix"
chmod 1777 "$ROOT/tmp/.X11-unix"
chown -R 1000:1000 "$ROOT/home/chatgpt" "$ROOT/run/user/1000"
chmod 700 "$ROOT/run/user/1000" "$ROOT/home/chatgpt/profile"
# Mount sysfs read-only on subsequent logins (see prepare-alpine-sysfs.sh).
echo "Alpine/Chromium installed under $ROOT."
echo 'Next: python3 -m midivoice browser-probe'
