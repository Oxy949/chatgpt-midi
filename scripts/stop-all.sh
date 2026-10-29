#!/bin/sh
# Stop ONLY this Midi ChatGPT module, including abandoned Alpine children.
# BusyBox/POSIX sh; no Python, GNU stat, pkill or killall required.
# Does not touch firmware, bluealsa-aplay, Entware daemons, profile or VPN data.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
ROOT=${MIDI_ALPINE_ROOT:-/data/opt/midi-browser/alpine}
[ -d "$ROOT" ] || { echo "Alpine directory not found: $ROOT" >&2; exit 1; }
ROOT=$(CDPATH= cd -- "$ROOT" && pwd -P)
MODE=${1:---stop}
case "$MODE" in --stop|--dry-run) ;; *) echo "Usage: sh scripts/stop-all.sh [--dry-run|--stop]" >&2; exit 2 ;; esac
if [ "$MODE" = --stop ] && [ "$(id -u)" -ne 0 ]; then
  echo 'Run as root on the station to stop Alpine processes owned by uid 1000.' >&2
  exit 1
fi
PROC=/proc
# Test-only override: never allow a fake proc tree to send signals.
if [ "$MODE" = --dry-run ] && [ -n "${MIDI_STOP_TEST_PROC:-}" ]; then
  PROC=$MIDI_STOP_TEST_PROC
fi

# Identify our processes by BOTH process identity and its isolated root/cwd,
# rather than killing all chromium/python/ALSA processes by name.
match_pid() {
  pid=$1
  [ "$pid" != "$$" ] || return 1
  p="$PROC/$pid"
  [ -r "$p/comm" ] && [ -r "$p/cmdline" ] || return 1
  comm=$(cat "$p/comm" 2>/dev/null) || return 1
  cmd=$(tr '\000' ' ' < "$p/cmdline" 2>/dev/null) || return 1
  [ -n "$cmd" ] || return 1
  pr=$(readlink "$p/root" 2>/dev/null || :)
  cwd=$(readlink "$p/cwd" 2>/dev/null || :)
  if [ "$pr" = "$ROOT" ]; then
    case "$comm" in
      chrome|chromium|chrome_*|chrome-*|Xvfb|x11vnc|openbox)
        # Only this Alpine environment is used by the project; additional
        # X servers must still be on our known display.
        case "$comm" in
          Xvfb) case "$cmd" in *' :99 '*) return 0 ;; esac ;;
          x11vnc) case "$cmd" in *' -display :99 '*) return 0 ;; esac ;;
          *) return 0 ;;
        esac ;;
      pulseaudio)
        case "$cmd" in *midi-pulse.pa*) return 0 ;; esac ;;
      sox)
        case "$cmd" in *'--buffer 2048'*' remix '*|*'--buffer 2048'*' rate -l '*) return 0 ;; esac ;;
    esac
  fi
  if [ "$cwd" = "$BASE" ]; then
    # Controller, mic/speaker bridges, clients and Quasar listener.
    case "$cmd" in
      *' -m midivoice '*|*' -m midivoice.pulse_mic '*|*' -m midivoice.pulse_speaker '*|*' -m midivoice.alpine_exec '*) return 0 ;;
      *'/scripts/run-visible-voice.sh'*|*'scripts/run-visible-voice.sh'*|*'scripts/run-voice.sh'*|*'scripts/login-browser.sh'*|*'scripts/run-headless-voice.sh'*|*'scripts/login-alpine-browser.sh'*|*'scripts/login-alpine-browser-native.sh'*) return 0 ;;
      *' -D midi_capture '*|*' -D default -t raw -f S16_LE -c 2 -r 48000 '*) return 0 ;;
    esac
  fi
  # The native wrapper initially runs chroot outside the Alpine root.
  # Require its precise Alpine path and a known GUI component.
  case "$cmd" in
    *"chroot $ROOT /bin/busybox su"*'/usr/bin/Xvfb :99 '*|*"chroot $ROOT /bin/busybox su"*'/usr/bin/x11vnc -display :99 '*|*"chroot $ROOT /bin/busybox su"*'/usr/bin/chromium --user-data-dir=/home/chatgpt/profile'*) return 0 ;;
  esac
  return 1
}

