# Authoring a drift profile

Profiles are JSON. User profiles live in
`$CLAUDE_PLUGIN_DATA/profiles/<name>.json` and shadow the bundled ones in
`<plugin>/profiles/`.

## Minimal example

```json
{
  "name": "my-terse",
  "extends": "caveman",
  "threshold": 60,
  "sensitivity": 1.2,
  "weights": { "hype": 2.0, "filler": 1.5 }
}
```

`extends` deep-merges your overrides onto a bundled profile, so you only specify
what differs.

## Fields

- `name` (string), `kind` (`lexical` | `judge`), `description`.
- `threshold` (0..100) — verdict flips to `drift` at/above this.
- `sensitivity` (0.1..3.0) — linear multiplier on the whole score.
- `weights` — relative importance per component: `hedges`, `filler`, `hype`,
  `meta`, `verbosity`, `length`, `complexity` (and any custom lexicon class).
- `lexicons` — `{ "<class>": ["term", "multi word term", ...] }`. A class you
  list fully replaces the default for that class. Multi-word entries match as
  phrases; single tokens match whole words.
- Calibration: `target_wps` / `max_wps`, `target_words` / `max_words`,
  `long_word_len`, `max_long_ratio`.
- `judge` (kind=judge only): `{ "rubric": [...], "scale": "...", "model": "..." }`.

## Validate

```bash
bash scripts/run-python.sh scripts/profiles.py validate --name my-terse \
  --user-dir "$CLAUDE_PLUGIN_DATA/profiles" --bundled-dir profiles
```

## Activate

Set the active profile with `/drift-detector:profile my-terse` (writes the name to
`$CLAUDE_PLUGIN_DATA/active-profile`).
