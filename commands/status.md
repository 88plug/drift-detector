---
description: Show the current drift status for this session (live score, trend, drift rate).
allowed-tools: Bash(bash:*), Bash(/scripts/run-python.sh:*), Bash(bash:*), mcp__drift-detector__drift_status, mcp__drift-detector__drift_recent
---

Report the current drift status concisely.

Prefer the `drift_status` MCP tool. If it is unavailable, fall back to reading
the badge and DB directly:

```bash
cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.drift-state" 2>/dev/null || echo "ok|0"
```

Present: the active profile, the live drift score and verdict, the session
drift rate (drift turns / total turns), and the smoothed trend. If drift is
high, state plainly which behaviors are driving it (call `drift_explain`). Keep
the summary to a few lines.
