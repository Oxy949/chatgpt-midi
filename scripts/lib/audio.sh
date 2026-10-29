# One duplex audio lifecycle shared by manual login and voice modes.

check_audio() {
  [ -x "$ROOT/usr/bin/pulseaudio" ] && [ -x "$ROOT/usr/bin/sox" ] || {
    echo 'Audio dependencies missing; run install-midi-audio.sh' >&2; exit 1;
  }
  VOLUME=${MIDI_SPEAKER_VOLUME:-100%}
  case "$VOLUME" in 100%|[1-9]%|[1-9][0-9]%) ;; *) echo 'MIDI_SPEAKER_VOLUME must be 1%..100%' >&2; exit 2 ;; esac
  # Check before preparing FIFOs: a live server owns its paths.
  if python3 -m midivoice.alpine_exec pactl info >/dev/null 2>&1; then
    echo 'Another PulseAudio session is running; close that session first' >&2
    exit 1
  fi
}

start_audio() {
  check_audio
  python3 -m midivoice.pulse_mic setup
  python3 -m midivoice.pulse_mic prepare
  session_start pulse python3 -m midivoice.alpine_exec pulseaudio \
    --daemonize=no --exit-idle-time=-1 -n -F /home/chatgpt/midi-pulse.pa
  PULSE_PID=$STARTED_PID
  READY=0
  for attempt in 1 2 3 4 5 6 7 8; do
    kill -0 "$PULSE_PID" 2>/dev/null || break
    if python3 -m midivoice.alpine_exec pactl list short sources 2>/dev/null | grep -q midi_microphone && \
       python3 -m midivoice.alpine_exec pactl list short sinks 2>/dev/null | grep -q midi_speaker; then
      READY=1
      break
    fi
    sleep 1
  done
  [ "$READY" = 1 ] || { echo 'Virtual audio not ready; see pulse.log' >&2; exit 1; }
  python3 -m midivoice.pulse_mic secure
  python3 -m midivoice.alpine_exec pactl set-sink-volume midi_speaker "$VOLUME"
  session_start mic python3 -m midivoice.pulse_mic stream
  session_start speaker python3 -m midivoice.pulse_speaker stream
  echo 'Audio ready: midi_microphone -> Chromium -> midi_speaker'
}
