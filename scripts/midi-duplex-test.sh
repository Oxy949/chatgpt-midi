#!/bin/sh
# Explicit test cue through Chromium's virtual PulseAudio output into ALSA.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$BASE"
eval "$(yse env)"
python3 -m midivoice.alpine_exec pactl list short sources
python3 -m midivoice.alpine_exec pactl list short sinks
python3 -m midivoice.pulse_speaker test
