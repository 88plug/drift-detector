# Drift Detector

Catch the moment Claude stops following your output contract — and pull it back.

[![plugin-validate](https://github.com/88plug/drift-detector/actions/workflows/plugin-validate.yml/badge.svg)](https://github.com/88plug/drift-detector/actions/workflows/plugin-validate.yml)
[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat)](https://github.com/88plug/drift-detector/blob/main/LICENSE.md)
[![Docs](https://img.shields.io/badge/docs-online-blue?style=flat)](https://88plug.github.io/drift-detector/)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A2BE2?style=flat)](https://github.com/88plug/claude-code-plugins)

Drift Detector scores every assistant turn for how far it has wandered from the
contract you set — a terse persona, hard length/format rules, an in-character
voice — and quietly steers it back. A status-line badge shows live drift. The
next prompt gets a one-shot correction nudge only when the previous reply broke
contract. Deterministic, dependency-free, never touches session reliability.

## Install

```text
/plugin marketplace add 88plug/drift-detector
/plugin install drift-detector@drift-detector
```

Then wire the status-line badge (the one piece a plugin manifest cannot auto-install):

```bash
bash "$(/plugin path drift-detector)/install.sh"
```

`install.sh` is idempotent. It merges the badge into `~/.claude/settings.json`,
composes with any existing statusline, and writes a timestamped backup.

!!! tip
    Hooks, commands, skills, and the MCP server wire automatically from the
    plugin manifest. Only the statusline needs the install script.

## Quickstart

Set a contract, work a few turns, then check drift:

```text
> from now on answer in caveman style: terse, no preamble, no hedging
> /drift:status
```

You should see something like `caveman | 12% | ok`. When a reply relapses into
hedging or verbosity, the badge flips to `DRIFT 98%` and the next prompt is
quietly reminded to tighten up.

!!! note
    **F1=0.9973** on 1,283 real-corpus entries (fp=0, tp=375, fn=2, tn=906).
    190-session synthetic eval: **100% accuracy, FP=0**. Adversarial suite
    included. The FP=0 constraint held across all 21 tuning rounds.

## Statusline badge

After `install.sh`, the Claude Code statusline shows a live drift segment:

| Badge | Meaning |
| --- | --- |
| `fit 12%` | On contract (green) |
| `drift 55%` | Warn band — near threshold (yellow) |
| `DRIFT 98%↑` | Above threshold; arrow is trajectory (red) |

Trajectory arrows: `↑` rising, `↓` recovering, `→` stable. Clean sessions
suppress the arrow. A dialogic segment (fit/eng) may append when present.

The badge is a single file read of `~/.claude/.drift-state` — no Python, no DB
on the hot path. Uninstall with:

```bash
bash "$(/plugin path drift-detector)/install.sh" --uninstall
```

## Profiles

A profile is the output contract plus how to score deviation: threshold,
sensitivity, per-class weights, and verbosity/length calibration.

| Profile | Threshold | Use case |
| --- | --- | --- |
| `caveman` | 50 | Terse persona, no preamble, no hedging |
| `strict-instructions` | 60 | Strict rule-following, low filler |
| `persona` | 65 | In-character voice work |
| `strict` | 55 | Maximum sensitivity |
| `relaxed` | 75 | Only extreme drift flagged |

```text
/drift:profile list
/drift:profile caveman
/drift:profile show
```

Ship your own as JSON under `$CLAUDE_PLUGIN_DATA/profiles/` and switch with
`/drift:profile <name>`. Full schema and authoring guide: [Profiles](profiles.md).

## MCP tools

The plugin registers a read-mostly MCP server (`drift-detector`). The model can
call these without slash commands:

| Tool | What it does |
| --- | --- |
| `drift_status` | Current rollup: score, verdict, turn count, drift rate, EWMA, active profile |
| `drift_recent` | Last N scored turns (ts, score, verdict, profile) |
| `drift_explain` | Score arbitrary text against a profile; returns offenders + component breakdown |
| `drift_list_profiles` | Installed profiles and which is active |
| `drift_set_profile` | Activate a profile by name (writes `active-profile` file only) |

All query tools open the SQLite index read-only. `drift_set_profile` never
touches the DB.

## Commands

| Command | What it does |
| --- | --- |
| `/drift:status` | Live score, verdict, drift rate, trend |
| `/drift:report` | Per-turn history and dominant offenders |
| `/drift:profile` | Switch or inspect (`name`, `list`, `show`) |
| `/drift:reset` | Clear live state (`session` or `all`) |
| `/drift:debug` | Toggle structured hook logging (`on` / `off`) |

## Features

| Feature | What it does |
| --- | --- |
| Per-turn scoring | Deterministic 0–100 drift score on every Stop |
| Live badge | Status-line segment; composes with your existing statusline |
| One-shot nudge | Correction only when drifted — never nags |
| Profiles | Bundled contracts plus your own JSON |
| Morin trajectory | Adaptive vs degenerative, velocity, chronic subclinical |
| Repeating-spike detection | Catches relapsing alternating patterns |
| DCD pipeline | Deferred Correction Detection: scans N+1…N+10 for delayed feedback |
| ExtraTree classifier | ML stage stacked on rules (GroupKFold/5, t=0.58) |
| MCP tools | Five tools for status, history, explain, profiles |
| Commands | status, report, profile, reset, debug |

## How it works

`SessionStart` initializes writable state. `Stop` scores the last assistant turn
(`src/lib/drift_score.py`), persists to a WAL-mode SQLite index, writes the badge,
and drops a marker if the turn drifted. `UserPromptSubmit` consumes that marker
and injects a correction only when drift is degenerative.

The scoring engine is pure stdlib: no network, no third-party deps. Same text and
profile always produce the same score. Full pipeline (point engine, trajectory,
ExtraTree + DCD): [Algorithm](algorithm.md).

## Metrics

Tuned through 21 scientific-method rounds on a 1,283-entry real corpus:

| Round | F1 | Notes |
|-------|----|-------|
| R0 | 0.21 | Baseline |
| R18 | 0.633 | 22-feature LR classifier |
| R19 | 0.9543 | ExtraTree 43-feature + DCD steps=8 |
| R20 | 0.977 | 11 new classify_user_reply patterns + DCD steps=10 |
| **R21** | **0.9973** | 17 patterns + exact-match gate + URL gate |

Two irreducible FNs remain (credential provision in ok context; bare "Try now").
Precision = 1.000 (fp=0). Full campaign: [Eval & Tuning](eval.md).

## Development

```bash
make selftest    # engine self-test
make test        # pytest suite
make validate    # full plugin CI gate
python3 scripts/adversarial_classify_test.py   # 37-case adversarial unit test
```

## License

[FSL-1.1-ALv2](https://github.com/88plug/drift-detector/blob/main/LICENSE.md) © 2026 88plug
