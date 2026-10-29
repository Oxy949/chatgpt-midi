#!/bin/sh
# Re-create volatile virtual filesystems after a station reboot.
# All mountpoints stay inside the private Alpine root.
set -eu
ROOT=${MIDI_ALPINE_ROOT:-/data/opt/midi-browser/alpine}
[ -d "$ROOT" ] || { echo "Alpine root not found: $ROOT" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo 'Root required for Alpine runtime mounts' >&2; exit 1; }
case "$ROOT" in /*) ;; *) echo 'MIDI_ALPINE_ROOT must be absolute' >&2; exit 2 ;; esac
ROOT=$(CDPATH= cd -- "$ROOT" && pwd -P)
[ "$ROOT" != / ] && [ -x "$ROOT/sbin/apk" ] || { echo 'Expected a private Alpine rootfs' >&2; exit 1; }
mkdir -p "$ROOT/proc" "$ROOT/sys" "$ROOT/dev/shm"
mounted() { grep -F " $1 " /proc/mounts >/dev/null; }
if ! mounted "$ROOT/proc"; then
  mount -t proc -o nosuid,nodev,noexec proc "$ROOT/proc"
fi
if ! mounted "$ROOT/sys"; then
  mount -t sysfs -o ro,nosuid,nodev,noexec sysfs "$ROOT/sys"
fi
if ! mounted "$ROOT/dev/shm"; then
  mount -t tmpfs -o size=64m,mode=1777,nosuid,nodev tmpfs "$ROOT/dev/shm"
fi
[ -r "$ROOT/proc/self/status" ] || { echo 'Alpine /proc is unavailable' >&2; exit 1; }
echo 'Alpine runtime ready: proc, read-only sysfs, private /dev/shm'
