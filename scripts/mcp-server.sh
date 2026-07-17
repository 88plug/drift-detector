#!/usr/bin/env bash
# mcp-server.sh — launcher for the drift-detector stdio MCP server.
#
# Claude Code starts MCP servers in a minimal environment where `python3` may
# not be on PATH and the plugin's engine module is not importable. This wrapper:
#   * resolves the plugin root from $CLAUDE_PLUGIN_ROOT (injected in plugin
#     context) and falls back to the repo root for direct/CLI runs;
#   * puts the engine dir (src/lib) on PYTHONPATH so `import drift_score` works;
#   * resolves the writable data root + DB path (shared logic with the hooks);
#   * finds a usable python interpreter and execs the server.
set -u

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "${SELF_DIR}/.." && pwd)}"
SERVER="${PLUGIN_ROOT}/mcp/server.py"

# Resolve the writable data root + DB path (defines DD_DATA_ROOT, DD_DB, ...).
# shellcheck source=/dev/null
if [ -f "${PLUGIN_ROOT}/hooks/lib/resolve-paths.sh" ]; then
  . "${PLUGIN_ROOT}/hooks/lib/resolve-paths.sh"
fi

# Make the scoring engine importable by mcp/server.py (drift_explain needs it).
export PYTHONPATH="${PLUGIN_ROOT}/src/lib${PYTHONPATH:+:${PYTHONPATH}}"

# Hand the resolved DB path to the server so it opens the right file read-only.
export DD_DB="${DD_DB:-${DD_DATA_ROOT:-${PLUGIN_ROOT}}/drift.db}"

# Shared fleet resolver (env override → venv → PATH → abs Homebrew/system).
exec bash "${PLUGIN_ROOT}/scripts/run-python.sh" "${SERVER}"
