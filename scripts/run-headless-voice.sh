#!/bin/sh
# Compatibility entry point; shared session implementation handles cleanup.
set -eu
export MIDI_BROWSER_MODE=${MIDI_BROWSER_MODE:-headless}
exec sh "$(dirname -- "$0")/run-voice.sh"
