#!/usr/bin/env bash
# stop.sh — task-named alias for the Stop hook.
#
# The working implementation lives in hooks/capture-stop.sh (the path the plugin
# manifest references). This wrapper lets the documented src/hooks/stop.sh entry
# point be invoked directly with identical behavior. It forwards stdin, env, and
# exit status unchanged.
set -u
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMPL="${SELF_DIR}/../../hooks/capture-stop.sh"
exec bash "${IMPL}"
