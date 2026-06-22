#!/usr/bin/env bash
# capture-stop.sh — Stop hook. The core capture path.
#
# Fires when the assistant finishes a turn. Reads the Stop hook JSON on stdin
# (which carries session_id + transcript_path), scores the last assistant turn
# via score.py, persists it to the SQLite index, writes the badge the
# statusline reads, and — if the turn drifted — drops a per-session marker so
# the NEXT UserPromptSubmit can inject a gentle correction.
#
# Hard rule: this must NEVER break the session. Every step is best-effort and
# the hook always exits 0 with no stdout that Claude would treat as blocking.
set -u

DD_HOOK_NAME="capture-stop"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/resolve-paths.sh
. "${SELF_DIR}/lib/resolve-paths.sh"

dd_ensure_dirs

# --- Read Stop hook input -------------------------------------------------- #
INPUT=""
if [ ! -t 0 ]; then INPUT="$(cat)"; fi

PY="$(dd_python || true)"
if [ -z "${PY}" ]; then
  dd_log_error "no python interpreter; skipping"
  exit 0
fi

# Extract session_id + transcript_path from the hook payload (best-effort).
# The JSON is passed as argv[1] (not stdin) so this composes cleanly inside
# command substitution without a heredoc fighting for stdin.
read_field() {
  "${PY}" -c 'import json,sys
try: d=json.loads(sys.argv[1])
except Exception: d={}
print(d.get(sys.argv[2],"") or "")' "${INPUT}" "$1" 2>/dev/null
}

SESSION_ID="$(read_field session_id)"
TRANSCRIPT="$(read_field transcript_path)"
[ -z "${SESSION_ID}" ] && SESSION_ID="unknown"

if [ -z "${TRANSCRIPT}" ] || [ ! -f "${TRANSCRIPT}" ]; then
  dd_log_debug "no transcript path; nothing to score"
  exit 0
fi

PROFILE_NAME="$(dd_active_profile_name)"
PROFILE_JSON="$(dd_profile_path "${PROFILE_NAME}")"

# --- Score + persist (under the incremental lock) -------------------------- #
# score.py emits the legacy badge ("state|pct") on line 1 and, with
# --emit-detail, a compact JSON of {score,threshold,verdict,top_offenders} on
# line 2 — the inputs the trajectory controller needs.
BADGE="ok|0"
DETAIL=""
score_now() {
  "${PY}" "${DD_SCORE_PY}" \
    --transcript "${TRANSCRIPT}" \
    --session "${SESSION_ID}" \
    --db "${DD_DB}" \
    --schema "${DD_SCHEMA_SQL}" \
    --profile-json "${PROFILE_JSON}" \
    --emit-detail 2>>"${DD_LOGS_DIR}/error.log"
}

OUT="$(dd_with_lock "${DD_INCREMENTAL_LOCK}" score_now)"
if [ -n "${OUT}" ]; then
  BADGE="$(printf '%s' "${OUT}" | sed -n '1p' | tr -d '\n\r' | head -c 32)"
  DETAIL="$(printf '%s' "${OUT}" | sed -n '2p')"
fi

STATE="${BADGE%%|*}"
PCT="${BADGE##*|}"
case "${PCT}" in (*[!0-9]*|"") PCT=0;; esac

# --- Pull the raw score/threshold/offenders out of the detail line --------- #
SCORE="${PCT}"
THRESHOLD="70"
OFFENDERS=""
if [ -n "${DETAIL}" ] && [ -n "${PY}" ]; then
  read_detail() {
    "${PY}" -c 'import json,sys
try: d=json.loads(sys.argv[1])
except Exception: d={}
k=sys.argv[2]
v=d.get(k,"")
if k=="top_offenders" and isinstance(v,list): print("\n".join(str(x) for x in v))
else: print(v if v!="" else "")' "${DETAIL}" "$1" 2>/dev/null
  }
  s="$(read_detail score)"; [ -n "${s}" ] && SCORE="${s}"
  t="$(read_detail threshold)"; [ -n "${t}" ] && THRESHOLD="${t}"
  OFFENDERS="$(read_detail top_offenders)"
fi

