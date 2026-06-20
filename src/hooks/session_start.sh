#!/usr/bin/env bash
# session_start.sh — task-named alias for the SessionStart hook.
#
# Working implementation: hooks/session-init.sh. Forwards stdin/exit status.
set -u
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMPL="${SELF_DIR}/../../hooks/session-init.sh"
exec bash "${IMPL}"
