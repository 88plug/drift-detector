---
description: Detailed drift report for this session — recent turns, offenders, and trend.
allowed-tools: mcp__drift-detector__drift_status, mcp__drift-detector__drift_recent, mcp__drift-detector__drift_explain, Bash(bash:*), Bash(/scripts/run-python.sh:*)
---

Produce a drift report for the current session.

Gather data with the MCP tools: `drift_status` for the rollup, `drift_recent`
(limit 10) for the per-turn history, and `drift_explain` for the latest turn's
component breakdown.

Then present:
1. A one-line headline (e.g. "Drifting: 3/8 turns over threshold, trend rising").
2. A compact per-turn table: turn timestamp, score, verdict, top offender.
3. The dominant drift components across the session and a one-sentence
   suggestion (tighten the profile, switch to `strict-instructions`, etc.).

If there is no data yet, say so and stop.
