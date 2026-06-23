<div align="center">

# Drift Detector

Catch the moment Claude stops following your output contract — and pull it back.

[![plugin-validate](https://github.com/88plug/drift-detector/actions/workflows/plugin-validate.yml/badge.svg)](https://github.com/88plug/drift-detector/actions/workflows/plugin-validate.yml)
[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat)](LICENSE.md)
[![Docs](https://img.shields.io/badge/docs-online-blue?style=flat)](https://88plug.github.io/drift-detector)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A2BE2?style=flat)](https://github.com/88plug/claude-code-plugins)
[![DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/88plug/drift-detector)

</div>

Drift Detector scores every assistant turn for how far it has wandered from the
contract you set — a terse persona, hard length/format rules, an in-character
voice — and quietly steers it back. A status-line badge shows live drift; the
next prompt gets a one-shot correction nudge only when the previous reply broke
contract. Deterministic, dependency-free, never touches your session's reliability.

## Install

```bash
/plugin marketplace add 88plug/drift-detector
/plugin install drift-detector@drift-detector
```

Then enable the status-line badge (the one piece a plugin manifest can't auto-wire):

```bash
bash "$(/plugin path drift-detector)/install.sh"
```

## Quickstart (under 60s)

Set a contract, work for a few turns, then check drift:

```text
> from now on answer in caveman style: terse, no preamble, no hedging
> /drift:status
```

You'll see a live score and verdict, e.g. `caveman | 12% | ok`. When a reply
relapses into "Certainly! I'd be delighted to walk you through this powerful,
seamless solution…", the badge flips to `DRIFT 98%` and your next turn is
quietly reminded to tighten up.

> [!NOTE]
> **F1=0.9973** on a 1,283-entry real-corpus (fp=0, tp=375, fn=2, tn=906).
> 190-session synthetic eval: 100% accuracy, FP=0. Adversarial test suite
> (37 cases targeting latent FP/FN patterns) included. A drift detector that
> cries wolf on clean work is worse than useless — the FP=0 constraint was
> held across every tuning lap without exception.

## Why / who

For anyone who sets a strong output contract and watches the model erode it over
a long session: persona work, strict formatting, compressed-output modes,
in-voice scripts that must not break character. Drift is easy to feel and hard
to name — the detector turns "it stopped listening" into a number you can see
and act on, and acts on it for you, occasionally and proportionally, instead of
nagging every turn.

## Features

| Feature | What it does |
| --- | --- |
| Per-turn scoring | Deterministic 0–100 drift score on every Stop |
| Morin trajectory | Scores drift as a vector — velocity and trend, not just level — so a self-correcting blip is tolerated and a slow climb is caught |
| Repeating-spike detection | Flags an oscillating relapse that looks adaptive turn-by-turn but is degenerative as a cycle |
| DCD pipeline | Deferred Correction Detection: scans N+1…N+10 for user correction follow-ups, improving recall on delayed feedback |
| ExtraTree classifier | ML stage (n=500, GroupKFold/5, t=0.58) stacked on the rule engine — catches patterns rules miss |
| Live badge | Status-line segment; composes with your existing statusline |
| One-shot nudge | Next prompt gets a correction reminder only when drifted, on a cooldown — never nags |
| Profiles | `caveman`, `strict-instructions`, `persona`, plus your own |
| MCP tools | `drift_status`, `drift_recent`, `drift_explain` (read-only) |
| Commands | `/drift:status`, `report`, `profile`, `reset`, `debug` |

## Metrics

Tuned through 21 scientific-method rounds on a 1,283-entry real-corpus extracted
from production sessions:

| Round | F1 | Notes |
|-------|----|-------|
| R0 | 0.21 | Baseline |
| R18 | 0.633 | 22-feature LR classifier |
| R19 | 0.9543 | ExtraTree 43-feature + DCD steps=8 |
| R20 | 0.977 | 11 new classify\_user\_reply patterns + DCD steps=10 |
| **R21** | **0.9973** | 17 patterns + exact-match gate + URL gate — ceiling reached |

Two irreducible FNs remain: one credential provision in ok context and one bare
"Try now" indistinguishable without session context. Precision = 1.000 (fp=0).

## Commands

- `/drift:status` — live score, verdict, drift rate, trend.
- `/drift:report` — per-turn history plus the dominant offenders.
- `/drift:profile [name|list|show]` — switch or inspect the active profile.
- `/drift:reset [session|all]` — clear live state (or the rebuildable index).
- `/drift:debug [on|off]` — toggle structured hook logging.

## How it works

`SessionStart` initializes writable state. `Stop` tails the transcript, scores
the last assistant turn (`src/lib/drift_score.py`), persists to a WAL-mode SQLite
index, writes the badge, and drops a marker if the turn drifted. The next
`UserPromptSubmit` consumes that marker and injects a short correction. A
read-only MCP server exposes the state to the model.

The **point engine** answers "how bad is *this* turn?" with a single number. The
**trajectory layer** (`src/lib/drift_trajectory.py`) treats drift as a vector:
velocity, plateau detection, repeating-spike cycle detection. The **ExtraTree
classifier** (`scripts/backtest_real.py`) stacks on top for the real-corpus eval.
The **DCD pipeline** (Deferred Correction Detection) scans the 10 turns following
a candidate for user correction signals, recovering cases where drift feedback is
delayed.

Scoring is lexical and structural: hedges, filler, hype, and meta-narration
combined with verbosity, length, and complexity, aggregated with a noisy-OR.
Code blocks are stripped before scoring. Everything is pure stdlib — same text
and profile always produce the same score.

## Profiles

A profile is the contract plus how to score deviation from it: a `threshold`, a
`sensitivity`, per-class lexicon weights, and verbosity/length calibration. Ship
your own as a JSON file and switch with `/drift:profile`. See
`skills/drift-detector/references/profile-authoring.md` for the full schema and
`skills/drift-detector/references/scoring-internals.md` for the scoring math.

## Development

```bash
make selftest    # engine self-test
make test        # pytest suite
make validate    # full plugin CI gate (66 checks)
python3 scripts/adversarial_classify_test.py   # 37-case adversarial unit test
```

See `EXPERIMENTS.md` for the 21-round tuning ledger and `IMPLEMENTATION_NOTES.md`
for the design decisions.

## License

FSL-1.1-ALv2 — see [LICENSE](LICENSE.md).
