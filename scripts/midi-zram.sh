#!/bin/sh
# Optional, conservative zram swap. Does not write to flash or change boot scripts.
# Never overwrite a zram device that may be owned by Quasar.
set -eu
usage() {
  echo 'Usage: sh scripts/midi-zram.sh probe|enable|status' >&2
  exit 2
}
ACTION=${1:-probe}
case "$ACTION" in probe|enable|status) ;; *) usage ;; esac
printf '%s\n' '=== Current swap ==='
cat /proc/swaps
if [ "$ACTION" = status ]; then
  if [ -r /sys/block/zram0/mm_stat ]; then
    echo '=== /sys/block/zram0/mm_stat (orig_bytes compr_bytes mem_used_total ...) ==='
    cat /sys/block/zram0/mm_stat
  fi
  exit 0
fi
if [ ! -d /sys/block/zram0 ]; then
  echo 'zram0 is not exposed by this kernel. No changes made.'
  exit 0
fi
if [ ! -b /dev/zram0 ]; then
  echo '/dev/zram0 block device missing. No changes made; not creating device nodes.'
  exit 0
fi
if [ ! -r /sys/block/zram0/disksize ]; then
  echo 'Cannot inspect zram0 size. No changes made.'
  exit 0
fi
SIZE=$(cat /sys/block/zram0/disksize)
printf 'zram0 configured size: %s bytes\n' "$SIZE"
if [ "$ACTION" = probe ]; then
  [ "$SIZE" = 0 ] && echo 'zram0 is available for opt-in 384MiB compressed swap.' || echo 'zram0 already configured: will not overwrite it.'
  exit 0
fi
[ "$(id -u)" -eq 0 ] || { echo 'Root required' >&2; exit 1; }
if grep -q '/dev/zram0' /proc/swaps; then
  echo 'zram0 already active as swap; leaving unchanged.'
  exit 0
fi
[ "$SIZE" = 0 ] || { echo 'Refusing to overwrite initialized zram0.' >&2; exit 1; }
command -v mkswap >/dev/null 2>&1 || { echo 'mkswap not installed.' >&2; exit 1; }
command -v swapon >/dev/null 2>&1 || { echo 'swapon not installed.' >&2; exit 1; }
# Conservative size, not a reservation of physical RAM. Memory is consumed
# only as pages are compressed. Swap configuration survives until reboot.
if [ -w /sys/block/zram0/comp_algorithm ] && grep -qw lz4 /sys/block/zram0/comp_algorithm; then
  echo lz4 > /sys/block/zram0/comp_algorithm || :
fi
echo 384M > /sys/block/zram0/disksize
if ! mkswap /dev/zram0; then
  echo 'mkswap failed; zram0 now initialized. No automatic reset attempted.' >&2
  exit 1
fi
if ! swapon /dev/zram0; then
  echo 'swapon failed. No Quasar or firmware files were modified.' >&2
  exit 1
fi
echo '384MiB zram swap activated. This is a temporary system-wide setting.'
cat /proc/swaps
