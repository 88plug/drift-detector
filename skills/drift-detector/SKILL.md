---
name: drift-detector
description: Consult live drift state to check whether your recent replies have wandered from the user's active output contract (a terse persona, hard length/format rules, an in-character voice). Use when the user says you've changed tone, stopped following an instruction, or got verbose; when you're deep in a long session under a strict style; or before a reply when you're unsure you're still on-contract.
---

# Drift Detector

This plugin scores every assistant turn for how far it has drifted from the
user's active output contract and exposes that state to you.

## When to consult

- The user signals drift: "you're being verbose again", "stay in character",
  "you forgot the format", "what happened to caveman mode".
- You are many turns into a session that began with a strict style instruction
  and want to self-check before replying.
- The user runs `/drift:status` or `/drift:report` (those commands drive you).

## How to consult

Use the MCP tools (server name `drift-detector`):

- `drift_status` — session rollup: live score, verdict, drift rate, smoothed
  trend, active profile. Start here.
- `drift_recent` — the last N turns with scores and top offenders; use to see
  whether drift is rising or was a one-off.
- `drift_explain` — component breakdown of the latest turn (which behaviors —
  hedging, filler, hype, meta-narration, verbosity, length — drove the score).

The score is 0..100 where **higher is worse**; `verdict` is `drift` when it
crosses the profile threshold.

## How to act

If recent turns are drifting, correct course in your NEXT reply: drop preamble,
hedging, filler, and marketing phrasing; honor the explicit constraints; match
the persona. Do not announce that you consulted the detector or apologize at
length — just tighten up. If the user wants the contract changed, point them to
`/drift:profile`.

See `references/scoring-internals.md` for how the score is computed and
`references/profile-authoring.md` for writing a custom profile.
