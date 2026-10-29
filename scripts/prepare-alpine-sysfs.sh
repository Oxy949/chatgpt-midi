#!/bin/sh
# Compatibility entry point; preparing only sysfs left Chromium without /proc.
set -eu
exec sh "$(dirname -- "$0")/prepare-alpine-runtime.sh"