# --- Drive the trajectory controller -------------------------------------- #
# control.py records the score into the per-session DriftController, returns a
# trajectory-aware badge ("state:vel|pct[|dialogic]", e.g. "drift:rising|72"),
# and persists state so inject-correction.sh can read the same trajectory next
# turn. If control.py yields nothing we keep the legacy "state|pct" badge from
# score.py above — the statusline shows no arrow for it (backward compatible).
SLUG="$(dd_slug "${SESSION_ID}")"
CTRL_STATE="${DD_CONTROLLER_DIR}/${SLUG}.json"
TURNS="$("${PY}" "${DD_SCORE_PY}" --status --db "${DD_DB}" --schema "${DD_SCHEMA_SQL}" \
  --session "${SESSION_ID}" 2>/dev/null \
  | "${PY}" -c 'import json,sys
try: print(int(json.load(sys.stdin).get("turns",0)))
except Exception: print(0)' 2>/dev/null)"
case "${TURNS}" in (*[!0-9]*|"") TURNS=0;; esac
# Turn index is the rollup count minus one (this turn already persisted above),
# so a re-fired Stop hook lands on the same index and the controller dedupes.
TURN_IDX=$(( TURNS > 0 ? TURNS - 1 : 0 ))

control_record() {
  "${PY}" "${DD_CONTROL_PY}" record \
    --state-path "${CTRL_STATE}" \
    --score "${SCORE}" \
    --pct "${PCT}" \
    --state "${STATE}" \
    --turn "${TURN_IDX}" \
    --threshold "${THRESHOLD}" \
    --transcript "${TRANSCRIPT}" 2>>"${DD_LOGS_DIR}/error.log"
}
CTRL_BADGE="$(dd_with_lock "${DD_INCREMENTAL_LOCK}" control_record)"
if [ -n "${CTRL_BADGE}" ]; then
  BADGE="$(printf '%s' "${CTRL_BADGE}" | tail -n1 | tr -d '\n\r' | head -c 64)"
fi

# --- Write the badge atomically ------------------------------------------- #
printf '%s' "${BADGE}" | dd_atomic_write "${DD_BADGE}" 0600
dd_log_debug "scored session=${SESSION_ID} badge=${BADGE} profile=${PROFILE_NAME}"

# --- Stash offenders for the next correction ------------------------------- #
# The controller (read by inject-correction.sh) decides whether to nudge; we
# only carry the scorer's offenders forward so the message can name them.
OFF_FILE="${DD_MARKERS_DIR}/${SLUG}.offenders"
if [ -n "${OFFENDERS}" ]; then
  printf '%s' "${OFFENDERS}" | dd_atomic_write "${OFF_FILE}" 0600
else
  rm -f "${OFF_FILE}" 2>/dev/null || true
fi

# --- Auto-calibrate CLAUDE.md every 10 cumulative drift turns -------------- #
# If we're in a project with a writable CLAUDE.md (or we can create one), and
# the project has accumulated at least 10 drift turns total, regenerate the
# anti-drift guidance block. This is best-effort and never breaks the session.
_maybe_calibrate() {
  [ -n "${PY}" ]               || return 0
  [ -n "${CLAUDE_PROJECT_DIR:-}" ] || return 0
  [ -d "${CLAUDE_PROJECT_DIR}" ]   || return 0

  local target="${CLAUDE_PROJECT_DIR}/CLAUDE.md"
  local update_py="${CLAUDE_PLUGIN_ROOT}/scripts/update_guidance.py"
  [ -f "${update_py}" ] || return 0

  # Count cumulative drift turns for this project directory (all sessions).
  local total_drift
  total_drift="$(${PY} -c "
import sqlite3, sys
try:
    con = sqlite3.connect('file:${DD_DB}?mode=ro', uri=True)
    row = con.execute('SELECT SUM(drift_turns) FROM sessions').fetchone()
    print(int(row[0] or 0))
    con.close()
except Exception:
    print(0)
" 2>/dev/null)"
  total_drift="${total_drift:-0}"
  case "${total_drift}" in (*[!0-9]*|"") total_drift=0;; esac

  # Fire on every 10th drift turn (0, 10, 20, …). Skip if < 10.
  [ "${total_drift}" -ge 10 ] || return 0
  local rem=$(( total_drift % 10 ))
  [ "${rem}" -eq 0 ] || return 0

  ${PY} "${update_py}" \
    --db "${DD_DB}" \
    --output "${target}" \
    --sessions 50 \
    --min-drift-turns 5 \
    >> "${DD_LOGS_DIR}/calibrate.log" 2>&1 || true
  dd_log_debug "calibration triggered at drift_turns=${total_drift} → ${target}"
}
_maybe_calibrate

exit 0