scan() {
  for d in "$PROC"/[0-9]*; do
    [ -d "$d" ] || continue
    pid=${d##*/}
    if match_pid "$pid"; then printf '%s\n' "$pid"; fi
  done
}

report() {
  for pid in $1; do
    cmd=$(tr '\000' ' ' < "$PROC/$pid/cmdline" 2>/dev/null || :)
    printf '%s: %s\n' "$pid" "$(printf '%s' "$cmd" | cut -c 1-145)"
  done
}

echo "Project: $BASE"
echo "Alpine: $ROOT"
TARGETS=$(scan)
if [ -n "$TARGETS" ]; then
  echo 'Module processes:'
  report "$TARGETS"
else
  echo 'No running module processes detected.'
fi
if [ "$MODE" = --dry-run ]; then
  echo 'DRY RUN: no signals sent, no files removed.'
  exit 0
fi

# First ask everything we own to exit gracefully. Signal targets only after
# rechecking each PID; do not use broad process-name kill commands.
for pid in $TARGETS; do
  if match_pid "$pid"; then kill -TERM "$pid" 2>/dev/null || :; fi
done
sleep 2
REMAINING=$(scan)
if [ -n "$REMAINING" ]; then
  echo 'Still running after SIGTERM; stopping module processes:'
  report "$REMAINING"
  for pid in $REMAINING; do
    if match_pid "$pid"; then kill -KILL "$pid" 2>/dev/null || :; fi
  done
  sleep 1
fi
# Children which became orphans while their parent was exiting (SoX, aplay,
# crashpad) are re-scanned after the first pass, for at most one extra round.
REMAINING=$(scan)
if [ -n "$REMAINING" ]; then
  echo 'Final cleanup of late/orphaned module processes:'
  report "$REMAINING"
  for pid in $REMAINING; do
    if match_pid "$pid"; then kill -KILL "$pid" 2>/dev/null || :; fi
  done
  sleep 1
fi
REMAINING=$(scan)
if [ -n "$REMAINING" ]; then
  echo 'WARNING: module processes remain; do not remove their sockets.' >&2
  report "$REMAINING"
  exit 1
fi

# Remove ONLY stale local module sockets. Never remove Chromium profile locks.
CONTROL="${MIDI_STATE_DIR:-$HOME/.local/state/midi-chatgpt-voice}/control.sock"
if [ ! -L "$CONTROL" ] && [ -S "$CONTROL" ]; then
  # Check the kernel's UNIX socket table; a controller launched from another
  # working directory might be alive even if it did not match our scan.
  if [ -r /proc/net/unix ] && grep -Fq " $CONTROL" /proc/net/unix; then
    echo 'control.sock still has a live kernel listener; retaining it.'
  else
    rm -f "$CONTROL"
    echo 'Removed stale control.sock'
  fi
fi
# Refuse to unlink a live display even if its Xvfb belongs to another launcher.
display_live=0
for d in "$PROC"/[0-9]*; do
  [ -r "$d/comm" ] && [ -r "$d/cmdline" ] || continue
  c=$(cat "$d/comm" 2>/dev/null || :)
  [ "$c" = Xvfb ] || continue
  cmd=$(tr '\000' ' ' < "$d/cmdline" 2>/dev/null || :)
  case "$cmd" in *' :99 '*) display_live=1; break ;; esac
done
if [ "$display_live" = 0 ]; then
  XS="$ROOT/tmp/.X11-unix/X99"
  XL="$ROOT/tmp/.X99-lock"
  if [ ! -L "$XS" ] && [ -S "$XS" ]; then
    rm -f "$XS"
    echo 'Removed stale X99 display socket'
  fi
  if [ ! -L "$XL" ] && [ -f "$XL" ]; then
    rm -f "$XL"
    echo 'Removed stale X99 lock'
  fi
else
  echo 'Display :99 is still in use; retaining X11 files.'
fi
echo 'Midi ChatGPT processes stopped. Chromium profile, FASTVPN, Quasar and firmware audio untouched.'
