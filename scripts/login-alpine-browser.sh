#!/bin/sh
# Compatibility entry point; shared session implementation handles cleanup.
set -eu
exec sh "$(dirname -- "$0")/login-browser.sh"
