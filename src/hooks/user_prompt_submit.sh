#!/usr/bin/env bash
# user_prompt_submit.sh — task-named alias for the UserPromptSubmit hook.
#
# Working implementation: hooks/inject-correction.sh. Forwards stdin/exit status.
set -u
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMPL="${SELF_DIR}/../../hooks/inject-correction.sh"
exec bash "${IMPL}"
