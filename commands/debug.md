---
description: Toggle drift-detector debug logging — /drift-detector:debug [on | off].
argument-hint: "[on|off]"
allowed-tools: Bash(touch:*), Bash(rm:*), Bash(ls:*), Bash(tail:*)
---

Toggle debug logging. Argument: `$ARGUMENTS`.

```bash
DATA="${CLAUDE_PLUGIN_DATA:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/data/drift-detector-88plug}"
SENTINEL="$DATA/.debug"
```

- `on`: `touch "$SENTINEL"` — hooks will start appending structured events to
  `$DATA/logs/debug.jsonl`. Confirm and show the log path.
- `off`: `rm -f "$SENTINEL"`. Confirm.
- empty: report whether the sentinel exists and, if so, `tail -n 20` the debug
  log so the user can see recent hook activity.
