#!/bin/sh
# Low-overhead, Python-free snapshot. Safe to run when Chromium is memory-starved.
set -eu
printf '%s\n' '=== Kernel memory (kB) ==='
grep -E '^(MemTotal|MemFree|MemAvailable|Cached|SReclaimable|SwapTotal|SwapFree|Shmem):' /proc/meminfo || :
printf '%s\n' '=== Memory pressure ==='
if [ -r /proc/pressure/memory ]; then cat /proc/pressure/memory; else echo 'PSI unsupported by kernel'; fi
printf '%s\n' '=== Load and runnable processes ==='
cat /proc/loadavg
printf '%s\n' '=== Related processes (BusyBox ps) ==='
ps | grep -E '[c]hromium|[c]hrome|[X]vfb|[x]11vnc|[s]ox|[p]ulseaudio|[a]record|[a]play|[m]idivoice|[m]aind' || :
printf '%s\n' '=== Active swap ==='
cat /proc/swaps
printf '%s\n' '=== Zram compression (if supported) ==='
for f in /sys/block/zram*/mm_stat; do
  if [ -r "$f" ]; then echo "$f"; cat "$f"; fi
done
