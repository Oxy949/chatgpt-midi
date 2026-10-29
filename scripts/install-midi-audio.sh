#!/bin/sh
# Adds PulseAudio virtual mic to existing ARM64 Alpine. Does not touch Quasar.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ROOT=${MIDI_ALPINE_ROOT:-/data/opt/midi-browser/alpine}
[ -x "$ROOT/sbin/apk" ] || { echo 'Install Alpine browser first' >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo 'Run as root on Station Midi' >&2; exit 1; }
chroot "$ROOT" /sbin/apk add --no-cache pulseaudio pulseaudio-utils sox
cd "$BASE"
python3 -m midivoice.pulse_mic setup
[ -x "$ROOT/usr/bin/sox" ] || { echo "SoX not installed" >&2; exit 1; }
[ -x "$ROOT/usr/bin/pulseaudio" ] || { echo 'PulseAudio not installed' >&2; exit 1; }
echo 'PulseAudio virtual microphone installed. Run scripts/login-alpine-browser.sh for a voice capture test.'
