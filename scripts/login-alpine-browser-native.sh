#!/bin/sh
# Compatibility entry point; shared session implementation handles cleanup.
set -eu
export MIDI_LOGIN_NO_AUDIO=1
exec sh "$(dirname -- "$0")/login-browser.sh"
