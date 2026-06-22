#!/usr/bin/env python3
"""update_guidance.py — auto-calibrate CLAUDE.md anti-drift section from DB.

Queries the drift SQLite DB, tallies which components fire most often across
drifted turns, and writes a data-driven guidance block between marker comments
in the target CLAUDE.md (or AGENTS.md, or any file passed as --output).

The block is idempotent: re-running updates the existing markers in place.
If the target file doesn't exist, it's created. If the target file has no
markers, the block is appended.

Usage:
  python3 update_guidance.py --db /path/to/drift.db --output /project/CLAUDE.md
  python3 update_guidance.py --db /path/to/drift.db --output /project/CLAUDE.md --sessions 30
  python3 update_guidance.py --db /path/to/drift.db --output /project/CLAUDE.md --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

MARKER_START = "<!-- drift-detector:start -->"
MARKER_END   = "<!-- drift-detector:end -->"

# Human-readable labels for DB component keys.
COMPONENT_LABELS: dict[str, str] = {
    "verbosity":   "Verbosity (long sentences)",
    "length":      "Response length",
    "complexity":  "Sentence complexity",
    "hedging":     "Hedging language",
    "filler":      "Filler / pleasantry words",
    "escalation":  "Uncertainty escalation",
    "affirmation": "Sycophantic affirmations",
    "meta":        "Meta-commentary about the task",
    "passive":     "Passive / weak constructions",
}

# Advice snippets keyed by component. Used to build actionable bullets.
COMPONENT_ADVICE: dict[str, str] = {
    "verbosity":   'Break sentences at conjunctions. Target ≤15 words/sentence.',
    "length":      'Lead with the answer; cut trailing restatements.',
    "complexity":  'Prefer parallel short clauses over embedded subordinates.',
    "hedging":     'State directly. Drop "might", "could", "seems", "perhaps", "I think".',
    "filler":      'Cut openers: "Sure!", "Certainly!", "Of course!", "Great question!".',
    "escalation":  'Commit to an answer. Avoid stacking qualifiers.',
    "affirmation": 'No mirror-validation before answering. Skip "That\'s a great point".',
    "meta":        'Don\'t narrate the process ("I\'ll now look at…"). Just do the thing.',
    "passive":     'Use active voice. Name the actor.',
}


def load_drifted_turns(db_path: str, limit_sessions: int) -> list[dict]:
    """Return drifted turns from the most recent `limit_sessions` sessions."""
    if not Path(db_path).exists():
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        # Most recent N session IDs by last_ts.
        sessions = con.execute(
            "SELECT session_id FROM sessions ORDER BY last_ts DESC LIMIT ?",
            (limit_sessions,),
        ).fetchall()
        if not sessions:
            return []
        placeholders = ",".join("?" * len(sessions))
        sids = [r["session_id"] for r in sessions]
        rows = con.execute(
            f"""SELECT verdict, score, threshold, top_offenders, components
                FROM scores
                WHERE session_id IN ({placeholders})
                  AND verdict = 'drift'""",
            sids,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def load_session_stats(db_path: str, limit_sessions: int) -> dict:
    """Return aggregate stats for the most recent N sessions."""
    if not Path(db_path).exists():
        return {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT turns, drift_turns FROM sessions ORDER BY last_ts DESC LIMIT ?",
            (limit_sessions,),
        ).fetchall()
        if not rows:
            return {}
        total_turns = sum(r["turns"] for r in rows)
        drift_turns = sum(r["drift_turns"] for r in rows)
        return {
            "sessions": len(rows),
            "total_turns": total_turns,
            "drift_turns": drift_turns,
            "drift_rate": round(100 * drift_turns / total_turns, 1) if total_turns else 0,
        }
    finally:
        con.close()


def tally_components(drifted_turns: list[dict]) -> dict[str, int]:
    """Count how many drifted turns each component fires in."""
    counts: dict[str, int] = defaultdict(int)
    for row in drifted_turns:
        comp_json = row.get("components") or "{}"
        try:
            comp = json.loads(comp_json)
        except (json.JSONDecodeError, TypeError):
            comp = {}
        for key, val in comp.items():
            if isinstance(val, (int, float)) and val > 0.1:
                counts[key] += 1
    return dict(counts)


def tally_offenders(drifted_turns: list[dict]) -> dict[str, int]:
    """Count occurrences of each category in top_offenders JSON arrays."""
    counts: dict[str, int] = defaultdict(int)
    for row in drifted_turns:
        off_json = row.get("top_offenders") or "[]"
        try:
            offs = json.loads(off_json)
        except (json.JSONDecodeError, TypeError):
            offs = []
        for item in offs:
            # format: "category (+X.X)"
            cat = str(item).split(" ")[0].lower()
            counts[cat] += 1
    return dict(counts)


def build_block(stats: dict, drifted_turns: list[dict], limit_sessions: int) -> str:
    """Build the CLAUDE.md guidance block string."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n_sessions = stats.get("sessions", 0)
    drift_rate = stats.get("drift_rate", 0)
    n_drifted = len(drifted_turns)

    if n_drifted == 0:
        body = (
            f"No drift detected across {n_sessions} sessions analysed. "
            "No targeted rules needed yet."
        )
    else:
        comp_counts = tally_components(drifted_turns)
        off_counts  = tally_offenders(drifted_turns)

        # Merge both signals; prefer component tally (more granular).
        merged: dict[str, int] = defaultdict(int)
        for k, v in comp_counts.items():
            merged[k] += v * 2   # weight component hits higher
        for k, v in off_counts.items():
            merged[k] += v

        ranked = sorted(merged.items(), key=lambda x: x[1], reverse=True)
        top = [(k, v) for k, v in ranked if v > 0][:5]

        bullets = []
        for cat, cnt in top:
            pct = round(100 * cnt / n_drifted / (2 if cat in comp_counts else 1))
            label = COMPONENT_LABELS.get(cat, cat.title())
            advice = COMPONENT_ADVICE.get(cat, "Reduce this pattern.")
            bullets.append(f"- **{label}** (fires in ~{min(pct,100)}% of drifted turns): {advice}")

        body = "\n".join(bullets)

    lines = [
        MARKER_START,
        f"<!-- calibrated {now} · last {limit_sessions} sessions · "
        f"{n_sessions} sampled · drift rate {drift_rate}% -->",
        "",
        "## Anti-drift (auto-calibrated from real session data)",
        "",
        f"Drift observed in {drift_rate}% of turns across {n_sessions} recent sessions.",
        "",
        body,
        "",
        MARKER_END,
    ]
    return "\n".join(lines)


