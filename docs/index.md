<div align="center">

# Drift Detector

A Claude Code plugin that scores every assistant turn for behavioral drift from
your active output contract and steers it back automatically.

[![plugin-validate](https://github.com/88plug/drift-detector/actions/workflows/plugin-validate.yml/badge.svg)](https://github.com/88plug/drift-detector/actions/workflows/plugin-validate.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat)](https://github.com/88plug/drift-detector/blob/master/LICENSE)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A2BE2?style=flat)](https://github.com/88plug/claude-code-plugins)
[![DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/88plug/drift-detector)

</div>

## Install

```text
/plugin marketplace add 88plug/drift-detector
/plugin install drift-detector@drift-detector
```

Then wire the status-line badge:

```bash
bash "$(/plugin path drift-detector)/install.sh"
```

## Quickstart

```text
> from now on answer in caveman style: terse, no preamble, no hedging
> /drift:status
```

You'll see `caveman | 12% | ok`. When a reply relapses into hedging or verbosity,
the badge flips to `DRIFT 98%` and your next prompt is quietly reminded to tighten up.

> [!NOTE]
> **F1=0.9973** on 1,283 real-corpus entries (fp=0, tp=375, fn=2, tn=906).
> 190-session synthetic eval: **100% accuracy, FP=0**. Adversarial test suite included.
> The FP=0 constraint was held across all 21 tuning rounds without exception.

## Why / who

For anyone who sets a strong output contract and watches the model erode it over
a long session: persona work, strict formatting, compressed-output modes,
red-team scripts that must stay in voice. Turns "it stopped listening" into a
number you can see and act on.

## Features

| Feature | What it does |
| --- | --- |
| Per-turn scoring | Deterministic 0–100 drift score on every Stop |
| Live badge | Status-line segment, composes with your existing statusline |
| One-shot nudge | Next prompt gets a correction reminder only when drifted — never nags |
| Profiles | `caveman`, `strict-instructions`, `persona`, plus your own |
| Morin trajectory | Adaptive vs degenerative classification, velocity, chronic subclinical |
| Repeating-spike detection | Catches relapsing alternating-drift patterns, not just sustained runs |
| DCD pipeline | Deferred Correction Detection: scans N+1…N+10 for delayed user feedback |
| ExtraTree classifier | ML stage stacked on rules, GroupKFold/5, t=0.58 |
| MCP tools | `drift_status`, `drift_recent`, `drift_explain` (read-only) |
| Commands | `/drift:status`, `report`, `profile`, `reset`, `debug` |

## Commands

- `/drift:status` — live score, verdict, drift rate, trend
- `/drift:report` — per-turn history and dominant offenders
- `/drift:profile [name|list|show]` — switch or inspect the active profile
- `/drift:reset [session|all]` — clear live state
- `/drift:debug [on|off]` — toggle structured hook logging

## How it works

`SessionStart` initializes writable state. `Stop` scores the last assistant turn
(`src/lib/drift_score.py`), persists to a WAL-mode SQLite index, writes the badge,
and drops a marker if the turn drifted. `UserPromptSubmit` consumes that marker
and injects a correction only when drift is degenerative.

The scoring engine is deterministic and dependency-free: stdlib only, no network
calls. See [Algorithm](algorithm.md) for the full pipeline and [Eval](eval.md)
for the 21-round scientific-method campaign.

## License

[MIT](https://github.com/88plug/drift-detector/blob/master/LICENSE) © 2026 88plug
