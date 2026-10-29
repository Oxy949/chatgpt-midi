#!/bin/sh
# Run while the login browser is OPEN, after installing PulseAudio.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ROOT=${MIDI_ALPINE_ROOT:-/data/opt/midi-browser/alpine}
LOGDIR=$(dirname -- "$ROOT")
export MIDI_ALPINE_ROOT="$ROOT"
cd "$BASE"
[ -x "$ROOT/usr/bin/pactl" ] || { echo 'First run sh scripts/install-midi-audio.sh' >&2; exit 1; }
echo '=== PulseAudio server ==='
python3 -m midivoice.alpine_exec pactl info
echo '=== Available microphones ==='
python3 -m midivoice.alpine_exec pactl list short sources
echo '=== PulseAudio sinks ==='
python3 -m midivoice.alpine_exec pactl list short sinks
echo '=== ALSA capture/playback processes ==='
ps | grep -E '[a]record|[a]play|[p]ulse_mic|[p]ulse_speaker|[p]ulseaudio' || :
echo '=== Most recent mic log ==='
tail -n 12 "$LOGDIR/mic.log" 2>/dev/null || :

echo '=== Most recent speaker log ==='
tail -n 12 "$LOGDIR/speaker.log" 2>/dev/null || :
echo '=== ALSA errors (if any) ==='
tail -n 10 "$LOGDIR/arecord.log" 2>/dev/null || :

echo '=== ALSA speaker errors (if any) ==='
tail -n 10 "$LOGDIR/aplay.log" 2>/dev/null || :
