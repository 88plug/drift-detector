#!/usr/bin/env bash
# install.sh — idempotent statusLine merge for drift-detector.
#
# The plugin's hooks, commands, skills, and MCP server are wired automatically
# by Claude Code from the manifest once the plugin is enabled. The ONE thing the
# manifest can't carry is the statusLine (it's a user-settings concept), so this
# script merges our status-line badge into ~/.claude/settings.json.
#
# It is safe to run repeatedly:
#   * If a *different* statusLine already exists, it is preserved to
#     scripts/.prior-statusline and composed by statusline.sh (badge appended).
#   * If our statusLine is already installed, it's a no-op.
#   * settings.json is rewritten atomically with a timestamped backup.
#
# Usage:  ./install.sh            # install / update
#         ./install.sh --uninstall
set -u

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-${SELF_DIR}}"
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
SETTINGS="${CONFIG_DIR}/settings.json"
PRIOR_FILE="${PLUGIN_ROOT}/scripts/.prior-statusline"
STATUSLINE_CMD="bash \"\${CLAUDE_PLUGIN_ROOT}/scripts/statusline.sh\""

UNINSTALL=0
[ "${1:-}" = "--uninstall" ] && UNINSTALL=1

PY=""
for p in python3 python; do
  if command -v "$p" >/dev/null 2>&1; then PY="$p"; break; fi
done
if [ -z "${PY}" ]; then
  echo "install.sh: python3 is required to edit settings.json safely." >&2
  exit 1
fi

mkdir -p "${CONFIG_DIR}" "${PLUGIN_ROOT}/scripts" 2>/dev/null || true
[ -f "${SETTINGS}" ] || printf '{}\n' > "${SETTINGS}"

# Backup.
BACKUP="${SETTINGS}.drift-bak.$(date -u +%Y%m%d%H%M%S 2>/dev/null || echo bak)"
cp -f "${SETTINGS}" "${BACKUP}" 2>/dev/null || true

export DD_SETTINGS="${SETTINGS}"
export DD_PRIOR_FILE="${PRIOR_FILE}"
export DD_STATUSLINE_CMD="${STATUSLINE_CMD}"
export DD_UNINSTALL="${UNINSTALL}"

"${PY}" - <<'PYEOF'
import json, os, sys

settings_path = os.environ["DD_SETTINGS"]
prior_file = os.environ["DD_PRIOR_FILE"]
our_cmd = os.environ["DD_STATUSLINE_CMD"]
uninstall = os.environ["DD_UNINSTALL"] == "1"

try:
    with open(settings_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        data = {}
except (OSError, json.JSONDecodeError):
    data = {}

def is_ours(sl):
    return isinstance(sl, dict) and "statusline.sh" in str(sl.get("command", ""))

current = data.get("statusLine")

def read_prior():
    try:
        with open(prior_file, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""

def write_prior(cmd):
    try:
        os.makedirs(os.path.dirname(prior_file), exist_ok=True)
        with open(prior_file, "w", encoding="utf-8") as fh:
            fh.write(cmd or "")
    except OSError:
        pass

if uninstall:
    # Restore a prior statusline if we have one, else remove ours.
    prior = read_prior()
    if is_ours(current):
        if prior:
            data["statusLine"] = {"type": "command", "command": prior, "padding": 0}
        else:
            data.pop("statusLine", None)
    try:
        os.remove(prior_file)
    except OSError:
        pass
    action = "uninstalled"
else:
    if is_ours(current):
        action = "already-installed (no change)"
    else:
        # Preserve an existing third-party statusLine so statusline.sh composes it.
        if isinstance(current, dict) and current.get("command"):
            write_prior(current["command"])
        else:
            write_prior("")
        data["statusLine"] = {"type": "command", "command": our_cmd, "padding": 0}
        action = "installed"

tmp = settings_path + ".dd.tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
os.replace(tmp, settings_path)
print(action)
PYEOF
RESULT=$?

# Seed writable data tree + bundled profiles for immediate use.
if [ "${UNINSTALL}" = "0" ]; then
  DATA_ROOT="${CLAUDE_PLUGIN_DATA:-${CONFIG_DIR}/plugins/data/drift-detector-88plug}"
  mkdir -p "${DATA_ROOT}/profiles" "${DATA_ROOT}/markers" "${DATA_ROOT}/logs/archive" 2>/dev/null || true
  [ -f "${DATA_ROOT}/active-profile" ] || printf 'caveman' > "${DATA_ROOT}/active-profile"
  [ -f "${CONFIG_DIR}/.drift-state" ] || printf 'ok|0' > "${CONFIG_DIR}/.drift-state"
  chmod 0600 "${CONFIG_DIR}/.drift-state" 2>/dev/null || true
  echo "drift-detector: data root at ${DATA_ROOT}"
fi

echo "drift-detector: settings.json updated (backup: ${BACKUP})"
exit "${RESULT}"