def update_file(output_path: str, block: str, dry_run: bool) -> str:
    """Insert/replace the marker block in output_path. Returns action taken."""
    p = Path(output_path)
    existing = p.read_text(encoding="utf-8") if p.exists() else ""

    if MARKER_START in existing and MARKER_END in existing:
        pattern = re.compile(
            re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
            re.DOTALL,
        )
        updated = pattern.sub(block, existing)
        action = "updated existing block"
    elif existing:
        updated = existing.rstrip("\n") + "\n\n" + block + "\n"
        action = "appended block to existing file"
    else:
        updated = block + "\n"
        action = "created file with block"

    if not dry_run:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(updated, encoding="utf-8")

    return action


def main() -> None:
    ap = argparse.ArgumentParser(description="Auto-calibrate CLAUDE.md anti-drift section")
    ap.add_argument("--db",       required=True, help="Path to drift.db")
    ap.add_argument("--output",   required=True, help="Path to CLAUDE.md / AGENTS.md to update")
    ap.add_argument("--sessions", type=int, default=50, help="Recent sessions to analyse (default 50)")
    ap.add_argument("--dry-run",  action="store_true", help="Print block, don't write")
    ap.add_argument("--min-drift-turns", type=int, default=5,
                    help="Minimum drifted turns required to write non-trivial guidance (default 5)")
    args = ap.parse_args()

    stats = load_session_stats(args.db, args.sessions)
    drifted = load_drifted_turns(args.db, args.sessions)

    if not stats:
        print("no sessions in DB yet; skipping", file=sys.stderr)
        sys.exit(0)

    if len(drifted) < args.min_drift_turns:
        print(
            f"only {len(drifted)} drifted turns (< {args.min_drift_turns}); "
            "not enough signal, skipping",
            file=sys.stderr,
        )
        sys.exit(0)

    block = build_block(stats, drifted, args.sessions)

    if args.dry_run:
        print(block)
        return

    action = update_file(args.output, block, dry_run=False)
    print(f"update_guidance: {action} → {args.output}")


if __name__ == "__main__":
    main()
