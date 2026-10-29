# Native graphical session: no dependency on host Python or audio.

start_display() {
  SCREEN=$1
  WITH_VNC=$2
  case "$SCREEN" in 640x480x24|800x600x24|1024x768x24) ;; *) echo 'Unsupported screen size' >&2; exit 2 ;; esac
  case "$WITH_VNC" in 0|1) ;; *) echo 'VNC option must be 0 or 1' >&2; exit 2 ;; esac
  [ -x "$ROOT/usr/bin/Xvfb" ] || { echo 'Alpine Xvfb missing' >&2; exit 1; }
  if [ "$WITH_VNC" = 1 ]; then
    [ -x "$ROOT/usr/bin/x11vnc" ] || { echo 'Alpine x11vnc missing' >&2; exit 1; }
  fi
  chroot "$ROOT" /bin/busybox --list | grep -qx su || { echo 'Alpine BusyBox su missing' >&2; exit 1; }
  [ ! -e "$ROOT/tmp/.X11-unix/X99" ] || { echo 'Display :99 in use; close the previous session' >&2; exit 1; }
  mkdir -p "$ROOT/tmp/.X11-unix"
  chmod 1777 "$ROOT/tmp/.X11-unix"
  # Only the validated screen size is interpolated into a shell command.
  NATIVE_ENV='exec /usr/bin/env -i HOME=/home/chatgpt USER=chatgpt LOGNAME=chatgpt PATH=/usr/sbin:/usr/bin:/sbin:/bin XDG_RUNTIME_DIR=/run/user/1000 XDG_CONFIG_HOME=/home/chatgpt/.config XDG_CACHE_HOME=/home/chatgpt/.cache DISPLAY=:99'
  session_start xvfb chroot "$ROOT" /bin/busybox su -s /bin/sh chatgpt -c \
    "$NATIVE_ENV /usr/bin/Xvfb :99 -screen 0 $SCREEN -nolisten tcp -noreset"
  DISPLAY_PID=$STARTED_PID
  sleep 2
  kill -0 "$DISPLAY_PID" 2>/dev/null || { echo 'Xvfb exited; see xvfb.log' >&2; exit 1; }
  # Xvfb alone has no window manager: Chromium then stays logically visible
  # when minimized and keeps running expensive voice animations.
  if [ -x "$ROOT/usr/bin/openbox" ]; then
    session_start wm chroot "$ROOT" /bin/busybox su -s /bin/sh chatgpt -c \
      "$NATIVE_ENV /usr/bin/openbox --sm-disable"
    sleep 1
    kill -0 "$STARTED_PID" 2>/dev/null || { echo 'Openbox exited; see wm.log' >&2; exit 1; }
  else
    echo 'Openbox missing: automatic voice-window minimization is unavailable.' >&2
  fi
  if [ "$WITH_VNC" = 1 ]; then
    session_start vnc chroot "$ROOT" /bin/busybox su -s /bin/sh chatgpt -c \
      "$NATIVE_ENV /usr/bin/x11vnc -display :99 -localhost -rfbport 5901 -forever -shared -nopw"
    VNC_PID=$STARTED_PID
    sleep 2
    kill -0 "$VNC_PID" 2>/dev/null || { echo 'x11vnc exited; see vnc.log' >&2; exit 1; }
    echo 'VNC: ssh -p 2222 -L 5901:127.0.0.1:5901 root@STATION_IP'
  fi
  export DISPLAY=:99
}

start_login_browser() {
  session_start chromium-visible chroot "$ROOT" /bin/busybox su -s /bin/sh chatgpt -c \
    "$NATIVE_ENV /usr/bin/chromium --user-data-dir=/home/chatgpt/profile --no-first-run --no-default-browser-check --disable-gpu --disable-features=Vulkan --disable-dev-shm-usage --renderer-process-limit=1 --disk-cache-size=33554432 https://chatgpt.com/"
}
