#!/bin/sh
# Start the two things a headed browser needs, then the worker.
#
# Both are optional in the sense that the worker starts without them and simply
# reports tiers 3 and 4 unavailable -- which is the whole point of the ladder
# degrading rather than failing. On a host with no /dev/uinput the scraper is
# still perfectly useful at tiers 1 and 2.
set -e

if [ -z "${SCRAPER_NO_XVFB:-}" ]; then
  Xvfb "${DISPLAY:-:99}" -screen 0 1440x900x24 -nolisten tcp &
  # Chromium fails to start against a display that is not up yet, and the
  # failure reads as a Chromium bug rather than a race.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -e "/tmp/.X11-unix/X${DISPLAY#:}" ] && break
    sleep 0.3
  done
fi

if [ -w /dev/uinput ]; then
  ydotoold --socket-path /tmp/.ydotool_socket &
  YDOTOOL_SOCKET=/tmp/.ydotool_socket
  export YDOTOOL_SOCKET
else
  echo "scraper: /dev/uinput not writable -- tier 4 will report itself unavailable" >&2
fi

exec python -m scraper.app
