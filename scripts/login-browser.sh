#!/bin/sh
# Canonical manual login: native GUI with optional duplex audio.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$BASE/scripts/lib/session.sh"
. "$BASE/scripts/lib/display.sh"
. "$BASE/scripts/lib/audio.sh"
LOGIN_NO_AUDIO=${MIDI_LOGIN_NO_AUDIO:-0}
case "$LOGIN_NO_AUDIO" in 0|1) ;; *) echo 'MIDI_LOGIN_NO_AUDIO must be 0 or 1' >&2; exit 2 ;; esac
session_init
start_display "${MIDI_VNC_SCREEN:-800x600x24}" 1
if [ "$LOGIN_NO_AUDIO" = 0 ]; then
  start_audio
else
  echo 'Audio disabled for this VNC session; host Python is not used.'
fi
echo 'Close Chromium normally to save login and VPN settings.'
start_login_browser
session_watch "$STARTED_PID"
