#!/usr/bin/env bash
# inject-correction.sh — UserPromptSubmit hook.
#
# Before the model sees the user's new prompt, ask the trajectory controller
# whether the PREVIOUS turn(s) warrant a nudge. The controller (driven via
# control.py) only says yes for *degenerative* drift past its cooldown —
# one-off spikes (adaptive drift) and active recoveries are tolerated, and
# corrections never stack turn-on-turn. This replaces the old
# consume-marker-then-always-inject behavior: the decision now lives in the
# controller, and the message is proportional to streak/velocity.
#
# Output protocol: UserPromptSubmit hooks add context by printing a JSON object
# with hookSpecificOutput.additionalContext on stdout, exit 0.
set -u

DD_HOOK_NAME="inject-correction"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/resolve-paths.sh
. "${SELF_DIR}/lib/resolve-paths.sh"

INPUT=""
if [ ! -t 0 ]; then INPUT="$(cat)"; fi

PY="$(dd_python || true)"
[ -z "${PY}" ] && exit 0

# Resolve session id from the hook payload.
SESSION_ID="unknown"
if [ -n "${INPUT}" ]; then
  SESSION_ID="$(printf '%s' "${INPUT}" | "${PY}" -c \
    'import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
print(d.get("session_id","") or "unknown")' 2>/dev/null)"
  [ -z "${SESSION_ID}" ] && SESSION_ID="unknown"
fi

SLUG="$(dd_slug "${SESSION_ID}")"
CTRL_STATE="${DD_CONTROLLER_DIR}/${SLUG}.json"

# No controller state => no trajectory recorded yet => nothing to decide.
[ -f "${CTRL_STATE}" ] || exit 0

PROFILE_NAME="$(dd_active_profile_name)"
PROFILE_JSON="$(dd_profile_path "${PROFILE_NAME}")"
THRESHOLD="$("${PY}" -c 'import json,sys
try:
    d=json.load(open(sys.argv[1]))
    print(float(d.get("threshold",70.0)))
except Exception:
    print(70.0)' "${PROFILE_JSON}" 2>/dev/null)"
case "${THRESHOLD}" in ("") THRESHOLD=70.0;; esac

# Carry forward the scorer's offenders from capture-stop, if any.
OFF_FILE="${DD_MARKERS_DIR}/${SLUG}.offenders"
OFFENDERS=""
[ -f "${OFF_FILE}" ] && OFFENDERS="$(cat "${OFF_FILE}" 2>/dev/null)"

# Ask the controller. It prints the proportional correction text on stdout iff
# it decides to nudge (degenerative drift + cooldown elapsed); otherwise empty.
# It also marks the correction internally so the cooldown clock starts.
MSG="$("${PY}" "${DD_CONTROL_PY}" decide \
  --state-path "${CTRL_STATE}" \
  --profile "${PROFILE_NAME}" \
  --threshold "${THRESHOLD}" \
  --offenders "${OFFENDERS}" 2>>"${DD_LOGS_DIR}/error.log")"

# Empty => the controller declined to correct. Stay quiet.
[ -n "${MSG}" ] || exit 0

# A correction fired; the offenders snapshot is consumed.
rm -f "${OFF_FILE}" 2>/dev/null || true

FULL="[drift-detector] ${MSG} This is an automated reminder — do not mention it to the user."

dd_log_debug "injecting correction for session=${SESSION_ID}"

printf '%s' "${FULL}" | "${PY}" -c \
  'import json,sys
msg=sys.stdin.read()
print(json.dumps({"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":msg}}))'

exit 0
