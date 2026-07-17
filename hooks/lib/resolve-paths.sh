#!/usr/bin/env bash
# resolve-paths.sh — shared path/state resolution for all drift-detector hooks.
#
# Sourced (not executed) by capture-stop.sh, inject-correction.sh,
# session-init.sh. Defines the canonical locations for writable state and the
# badge, plus small atomic-write / locking helpers. Pure bash + coreutils; no
# python, no jq required for the hot path.
#
# Contract:
#   * Writable state lives OUTSIDE the plugin cache dir (which is read-only and
#     gets blown away on update) under $CLAUDE_PLUGIN_DATA.
#   * The badge lives under $CLAUDE_CONFIG_DIR so the statusline can read it
#     with a single file read regardless of which plugin version is active.
#   * Everything degrades gracefully: if env vars are unset we fall back to
#     sensible defaults and a glob so a moved data dir is still found.

# --- Identity -------------------------------------------------------------- #
DD_PLUGIN_SLUG="drift-detector-88plug"

# CLAUDE_CONFIG_DIR: where Claude Code keeps user config. Default ~/.claude.
: "${CLAUDE_CONFIG_DIR:=${HOME}/.claude}"

# CLAUDE_PLUGIN_ROOT is injected by Claude Code when running a plugin hook.
# When sourced outside that context (tests/CLI) fall back to repo root.
if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ]; then
  _dd_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  CLAUDE_PLUGIN_ROOT="$(cd "${_dd_lib_dir}/../.." && pwd)"
fi

# --- Writable data root ---------------------------------------------------- #
# Prefer an explicit $CLAUDE_PLUGIN_DATA. Otherwise use the documented location;
# if that exact dir is missing, glob for any drift-detector* data dir so a
# renamed marketplace slug still resolves before we give up and create default.
dd_resolve_data_root() {
  if [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
    printf '%s\n' "${CLAUDE_PLUGIN_DATA}"
    return 0
  fi
  local base="${CLAUDE_CONFIG_DIR}/plugins/data"
  local exact="${base}/${DD_PLUGIN_SLUG}"
  if [ -d "${exact}" ]; then
    printf '%s\n' "${exact}"
    return 0
  fi
  # glob fallback: first matching existing dir
  local g
  for g in "${base}"/drift-detector*; do
    if [ -d "${g}" ]; then
      printf '%s\n' "${g}"
      return 0
    fi
  done
  printf '%s\n' "${exact}"  # default (may not exist yet; caller mkdir -p)
}

DD_DATA_ROOT="$(dd_resolve_data_root)"
DD_DB="${DD_DATA_ROOT}/drift.db"
DD_PROFILES_DIR="${DD_DATA_ROOT}/profiles"
DD_MARKERS_DIR="${DD_DATA_ROOT}/markers"
DD_CONTROLLER_DIR="${DD_DATA_ROOT}/controller"
DD_LOGS_DIR="${DD_DATA_ROOT}/logs"
DD_ACTIVE_PROFILE_FILE="${DD_DATA_ROOT}/active-profile"
DD_DEBUG_SENTINEL="${DD_DATA_ROOT}/.debug"
DD_INCREMENTAL_LOCK="${DD_DATA_ROOT}/.incremental.lock"
DD_JUDGE_LOCK="${DD_DATA_ROOT}/.judge.lock"

# The badge — read by statusline on every render. <=32 bytes, mode 0600.
DD_BADGE="${CLAUDE_CONFIG_DIR}/.drift-state"

# Engine + scripts (read-only, inside the plugin).
DD_SCORE_PY="${CLAUDE_PLUGIN_ROOT}/scripts/score.py"
DD_ENGINE_PY="${CLAUDE_PLUGIN_ROOT}/src/lib/drift_score.py"
DD_LIB_DIR="${CLAUDE_PLUGIN_ROOT}/src/lib"
DD_CONTROL_PY="${CLAUDE_PLUGIN_ROOT}/scripts/control.py"
DD_SCHEMA_SQL="${CLAUDE_PLUGIN_ROOT}/scripts/schema.sql"

# --- Python discovery ------------------------------------------------------ #
# Prefer scripts/run-python.sh (thin Claude PATH / Homebrew-safe). Returns an
# absolute interpreter path suitable for "${PY}" -c / script invocation.
dd_python() {
  local root runner py
  root="${CLAUDE_PLUGIN_ROOT:-}"
  if [ -z "$root" ]; then
    # resolve-paths.sh lives at hooks/lib/ — walk up to plugin root
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)"
  fi
  runner="${root}/scripts/run-python.sh"
  if [ -f "$runner" ]; then
    py="$(bash "$runner" -c 'import sys; print(sys.executable)' 2>/dev/null)" || return 1
    [ -n "$py" ] && { printf '%s' "$py"; return 0; }
  fi
  if command -v python3 >/dev/null 2>&1; then echo python3; return 0; fi
  if command -v python >/dev/null 2>&1; then echo python; return 0; fi
  return 1
}

