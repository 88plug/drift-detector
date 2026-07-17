---
description: Analyse drift history and update this project's CLAUDE.md with data-driven anti-drift guidance
---

# Drift Calibration

Analyse the drift DB for the current project and regenerate the `CLAUDE.md`
anti-drift block based on what patterns actually fire most in real sessions.

## What to do

1. Locate the drift DB:
   ```
   DB="${CLAUDE_PLUGIN_DATA:-${HOME}/.claude/plugins/data/drift-detector-88plug}/drift.db"
   ```

2. Check if enough signal exists (at least 5 drifted turns):
   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/run-python.sh" -c "
   import sqlite3
   con = sqlite3.connect(f'file:${DB}?mode=ro', uri=True)
   n = con.execute('SELECT SUM(drift_turns) FROM sessions').fetchone()[0] or 0
   print(f'{int(n)} cumulative drift turns in DB')
   con.close()
   "
   ```

3. Run calibration against this project's CLAUDE.md:
   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/run-python.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/update_guidance.py" \
     --db "$DB" \
     --output "${CLAUDE_PROJECT_DIR}/CLAUDE.md" \
     --sessions 50
   ```

4. Show the updated section so the user can review it.

5. If `$CLAUDE_PROJECT_DIR` is unset or not a project, ask the user which
   file to write to, or use `--dry-run` to print the block without writing.

## Notes

- Calibration also runs automatically every 10 cumulative drift turns via the
  Stop hook — this command is for manual on-demand regeneration.
- The block is idempotent: it replaces itself between `<!-- drift-detector:start -->`
  and `<!-- drift-detector:end -->` markers on every run.
- Pass `--dry-run` to preview without writing.
