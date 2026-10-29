#!/bin/sh
# Read-only, no rootfs changes. Run via SSH on Station Midi.
set -u
printf '\n=== uname/arch ===\n'
uname -a
getconf LONG_BIT 2>/dev/null || true
printf '\n=== memory ===\n'
grep -E 'MemTotal|MemAvailable|SwapTotal|SwapFree' /proc/meminfo 2>/dev/null || true
printf '\n=== storage ===\n'
df -h /data 2>/dev/null || true
printf '\n=== devices ===\n'
cat /proc/asound/cards /proc/asound/pcm 2>/dev/null || true
printf '\n=== tools ===\n'
for tool in chromium chromium-browser python3 socat aplay arecord; do command -v "$tool" || true; done
printf '\n=== Quasar ===\n'
sha256sum /system/vendor/quasar/maind 2>/dev/null || true
printf '\n=== LEDs ===\n'
ls /sys/class/leds/ 2>/dev/null | head -50
