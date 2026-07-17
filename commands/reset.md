---
description: Reset drift state — /drift-detector:reset [session | all].
argument-hint: "[session|all]"
allowed-tools: Bash(bash:*), Bash(/scripts/run-python.sh:*), Bash(rm:*), Bash(printf:*)
---

Reset drift state. Argument: `$ARGUMENTS` (default: `session`).

```bash
DATA="${CLAUDE_PLUGIN_DATA:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/data/drift-detector-88plug}"
BADGE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.drift-state"
```

- `session`: clear the badge to a neutral value (`printf 'ok|0' > "$BADGE"`),
  delete any pending correction markers in `$DATA/markers/`, and tell the user
  the live state was reset (historical rows in the DB are kept).
- `all`: do the above AND delete the rolled-up index by removing
  `$DATA/drift.db` plus its `-wal`/`-shm` sidecars. The DB is rebuildable from
  transcripts, so this is safe. Confirm before deleting if the user did not
  explicitly pass `all`.

Report exactly what was cleared.
