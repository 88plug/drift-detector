#!/usr/bin/env bash
# statusline.sh — hot-path drift badge renderer.
#
# Claude Code invokes the statusLine command on every render and pipes a JSON
# blob on stdin (session info, model, cwd, etc.). This script must be FAST: a
# single file read + printf, no python, no DB. It reads the precomputed badge
# at $CLAUDE_CONFIG_DIR/.drift-state ('state|pct') and prints a colored segment.
#
# If a prior statusLine existed before install, install.sh saved it to
# scripts/.prior-statusline; we run it first and prepend the drift badge so we
# compose rather than clobber the user's existing statusline.
set -u

CONFIG_DIR="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
BADGE_FILE="${CONFIG_DIR}/.drift-state"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIOR="${SELF_DIR}/.prior-statusline"

# Capture stdin once; both we and the prior statusline may want it.
STDIN_JSON=""
if [ ! -t 0 ]; then
  STDIN_JSON="$(cat)"
fi

# --- prior statusline (compose) ------------------------------------------- #
prior_out=""
if [ -s "${PRIOR}" ]; then
  prior_cmd="$(cat "${PRIOR}")"
  if [ -n "${prior_cmd}" ]; then
    prior_out="$(printf '%s' "${STDIN_JSON}" | bash -c "${prior_cmd}" 2>/dev/null)" || prior_out=""
  fi
fi

# --- drift badge ---------------------------------------------------------- #
# Badge formats, newest first (all backward-compatible):
#   new:    "state:vel|pct[|dialogic]"   e.g. "drift:rising|72|DRIFT:72 eng:40"
#   legacy: "state|pct"                  e.g. "drift|72"
# state = ok|warn|drift; vel describes the trajectory direction and is
# OPTIONAL (absent => no arrow). The vel field is intentionally permissive: it
# accepts both the controller's vocabulary (rising/falling/flat) and the
# trajectory engine's (rising/recovering/stable). The dialogic segment (if
# present) is appended verbatim — it already carries fit:/eng: numbers.
state="ok"; vel=""; pct="0"; dialogic=""
if [ -r "${BADGE_FILE}" ]; then
  raw="$(head -c 64 "${BADGE_FILE}" 2>/dev/null | tr -d '\n\r')"
  # Split on '|': field 1 = state[:vel], field 2 = pct, field 3 = dialogic.
  f1="${raw%%|*}"; rest="${raw#*|}"
  pct="${rest%%|*}"; dtail="${rest#*|}"
  [ "${dtail}" != "${rest}" ] && dialogic="${dtail}"
  # state:vel split (legacy has no ':', so vel stays empty => no arrow).
  state="${f1%%:*}"
  if [ "${f1}" != "${state}" ]; then vel="${f1#*:}"; fi
  case "${pct}" in (*[!0-9]*|"") pct="0";; esac
  case "${state}" in (ok|warn|drift) ;; (*) state="ok";; esac
fi

# Trajectory arrow from the velocity field:
#   rising            -> ↑  (drifting worse)
#   falling/recovering-> ↓  (pulling back toward contract)
#   stable/flat       -> →  (holding; explicit, not "no data")
#   absent/unknown    -> "" (legacy badge or no trajectory yet: no arrow)
# Clean sessions (state=ok) suppress the arrow entirely — "fit 12%", no glyph —
# since a healthy line needs no motion cue.
arrow=""
case "${vel}" in
  rising)              arrow="↑";;
  falling|recovering)  arrow="↓";;
  stable|flat)         arrow="→";;
esac
[ "${state}" = "ok" ] && arrow=""

# Colors + label (Claude renders ANSI fine). Clean reads as "fit", drift as a
# shouty uppercase "DRIFT", the warn band as a quiet lowercase "drift".
ESC=$'\033'
case "${state}" in
  drift) color="${ESC}[1;31m"; label="DRIFT";;
  warn)  color="${ESC}[1;33m"; label="drift";;
  *)     color="${ESC}[2;32m"; label="fit";;
esac
reset="${ESC}[0m"

badge="${color}${label} ${pct}%${arrow}${reset}"
[ -n "${dialogic}" ] && badge="${badge} ${dialogic}"

if [ -n "${prior_out}" ]; then
  printf '%s  %s\n' "${prior_out}" "${badge}"
else
  printf '%s\n' "${badge}"
fi