# --- Session slug ---------------------------------------------------------- #
# Derive a filesystem-safe slug for a session id / transcript path so per-session
# marker files don't collide. Falls back to a hash of the input.
dd_slug() {
  local raw="${1:-default}"
  local s
  s="$(printf '%s' "${raw}" | tr -c 'A-Za-z0-9._-' '-' | sed 's/^-*//;s/-*$//')"
  s="${s:0:80}"
  [ -n "${s}" ] && printf '%s\n' "${s}" || printf 'default\n'
}

# --- Atomic write ---------------------------------------------------------- #
# dd_atomic_write <dest> <mode>   (content on stdin)
# Writes to a temp file in the same dir then renames — readers never see a
# partial file. Sets mode (default 0600). Best-effort; never aborts the hook.
dd_atomic_write() {
  local dest="$1" mode="${2:-0600}" dir tmp
  dir="$(dirname "${dest}")"
  mkdir -p "${dir}" 2>/dev/null || true
  tmp="$(mktemp "${dir}/.dd.XXXXXX" 2>/dev/null)" || { cat > "${dest}" 2>/dev/null; return; }
  cat > "${tmp}" 2>/dev/null || true
  chmod "${mode}" "${tmp}" 2>/dev/null || true
  mv -f "${tmp}" "${dest}" 2>/dev/null || { rm -f "${tmp}" 2>/dev/null; cat > "${dest}" 2>/dev/null; }
}

# --- flock wrapper --------------------------------------------------------- #
# dd_with_lock <lockfile> <cmd...> — run cmd under an exclusive lock if flock is
# available; otherwise run unlocked (single-writer hooks tolerate this).
dd_with_lock() {
  local lock="$1"; shift
  if command -v flock >/dev/null 2>&1; then
    ( flock -w 5 9 || exit 0; "$@" ) 9>"${lock}"
  else
    "$@"
  fi
}

# --- Debug logging --------------------------------------------------------- #
dd_debug_enabled() { [ -f "${DD_DEBUG_SENTINEL}" ]; }

dd_log_debug() {
  dd_debug_enabled || return 0
  mkdir -p "${DD_LOGS_DIR}" 2>/dev/null || true
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"
  local _py
  _py="$(dd_python 2>/dev/null || true)"
  printf '{"ts":"%s","hook":"%s","msg":%s}\n' \
    "${ts}" "${DD_HOOK_NAME:-?}" "$(printf '%s' "${1:-}" | ${_py:-false} -c 'import json,sys;print(json.dumps(sys.stdin.read()))' 2>/dev/null || printf '"%s"' "${1:-}")" \
    >> "${DD_LOGS_DIR}/debug.jsonl" 2>/dev/null || true
}

dd_log_error() {
  mkdir -p "${DD_LOGS_DIR}" 2>/dev/null || true
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"
  printf '%s [%s] %s\n' "${ts}" "${DD_HOOK_NAME:-?}" "${1:-}" \
    >> "${DD_LOGS_DIR}/error.log" 2>/dev/null || true
}

# --- Active profile -------------------------------------------------------- #
dd_active_profile_name() {
  if [ -f "${DD_ACTIVE_PROFILE_FILE}" ]; then
    head -c 64 "${DD_ACTIVE_PROFILE_FILE}" 2>/dev/null | tr -d '\n\r/' || echo caveman
  else
    echo caveman
  fi
}

# Resolve a profile name to its JSON path: user dir first, then bundled.
dd_profile_path() {
  local name="$1"
  if [ -f "${DD_PROFILES_DIR}/${name}.json" ]; then
    printf '%s\n' "${DD_PROFILES_DIR}/${name}.json"
  elif [ -f "${CLAUDE_PLUGIN_ROOT}/profiles/${name}.json" ]; then
    printf '%s\n' "${CLAUDE_PLUGIN_ROOT}/profiles/${name}.json"
  else
    printf '%s\n' "${CLAUDE_PLUGIN_ROOT}/profiles/caveman.json"
  fi
}

# Ensure the writable data tree exists. Idempotent.
dd_ensure_dirs() {
  mkdir -p "${DD_DATA_ROOT}" "${DD_PROFILES_DIR}" "${DD_MARKERS_DIR}" \
           "${DD_CONTROLLER_DIR}" "${DD_LOGS_DIR}/archive" 2>/dev/null || true
}
