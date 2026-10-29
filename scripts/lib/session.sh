# Shared POSIX/BusyBox session lifecycle. Source after setting BASE.
# Nothing starts until a caller invokes session_init/start_*.

session_init() {
  ROOT=${MIDI_ALPINE_ROOT:-/data/opt/midi-browser/alpine}
  case "$ROOT" in /*) ;; *) echo 'MIDI_ALPINE_ROOT must be absolute' >&2; exit 2 ;; esac
  LOGDIR=$(dirname -- "$ROOT")
  PROFILE="$ROOT/home/chatgpt/profile"
  export MIDI_ALPINE_ROOT="$ROOT"
  export MIDI_CHROMIUM="$BASE/scripts/midi-chromium"
  export MIDI_BROWSER_PROFILE_DIR="$PROFILE"
  umask 077
  cd "$BASE"
  [ "$(id -u)" -eq 0 ] || { echo 'Root required for Alpine chroot' >&2; exit 1; }
  [ -x "$ROOT/usr/bin/chromium" ] || { echo 'Alpine Chromium missing; run install-alpine-browser.sh' >&2; exit 1; }
  [ -d "$PROFILE" ] || { echo "Saved profile missing: $PROFILE" >&2; exit 1; }
  PROFILE_OWNER=$(ls -ldn "$PROFILE" | awk '{print $3}')
  [ "$PROFILE_OWNER" = 1000 ] || { echo "Profile uid is $PROFILE_OWNER, expected 1000" >&2; exit 1; }
  mkdir -p "$LOGDIR"
  if ulimit -n 8192 2>/dev/null; then :; fi
  sh "$BASE/scripts/prepare-alpine-sysfs.sh"
  SESSION_PIDS=
  SESSION_MAIN_PID=
  trap session_cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

session_start() {
  session_log=$1
  shift
  "$@" > "$LOGDIR/$session_log.log" 2>&1 &
  STARTED_PID=$!
  SESSION_PIDS="$STARTED_PID $SESSION_PIDS"
}

session_stop_pid() {
  kill -TERM "$1" 2>/dev/null || :
}

session_cleanup() {
  # Give the controller/browser time to save its profile before stopping audio/X.
  if [ -n "$SESSION_MAIN_PID" ]; then
    session_stop_pid "$SESSION_MAIN_PID"
    for session_attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
      kill -0 "$SESSION_MAIN_PID" 2>/dev/null || break
      sleep 1
    done
  fi
  for session_pid in $SESSION_PIDS; do
    if [ "$session_pid" != "$SESSION_MAIN_PID" ]; then session_stop_pid "$session_pid"; fi
  done
  for session_attempt in 1 2 3; do
    session_alive=0
    for session_pid in $SESSION_PIDS; do
      if kill -0 "$session_pid" 2>/dev/null; then session_alive=1; fi
    done
    [ "$session_alive" = 1 ] || break
    sleep 1
  done
  for session_pid in $SESSION_PIDS; do
    if kill -0 "$session_pid" 2>/dev/null; then kill -KILL "$session_pid" 2>/dev/null || :; fi
    wait "$session_pid" 2>/dev/null || :
  done
}

session_watch() {
  SESSION_MAIN_PID=$1
  while :; do
    for session_pid in $SESSION_PIDS; do
      if ! kill -0 "$session_pid" 2>/dev/null; then
        session_rc=0
        wait "$session_pid" || session_rc=$?
        if [ "$session_pid" = "$SESSION_MAIN_PID" ]; then return "$session_rc"; fi
        echo "Session service $session_pid exited with code $session_rc; inspect logs in $LOGDIR" >&2
        return 1
      fi
    done
    sleep 1
  done
}
