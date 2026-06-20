# Drift Detector

Catch the moment Claude stops following your output contract — and pull it back.

[![CI](https://img.shields.io/badge/CI-validate__plugin-blue)](.ci/validate_plugin.py)
[![version](https://img.shields.io/badge/version-1.3.0-informational)](plugin.json)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Drift Detector scores every assistant turn for how far it has wandered from the
contract you set — a terse persona, hard length/format rules, an in-character
voice — and quietly steers it back. A status-line badge shows live drift; the
next prompt gets a one-shot correction nudge only when the previous reply broke
contract. Deterministic, dependency-free, and it never touches your session's
reliability.

## Install

```bash
/plugin marketplace add 88plug/drift-detector
/plugin install drift-detector@drift-detector
```

Then enable the status-line badge (the one piece a plugin manifest can't
auto-wire):

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
> Validated on a 170-session labeled corpus: 100% accuracy, 0.0%
> false-positive rate. A drift detector that cries wolf on clean terse work is
> worse than useless, so a zero false-positive rate is the engine's hard
> invariant — tuned across ten scientific-method laps without ever breaking it.

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
| Repeating-spike detection | Flags an oscillating relapse (a session that keeps bouncing back over the line) that looks adaptive turn-by-turn but is degenerative as a cycle |
| Live badge | Status-line segment; composes with your existing statusline |
| One-shot nudge | Next prompt gets a correction reminder only when drifted, on a cooldown |
| Profiles | `caveman`, `strict-instructions`, `persona`, plus your own |
| MCP tools | `drift_status`, `drift_recent`, `drift_explain` (read-only) |
| Commands | `/drift:status`, `report`, `profile`, `reset`, `debug` |

## Commands

- `/drift:status` — live score, verdict, drift rate, trend.
- `/drift:report` — per-turn history plus the dominant offenders.
- `/drift:profile [name|list|show]` — switch or inspect the active profile.
- `/drift:reset [session|all]` — clear live state (or the rebuildable index).
- `/drift:debug [on|off]` — toggle structured hook logging.

## How it works

`SessionStart` initializes writable state. `Stop` tails the transcript, scores
the last assistant turn (`scripts/score.py` → `src/lib/drift_score.py`),
persists to a WAL-mode SQLite index, writes the badge, and drops a marker if the
turn drifted. The next `UserPromptSubmit` consumes that marker and injects a
short correction. A read-only MCP server exposes the state to the model.

The point engine answers "how bad is *this* turn?" with a single number. But a
snapshot lies about dynamics, so a trajectory layer (`src/lib/drift_trajectory.py`)
treats drift as a vector: an isolated spike that immediately falls back is the
system self-correcting and is left alone, while a sustained climb, a plateau
parked just under threshold, or a repeating-spike cycle is flagged. This
adaptive-vs-degenerative distinction is what keeps the false-positive rate at
zero — corrections fire on patterns, not on single twitchy turns.

Scoring is lexical and structural: hedges, filler, hype, and meta-narration
markers combined with verbosity, length, and complexity pressure, aggregated
with a noisy-OR so one strong signal carries while many weak ones still
accumulate. Code blocks are stripped before scoring — a terse persona is still
allowed to emit normal code. Everything is pure stdlib and fully deterministic:
the same text and profile always produce the same score.

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
make validate    # full plugin CI gate
```

See `EXPERIMENTS.md` for the ten-lap tuning ledger and `IMPLEMENTATION_NOTES.md`
for the design decisions.

## License

MIT — see [LICENSE](LICENSE).
