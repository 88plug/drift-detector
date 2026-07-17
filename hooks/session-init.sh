#!/usr/bin/env bash
# session-init.sh — SessionStart hook.
#
# Runs once at session start (startup | resume | compact). Idempotently:
#   * creates the writable data tree,
#   * applies the schema (CREATE IF NOT EXISTS — safe every time),
#   * seeds the active-profile file if missing,
#   * seeds a neutral badge so the statusline has something to read,
#   * prunes stale correction markers and old consumed temp files.
#
# Best-effort, always exits 0. Emits nothing to stdout (no context injection).
set -u

export DD_HOOK_NAME="session-init"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/resolve-paths.sh
. "${SELF_DIR}/lib/resolve-paths.sh"

dd_ensure_dirs

# --- Seed active profile --------------------------------------------------- #
if [ ! -f "${DD_ACTIVE_PROFILE_FILE}" ]; then
  printf 'caveman' | dd_atomic_write "${DD_ACTIVE_PROFILE_FILE}" 0600
fi

# --- Apply schema (idempotent) -------------------------------------------- #
PY="$(dd_python || true)"
if [ -n "${PY}" ] && [ -f "${DD_SCHEMA_SQL}" ]; then
  "${PY}" - "${DD_DB}" "${DD_SCHEMA_SQL}" <<'PYEOF' 2>>"${DD_LOGS_DIR}/error.log" || true
import sqlite3, sys, os
db, schema = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(os.path.abspath(db)), exist_ok=True)
con = sqlite3.connect(db, timeout=5.0)
con.execute("PRAGMA busy_timeout=5000")
with open(schema, "r", encoding="utf-8") as fh:
    con.executescript(fh.read())
con.commit(); con.close()
PYEOF
elif command -v sqlite3 >/dev/null 2>&1 && [ -f "${DD_SCHEMA_SQL}" ]; then
  sqlite3 "${DD_DB}" < "${DD_SCHEMA_SQL}" 2>>"${DD_LOGS_DIR}/error.log" || true
fi

# --- Seed neutral badge if missing ---------------------------------------- #
if [ ! -f "${DD_BADGE}" ]; then
  printf 'ok|0' | dd_atomic_write "${DD_BADGE}" 0600
fi

# --- Prune stale markers --------------------------------------------------- #
# Pending corrections older than 1 day are stale (the session moved on); consumed
# temp files are always junk. Use find when available.
if command -v find >/dev/null 2>&1; then
  find "${DD_MARKERS_DIR}" -maxdepth 1 -type f -name '*.pending-correction' -mtime +1 \
    -delete 2>/dev/null || true
  find "${DD_MARKERS_DIR}" -maxdepth 1 -type f -name '*.consumed.*' -mmin +60 \
    -delete 2>/dev/null || true
fi

dd_log_debug "session-init done data_root=${DD_DATA_ROOT}"
exit 0
