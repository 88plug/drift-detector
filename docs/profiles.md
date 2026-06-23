# Profiles

A profile defines your output contract: the threshold at which drift is called,
the sensitivity, per-component weights, and custom lexicons.

## Bundled profiles

| Profile | Threshold | Use case |
| --- | --- | --- |
| `caveman` | 50 | Terse persona, no preamble, no hedging |
| `strict-instructions` | 60 | Strict rule-following, low filler tolerance |
| `persona` | 65 | In-character voice work |
| `strict` | 55 | Maximum sensitivity |
| `relaxed` | 75 | Normal assistant, only extreme drift flagged |

Switch with:

```text
/drift:profile caveman
```

Inspect the active profile:

```text
/drift:profile show
```

List all available:

```text
/drift:profile list
```

## Authoring a custom profile

Profiles are JSON files. User profiles live in `$CLAUDE_PLUGIN_DATA/profiles/`
and shadow bundled ones in `<plugin>/profiles/`.

### Minimal example

```json
{
  "name": "my-terse",
  "extends": "caveman",
  "threshold": 60,
  "sensitivity": 1.2,
  "weights": { "hype": 2.0, "filler": 1.5 }
}
```

`extends` deep-merges your overrides onto the base profile — only specify what
differs.

### All fields

| Field | Type | Description |
| --- | --- | --- |
| `name` | string | Profile identifier |
| `kind` | `lexical` \| `judge` | Scoring mode (default: `lexical`) |
| `description` | string | Human-readable description |
| `threshold` | 0–100 | Verdict flips to `drift` at/above this |
| `sensitivity` | 0.1–3.0 | Linear multiplier on the whole score |
| `weights` | object | Per-component importance: `hedges`, `filler`, `hype`, `meta`, `verbosity`, `length`, `complexity` |
| `lexicons` | object | `{ "<class>": ["term", "multi word term"] }` — replaces the default for that class |
| `target_wps` | number | Target words-per-sentence (below = no verbosity penalty) |
| `max_wps` | number | Max words-per-sentence (above = full verbosity score) |
| `target_words` | number | Target prose word count |
| `max_words` | number | Max prose word count |
| `long_word_len` | number | Syllable threshold for "long word" in complexity |
| `max_long_ratio` | 0–1 | Long-word fraction that saturates the complexity score |

### Custom lexicons

Add your own vocabulary classes to the scoring:

```json
{
  "name": "formal-tone",
  "extends": "strict-instructions",
  "lexicons": {
    "hedges": ["perhaps", "possibly", "might", "could", "seemingly", "arguably"],
    "my_forbidden": ["synergy", "leverage", "paradigm", "circle back"]
  },
  "weights": {
    "my_forbidden": 3.0
  }
}
```

A class listed in `lexicons` fully replaces the default for that class. Multi-word
entries match as phrases; single tokens match whole words.

### Validate

```bash
python3 scripts/profiles.py validate --name my-terse \
  --user-dir "$CLAUDE_PLUGIN_DATA/profiles" --bundled-dir profiles
```

### Activate

```text
/drift:profile my-terse
```

Writes the profile name to `$CLAUDE_PLUGIN_DATA/active-profile`. Persists across
sessions.

## Judge profiles (experimental)

Set `"kind": "judge"` to use an LLM judge instead of the lexical engine:

```json
{
  "name": "semantic-drift",
  "kind": "judge",
  "threshold": 65,
  "judge": {
    "rubric": [
      "Is the response more verbose than the contract requires?",
      "Does the response hedge or qualify where the contract says not to?"
    ],
    "scale": "0-100",
    "model": "claude-haiku-4-5-20251001"
  }
}
```

Judge mode makes an MCP call on every scored turn. Use sparingly — it is slower
and non-deterministic.
