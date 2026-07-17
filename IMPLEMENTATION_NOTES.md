# Implementation Notes

## How to install

1. Add the repo as a marketplace and install the plugin:

   ```bash
   /plugin marketplace add 88plug/drift-detector
   /plugin install drift-detector@drift-detector
   ```

   This wires the hooks, commands, skill, and MCP server automatically from
   `.claude-plugin/plugin.json`.

2. Enable the status-line badge:

   ```bash
   bash "$(/plugin path drift-detector)/install.sh"
   ```

   `install.sh` merges our `statusLine` into `~/.claude/settings.json`. It is
   idempotent, takes a timestamped backup, and — if you already had a
   statusLine — preserves it to `scripts/.prior-statusline` so the badge is
   *appended* rather than clobbering your prompt. `install.sh --uninstall`
   reverses it and restores any prior statusline.

   The status line is the only part that can't be auto-installed: Claude Code's
   `statusLine` is a user-settings concept, and the plugin-manifest schema has
   no `statusLine` field — hence the separate one-liner.

For local development, run from a checkout with `CLAUDE_PLUGIN_ROOT` pointed at
the repo, and `.ci/validate_plugin.py` as the gate.

## How it works

State machine across the session lifecycle:

- **SessionStart** (`hooks/session-init.sh`) — creates the writable data tree,
  applies the idempotent schema (`scripts/schema.sql`), seeds the active profile
  (`caveman`) and a neutral badge, and prunes stale markers.
- **Stop** (`hooks/capture-stop.sh`) — the core path. Reads `session_id` +
  `transcript_path` from the hook payload, calls `scripts/score.py` to tail the
  last assistant turn, score it against the active profile via the engine, and
  persist a row + per-session rollup to the WAL-mode SQLite index. Writes the
  badge (`state|pct`, atomic, 0600) that the status line reads. If the turn
  drifted, drops a one-shot marker.
- **UserPromptSubmit** (`hooks/inject-correction.sh`) — before the model sees
  the new prompt, consumes any pending marker (rename-then-read, so it fires
  exactly once) and emits `additionalContext` reminding the model of the active
  contract.
- **MCP server** (`mcp/server.py` via `scripts/mcp-server.sh`) — a zero-dep
  stdlib JSON-RPC stdio server, DB opened `mode=ro`, exposing `drift_status`,
  `drift_recent`, `drift_explain`.

The scoring engine (`src/lib/drift_score.py`) strips code, tokenizes the prose,
computes per-component signals (hedges / filler / hype / meta lexicons by
saturating density; verbosity, length, complexity by calibrated ramps), and
combines them with a **weighted noisy-OR** so a single strong signal can drive
the score while many weak ones still accumulate. Output is 0–100, higher =
worse; `verdict` flips to `drift` at the profile threshold.

## Key design decisions

- **Noisy-OR, not a weighted average.** A flat average let short-but-egregious
  replies (a one-liner full of hype) dilute below threshold. Noisy-OR treats
  each component as an independent probability of drift; the implementation and
  rationale live in `references/scoring-internals.md`.
- **Determinism.** No clock, randomness, or locale in the score path — same
  text + profile always yields the same number. Makes CI and `--rebuild`
  trustworthy.
- **Never break the session.** Every hook is best-effort and exits 0; `score.py`
  has a last-resort guard that prints a neutral badge. Drift detection is never
  worth a broken turn.
- **Writable state lives outside the plugin cache.** The cache dir is read-only
  and wiped on update, so all mutable state goes under `$CLAUDE_PLUGIN_DATA`
  (`~/.claude/plugins/data/drift-detector-88plug/`); the badge lives under
  `$CLAUDE_CONFIG_DIR` so the status line reads it regardless of plugin version.
  `hooks/lib/resolve-paths.sh` resolves these with a glob fallback.
- **WAL + read-only MCP.** The Stop-hook writer and the MCP reader never block
  each other; the DB is rebuildable from transcripts (`score.py --rebuild`), so
  `/drift-detector:reset all` is safe.
- **Badge is dumb and tiny.** The status line does a single file read + printf —
  no python, no DB — so it stays cheap on every render, and composes with a
  pre-existing statusline.
- **One-shot, quiet correction.** The nudge fires once per drift event and tells
  the model not to mention it, avoiding a nagging feedback loop.
- **Profiles with inheritance.** `extends` deep-merges onto a bundled profile so
  a custom profile only states what differs. `kind: judge` is reserved for
  semantic personas the lexical engine can't capture.

## Known limitations

- **Lexical, English-tuned.** The default engine keys off English marker
  lexicons; other languages or highly domain-specific styles need a custom
  profile (or the judge backend).
- **Judge profiles are a stub.** `persona.json` declares a rubric, but the LLM
  judge backend is not yet wired — judge profiles currently fall back to the
  lexical baseline. The `.judge.lock` and scaffolding exist for it.
- **Prose-only.** Code is stripped before scoring, so drift expressed purely
  inside code blocks (e.g. over-commenting) is not detected.
- **Per-turn, not intra-turn.** Scoring happens on Stop, so the correction lands
  on the *next* prompt, not mid-reply.
- **Heuristic, not ground truth.** A high score is a strong signal, not proof;
  thresholds are tunable per profile for exactly this reason.

## Future improvements

- Wire the LLM judge backend (`kind: judge`) behind `.judge.lock`, with the
  lexical score as a cheap pre-filter.
- A `PreCompact`/`SessionEnd` summary writing a drift trend into the archive.
- Adaptive thresholds that learn a session's baseline verbosity.
- Optional `asyncRewake` on Stop to surface a correction in the *same* turn when
  drift is severe, instead of waiting for the next prompt.
- Per-project profile pinning (read an active profile from the repo).
