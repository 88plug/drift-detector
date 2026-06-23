# Algorithm

Drift Detector uses a three-layer pipeline: a deterministic lexical/structural
**point engine**, a **trajectory layer** that classifies drift over time, and a
real-corpus **ExtraTree classifier + DCD pipeline** for production eval.

## Point engine (`src/lib/drift_score.py`)

Answers: "how bad is *this single turn*?" Returns a 0–100 score.

### Pipeline

1. **Strip code.** Fenced and inline code blocks removed — prose-only scoring,
   so emitting normal code never counts as drift.
2. **Tokenize.** Lowercased, accent-stripped word tokens; sentences split on `.!?`.
3. **Component scores** (each normalized 0..1):

| Component | How measured |
| --- | --- |
| `hedges` | Marker density via saturating curve `1 - e^(-rate×18)` |
| `filler` | Same saturating curve — one stray filler barely moves score; marker-saturated reply hits ceiling |
| `hype` | Positive hype vocabulary density |
| `meta` | Meta-narration markers ("I'll now explain…", "Let me walk you through…") |
| `verbosity` | Words-per-sentence ramped between profile's `target_wps` and `max_wps` |
| `length` | Prose word count ramped between `target_words` and `max_words` |
| `complexity` | Fraction of long words (≥ `long_word_len`) up to `max_long_ratio` |

4. **Noisy-OR aggregation.** One strong signal carries; many weak ones accumulate.
   Weighted by the active profile's `weights` map.
5. **Polysemous corroboration gate.** Polysemous words (`leverage`, `robust`, etc.)
   only count toward a class when another lexical marker fires or real length
   pressure exists — eliminates technical-prose false positives.
6. **Structural-only damping.** When total lexical hits == 0, structural components
   (`verbosity`, `length`, `complexity`) are scaled ×0.8 — dense jargon without
   hedges/filler is not drift.

### Profiles

A profile defines the contract: `threshold` (0–100), `sensitivity` multiplier,
per-component `weights`, custom `lexicons`, and calibration parameters. See
[Profiles](profiles.md) for authoring.

## Trajectory layer (`src/lib/drift_trajectory.py`)

Answers: "is this session drifting, adapting, or at a chronic subclinical level?"

Takes a sequence of per-turn scores and classifies:

- **Adaptive drift** — isolated spike that immediately falls back. System was
  self-correcting; no nudge fired.
- **Degenerative drift** — sustained climb, plateau near threshold, or
  repeating-spike cycle. Correction nudge fires.
- **Chronic subclinical** — narrow band of scores parked just under threshold
  across multiple turns. Flagged even though no single turn crosses threshold.
- **Repeating-spike** — session that alternates above/below threshold ≥3 times.
  Adaptive turn-by-turn but degenerative as a cycle; bypasses the adaptive gate.
- **Velocity alarm** — rate of score increase over recent turns exceeds a
  per-profile ramp threshold, catching slow creep before it crosses threshold.

### Why trajectory matters

A single-turn snapshot lies about dynamics. A session can score 85 once and
recover (not drift) or score 65 consistently (chronic subclinical drift). The
trajectory layer is what keeps false positives at zero: corrections fire on
*patterns*, not on individual twitchy turns.

## ExtraTree classifier (`scripts/backtest_real.py`)

43-feature ExtraTree (n\_estimators=500, max\_depth=10, class\_weight='balanced'),
GroupKFold(5) by session\_id, threshold t=0.58. Features include:

- Point scores, trajectory flags, drift rate, max streak
- `classify_user_reply()` labels on following user messages (correction\_style,
  correction\_substance, frustration, approval, continuation, new\_task)
- Turn position, burst length, interrupt flags

### DCD pipeline (Deferred Correction Detection)

Scans N+1…N+10 turns following a candidate for user correction signals. Critical
for recovering cases where user feedback is delayed (e.g., continues working for
a few turns before explicitly correcting).

`steps=10, t=0.58` — tuned on the real corpus to maximize F1 without introducing
false positives.

## Eval results

| Metric | Value |
| --- | --- |
| F1 | **0.9973** |
| Precision | 1.0000 |
| Recall | 0.9947 |
| tp | 375 |
| fp | **0** |
| fn | 2 |
| tn | 906 |
| Corpus size | 1,283 labeled entries |

Two irreducible FNs: one credential provision in ok context (3 overlapping ok
patterns prevent safe classification) and one bare "Try now" indistinguishable
from ok entries without session context.

See [Eval](eval.md) for the full 21-round scientific-method campaign.
