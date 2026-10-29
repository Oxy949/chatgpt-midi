#!/bin/sh
# Canonical supervised voice session. Existing visible/headless wrappers remain.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$BASE/scripts/lib/session.sh"
. "$BASE/scripts/lib/display.sh"
. "$BASE/scripts/lib/audio.sh"
session_init
MIDI_BROWSER_MODE=${MIDI_BROWSER_MODE:-visible}
export MIDI_BROWSER_MODE
case "$MIDI_BROWSER_MODE" in
  visible) start_display "${MIDI_VOICE_SCREEN:-800x600x24}" "${MIDI_VOICE_DEBUG_VNC:-0}" ;;
  headless) unset DISPLAY WAYLAND_DISPLAY ;;
  *) echo 'MIDI_BROWSER_MODE must be headless or visible' >&2; exit 2 ;;
esac
start_audio
echo 'In another SSH terminal: python3 -m midivoice open'
session_start controller python3 -m midivoice serve
CONTROLLER_PID=$STARTED_PID
if [ "${MIDI_QUASAR:-1}" != 0 ]; then
  session_start quasar python3 -m midivoice quasar-listen --dispatch --cancel-stock
fi
session_watch "$CONTROLLER_PID"
