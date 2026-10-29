#!/bin/sh
# Read-only diagnostics for Chrome resource failures on Station Midi.
set -eu
ROOT=${MIDI_ALPINE_ROOT:-/data/opt/midi-browser/alpine}
echo '=== RAM and swap (kB) ==='
grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree|Shmem):' /proc/meminfo || :
echo '=== File descriptor limit of this shell ==='
ulimit -n || :
echo '=== System file handle usage: allocated unused max ==='
cat /proc/sys/fs/file-nr 2>/dev/null || :
echo '=== Chromium shared memory (inside Alpine) ==='
df -h "$ROOT/dev/shm" "$ROOT/tmp" 2>/dev/null || :
echo '=== Running browser process resources ==='
for p in /proc/[0-9]*; do
  [ -r "$p/comm" ] || continue
  IFS= read -r name < "$p/comm" || continue
  case "$name" in *chromium*|*chrome*|Xvfb|x11vnc) ;; *) continue ;; esac
  pid=${p#/proc/}
  echo "PID=$pid COMM=$name"
  grep -E '^(VmRSS|VmSize|Threads):' "$p/status" 2>/dev/null || :
  printf 'FD_COUNT='; ls "$p/fd" 2>/dev/null | wc -l
  grep 'Max open files' "$p/limits" 2>/dev/null || :
done
