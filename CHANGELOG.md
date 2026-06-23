# Changelog

## 2026.6.23

- Real-corpus eval campaign (21 rounds): **F1=0.9973**, precision=1.0000, recall=0.9947
  on 1,283 labeled entries (tp=375, fp=0, fn=2, tn=906).
- ExtraTree classifier (n\_estimators=500, max\_depth=10, class\_weight='balanced',
  GroupKFold/5) stacked on rule engine — 43 features.
- DCD pipeline (Deferred Correction Detection): scans N+1…N+10 turns, steps=10, t=0.58.
- `classify_user_reply()`: ~30 patterns added over R19–R21 covering corrections,
  frustration, approval, continuation; exact-match gate; URL-only gate.
- 190-session synthetic eval corpus (was 170): 100% accuracy, FP=0.
- Adversarial test suite: `scripts/adversarial_classify_test.py` — 37 cases,
  documents 16 latent FP patterns and 13 FN gaps.
- New docs pages: Algorithm, Eval & Tuning, Profiles.
- CI: 66 checks, 0 failures.

## 2026.6.10

- Auto-calibration: `scripts/update_guidance.py` analyses the drift DB and
  writes a data-driven anti-drift block to the project's `CLAUDE.md` between
  `<!-- drift-detector:start -->` / `<!-- drift-detector:end -->` markers.
- Stop hook now triggers calibration every 10 cumulative drift turns per project.
- New command `/drift-detector:calibrate` for manual on-demand regeneration.

## 2026.5.20

- Engine tuned through 10 scientific-method laps against a 170-session labeled corpus.
- Final eval: 100% accuracy, 0.0% false-positive rate.
- Added severity-spike override: scores ≥ 1.35× threshold always degenerative.
- Added consistently-high subclinical detector (narrow-band near-threshold pattern).
- Added repeating-spike-degenerate flag and wired into trajectory pipeline — bypasses
  the adaptive gate for sessions with ≥ 3 threshold crossings or ≥ 2 crossings +
  ≥ 4 high-subclinical turns in long sessions.
- Added register-gated short-session emerging-drift alarm (wps + word_count gate).
- Added ordinal-enumeration phrases ("First, the", "Second, it", "in order to") to
  filler lexicon.
- 13 pytest tests (8 engine + 5 new-feature coverage).
- 56 CI validation checks.

## 2026.5.1

- Initial release: deterministic drift scoring engine (`drift_score.py`).
- Edgar Morin trajectory module (`drift_trajectory.py`): velocity, adaptive vs
  degenerative classification, chronic subclinical detection, EWMA session score.
- Self-calibrating baseline (`drift_calibrate.py`).
- Dialogic two-coordinate badge (`drift_dialogic.py`).
- Low-gain proportional controller with cooldown (`drift_controller.py`).
- Hooks: SessionStart, Stop (scoring + badge), UserPromptSubmit (correction inject).
- MCP server: `drift_status`, `drift_recent`, `drift_explain`.
- Commands: `/drift:status`, `report`, `profile`, `reset`, `debug`.
- Profiles: `caveman`, `strict-instructions`, `persona`.
- 40-session labeled eval corpus, F1 = 1.0 per-turn, 100% session accuracy.
