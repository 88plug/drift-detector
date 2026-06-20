# Scoring internals

The engine (`src/lib/drift_score.py`) is deterministic and stdlib-only: the same
text + profile always yields the same score.

## Pipeline

1. **Strip code.** Fenced and inline code are removed — only natural-language
   prose is scored, so emitting normal code never counts as drift.
2. **Tokenize.** Lowercased, accent-stripped word tokens; sentences split on
   `.!?`.
3. **Components (each normalized 0..1):**
   - Lexicon classes — `hedges`, `filler`, `hype`, `meta` — scored by marker
     *density* through a saturating curve (`1 - e^(-rate*18)`), so one stray
     filler word barely moves the score but a reply made of hedges saturates.
   - `verbosity` — words-per-sentence ramped between the profile's `target_wps`
     and `max_wps`.
   - `length` — prose word count ramped between `target_words` and `max_words`.
   - `complexity` — fraction of long words (>= `long_word_len`) up to
     `max_long_ratio`.
4. **Aggregate with weighted noisy-OR.** Each component is treated as an
   independent probability of drift, scaled by its weight relative to the max
   weight, then combined: `P = 1 - prod(1 - p_i)`. This lets any single strong
   signal drive the score high (a short but hype-saturated reply still scores
   high) while many weak signals still accumulate — a plain average wrongly let
   structural components dilute clear lexical violations.
5. **Scale + clamp.** `score = clamp(P * 100 * sensitivity, 0, 100)`.
   `verdict = drift` iff `score >= threshold`.

## Explainability

`contributions` attributes the final score across components proportional to the
log-odds each removed from the noisy-OR product (sums to the score).
`top_offenders` is the human-readable top 3. `drift_explain` surfaces these.

## Profiles

A profile sets weights, thresholds, calibration knobs, and optional lexicon
overrides. `kind: "judge"` profiles are scored semantically by an LLM against a
rubric instead of lexically (falls back to the lexical baseline when the judge
backend is unavailable).
