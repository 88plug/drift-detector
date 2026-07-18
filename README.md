<div align="center">

# Drift Detector

**Claude Code drift detection plugin** — scores every assistant turn against your output contract and steers the LLM back when instruction-following erodes.

[![plugin-validate](https://github.com/88plug/drift-detector/actions/workflows/plugin-validate.yml/badge.svg)](https://github.com/88plug/drift-detector/actions/workflows/plugin-validate.yml)
[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-online-blue?style=flat)](https://88plug.github.io/drift-detector/)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A2BE2?style=flat)](https://github.com/88plug/claude-code-plugins)
[![DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/88plug/drift-detector)

</div>

Drift Detector is a Claude Code plugin and MCP server for drift detection and LLM guardrails. You set a contract — terse persona, hard length/format rules, in-character voice — and the deterministic, dependency-free engine scores every assistant turn for how far it wandered. A status-line badge shows live drift. The next prompt gets a one-shot correction nudge only when the previous reply broke contract. It never touches session reliability.

Built for AI agents, productivity workflows, and automation where instruction-following must hold across long Claude sessions — persona work, compressed-output modes, strict formatting, and in-voice scripts that cannot break character.

## Install

```text
/plugin marketplace add 88plug/claude-code-plugins
/plugin install drift-detector@88plug
```

Then enable the status-line badge (the one piece a plugin manifest can't auto-wire):

```bash
bash "$(/plugin path drift-detector)/install.sh"
```

`install.sh` is idempotent. It merges the badge into `~/.claude/settings.json`, composes with any existing statusline, and writes a timestamped backup.

## Quickstart

Set a contract, work a few turns, then check drift:

```text
> from now on answer in caveman style: terse, no preamble, no hedging
> /drift-detector:status
```

You see a live score and verdict, e.g. `caveman | 12% | ok`. When a reply relapses into "Certainly! I'd be delighted to walk you through this powerful, seamless solution…", the badge flips to `DRIFT 98%` and your next turn is quietly reminded to tighten up.

> [!NOTE]
> **F1=0.9973** on a 1,283-entry real corpus (fp=0, tp=375, fn=2, tn=906).
> 190-session synthetic eval: 100% accuracy, FP=0. Adversarial suite
> (37 cases targeting latent FP/FN patterns) included. A detector that
> cries wolf on clean work is worse than useless — the FP=0 constraint
> held across every tuning lap.

## Why / who

You set a strong output contract. Over a long session the model erodes it. Drift is easy to feel and hard to name. The detector turns "it stopped listening" into a number you can see, then acts on it for you — occasionally and proportionally, not every turn.

Use it when you need developer-tools-grade guardrails on Claude: AI coding with strict format rules, multi-agent pipelines, or any CLI workflow where persona and length must stick.

## Features

| Feature | What it does |
| --- | --- |
| Per-turn scoring | Deterministic 0–100 drift score on every Stop |
| Morin trajectory | Drift as a vector — velocity and trend, not just level; tolerates self-correcting blips, catches slow climbs |
| Repeating-spike detection | Flags oscillating relapse that looks adaptive turn-by-turn but is degenerative as a cycle |
| DCD pipeline | Deferred Correction Detection: scans N+1…N+10 for user correction follow-ups; improves recall on delayed feedback |
| Rule + DCD scoring | Stop-hook runtime: rule engine + drift-control detector (ExtraTree is eval-only, not the live hook) |
| Live badge | Status-line segment; composes with your existing statusline |
| One-shot nudge | Correction reminder only when drifted, on a cooldown — never nags |
| Profiles | `caveman`, `strict-instructions`, `persona`, plus your own |
| MCP tools | `drift_status`, `drift_recent`, `drift_explain`, `drift_list_profiles`, `drift_set_profile` |
| Commands | `/drift-detector:status`, `report`, `profile`, `reset`, `debug`, `calibrate` |

## Metrics

Tuned through 21 scientific-method rounds on a 1,283-entry real corpus from production sessions:

| Round | F1 | Notes |
|-------|----|-------|
| R0 | 0.21 | Baseline |
| R18 | 0.633 | 22-feature LR classifier |
| R19 | 0.9543 | ExtraTree 43-feature + DCD steps=8 |
| R20 | 0.977 | 11 new classify_user_reply patterns + DCD steps=10 |
| **R21** | **0.9973** | 17 patterns + exact-match gate + URL gate — ceiling reached |

Two irreducible FNs remain: one credential provision in ok context and one bare "Try now" indistinguishable without session context. Precision = 1.000 (fp=0).

## Commands

- `/drift-detector:status` — live score, verdict, drift rate, trend
- `/drift-detector:report` — per-turn history plus dominant offenders
- `/drift-detector:profile [name|list|show]` — switch or inspect the active profile
- `/drift-detector:reset [session|all]` — clear live state (or the rebuildable index)
- `/drift-detector:debug [on|off]` — toggle structured hook logging
- `/drift-detector:calibrate` — analyse history and update project anti-drift guidance in CLAUDE.md

## How it works

`SessionStart` initializes writable state. `Stop` tails the transcript, scores the last assistant turn (`src/lib/drift_score.py`), persists to a WAL-mode SQLite index, writes the badge, and drops a marker if the turn drifted. The next `UserPromptSubmit` consumes that marker and injects a short correction. A read-mostly MCP server (model context protocol) exposes state to the model.

The **point engine** answers "how bad is *this* turn?" with one number. The **trajectory layer** (`src/lib/drift_trajectory.py`) treats drift as a vector: velocity, plateau detection, repeating-spike cycles. An **eval-only ExtraTree classifier** (`scripts/backtest_real.py`) stacks on top for the real-corpus eval. The **DCD pipeline** scans the 10 turns after a candidate for user correction signals, recovering delayed feedback.

Scoring is lexical and structural: hedges, filler, hype, and meta-narration plus verbosity, length, and complexity, aggregated with a noisy-OR. Code blocks are stripped before scoring. Pure stdlib — same text and profile always produce the same score.

Full docs: [Algorithm](https://88plug.github.io/drift-detector/algorithm/), [Profiles](https://88plug.github.io/drift-detector/profiles/), [Eval](https://88plug.github.io/drift-detector/eval/).

## Profiles

A profile is the contract plus how to score deviation: `threshold`, `sensitivity`, per-class lexicon weights, and verbosity/length calibration. Ship your own as JSON and switch with `/drift-detector:profile`. See `skills/drift-detector/references/profile-authoring.md` for the schema and `skills/drift-detector/references/scoring-internals.md` for the scoring math.

## Development

```bash
make selftest    # engine self-test
make test        # pytest suite
make validate    # full plugin CI gate (66 checks)
bash scripts/run-python.sh scripts/adversarial_classify_test.py   # 37-case adversarial unit test
```

See `EXPERIMENTS.md` for the 21-round tuning ledger and `IMPLEMENTATION_NOTES.md` for design decisions.

## License

FSL-1.1-ALv2 — see [LICENSE](LICENSE).
