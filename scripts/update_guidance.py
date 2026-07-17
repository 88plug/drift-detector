#!/usr/bin/env python3
"""update_guidance.py — auto-calibrate CLAUDE.md anti-drift section from DB.

Queries the drift SQLite DB (stylometric signal) and optionally the
total-recall index.db (real user-correction signal) to build a data-driven
guidance block between marker comments in the target CLAUDE.md.

When --recall-db is provided the block leads with mistake-based bullets
(what the agent actually did wrong across sessions) ranked by cross-session
recurrence. The stylometric block is retained as a secondary <details> section.

When --recall-db is absent the block falls back to the existing stylometric
component-frequency bullets.

The block is idempotent: re-running updates the existing markers in place.
If the target file doesn't exist, it's created. If the target file has no
markers, the block is appended.

Usage:
  python3 update_guidance.py --db /path/to/drift.db --output /project/CLAUDE.md
  python3 update_guidance.py --recall-db ~/.claude/plugins/data/total-recall/index.db \\
      --output /project/CLAUDE.md --dry-run
  python3 update_guidance.py --db /path/to/drift.db \\
      --recall-db ~/.claude/plugins/data/total-recall/index.db \\
      --output /project/CLAUDE.md --cwd /home/andrew/my-project
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

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(os.path.dirname(_HERE), "src", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

try:
    from drift_user_correction import classify_user_reply, classify_mistake_type

    _HAS_CORRECTION_LIB = True
except ImportError:
    _HAS_CORRECTION_LIB = False

MARKER_START = "<!-- drift-detector:start -->"
MARKER_END = "<!-- drift-detector:end -->"

# Human-readable labels for DB component keys.
COMPONENT_LABELS: dict[str, str] = {
    "verbosity": "Verbosity (long sentences)",
    "length": "Response length",
    "complexity": "Sentence complexity",
    "hedging": "Hedging language",
    "filler": "Filler / pleasantry words",
    "escalation": "Uncertainty escalation",
    "affirmation": "Sycophantic affirmations",
    "meta": "Meta-commentary about the task",
    "passive": "Passive / weak constructions",
}

# Advice snippets keyed by component. Used to build actionable bullets.
COMPONENT_ADVICE: dict[str, str] = {
    "verbosity": "Break sentences at conjunctions. Target ≤15 words/sentence.",
    "length": "Lead with the answer; cut trailing restatements.",
    "complexity": "Prefer parallel short clauses over embedded subordinates.",
    "hedging": 'State directly. Drop "might", "could", "seems", "perhaps", "I think".',
    "filler": 'Cut openers: "Sure!", "Certainly!", "Of course!", "Great question!".',
    "escalation": "Commit to an answer. Avoid stacking qualifiers.",
    "affirmation": 'No mirror-validation before answering. Skip "That\'s a great point".',
    "meta": "Don't narrate the process (\"I'll now look at…\"). Just do the thing.",
    "passive": "Use active voice. Name the actor.",
}


# --------------------------------------------------------------------------- #
# Mistake extraction from total-recall index.db
# --------------------------------------------------------------------------- #

_MISTAKE_LABELS: dict[str, str] = {
    "mcp_first_violation": "Reach for the configured MCP, not generic shell",
    "unverified_claim": "Verify before asserting — fan out agents for high-stakes claims",
    "wrong_target": "Confirm the exact target (file/repo/host) before any irreversible op",
    "premature_action": "Never push, restart, or let jobs run unmonitored without explicit go-ahead",
    "ignored_instruction": "Apply a stated preference from the first time, not after being reminded",
    "overengineering": "KISS — prefer a one-liner solution over a complex one",
}

_MISTAKE_SPOT: dict[str, str] = {
    "mcp_first_violation": "about to run generic Bash / give a text-only answer for a task an MCP tool owns.",
    "unverified_claim": "about to state a conclusion without having read the actual source / session logs.",
    "wrong_target": "about to Edit/Write/run a command on a path or host — confirm it matches what the user named.",
    "premature_action": "about to push, restart, deploy, or let a long job run without an explicit checkpoint.",
    "ignored_instruction": "about to do something the user has corrected before — check prior turns for stated preferences.",
    "overengineering": "solution has more moving parts than necessary — ask whether a simpler path exists first.",
}


def load_mistakes_from_recall(
    recall_db: str,
    cwd_filter: str | None = None,
    limit_days: int = 90,
) -> list[dict]:
    """Query total-recall index.db for user corrections and classify each.

    Returns a list of {category, correction_text, prior_text, session_id, cwd}
    sorted by ts descending.  Returns [] if DB not accessible or lib missing.
    """
    if not _HAS_CORRECTION_LIB:
        return []
    if not Path(recall_db).exists():
        return []

    try:
        con = sqlite3.connect(f"file:{recall_db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return []

    try:
        # Heuristic SQL: candidate user messages that start with a correction opener.
        # We then run the full classify_user_reply() filter in Python.
        # total-recall stores ts as Unix seconds (not milliseconds)
        cutoff_ts = int(datetime.now(timezone.utc).timestamp() - limit_days * 86400)
        cwd_clause = "AND m.cwd = :cwd" if cwd_filter else ""
        rows = con.execute(
            f"""
            SELECT m.id, m.session_id, m.cwd, m.ts, m.source_file,
                   m.byte_offset, m.text
            FROM messages m
            WHERE m.role = 'user'
              AND m.text IS NOT NULL
              AND LENGTH(TRIM(m.text)) > 2
              AND m.ts >= :cutoff
              {cwd_clause}
            ORDER BY m.ts DESC
            LIMIT 2000
            """,
            {"cutoff": cutoff_ts, "cwd": cwd_filter or ""},
        ).fetchall()

        corrections = []
        for row in rows:
            text = (row["text"] or "").strip()
            if not text:
                continue
            subtype = classify_user_reply(text)
            if subtype not in (
                "correction_style",
                "correction_substance",
                "frustration",
            ):
                continue

            # Find preceding assistant message in same source file
            prior_row = con.execute(
                """
                SELECT text FROM messages
                WHERE source_file = ? AND role = 'assistant'
                  AND byte_offset < ?
                  AND text IS NOT NULL AND LENGTH(TRIM(text)) > 0
                ORDER BY byte_offset DESC
                LIMIT 1
                """,
                (row["source_file"], row["byte_offset"]),
            ).fetchone()
            prior_text = (prior_row["text"] if prior_row else "") or ""

            category = classify_mistake_type(text, prior_text)
            if category == "other":
                continue

            corrections.append(
                {
                    "category": category,
                    "correction_text": text[:300],
                    "prior_text": prior_text[:300],
                    "session_id": row["session_id"],
                    "cwd": row["cwd"],
                }
            )

        return corrections
    except Exception:
        return []
    finally:
        con.close()


def tally_mistakes(corrections: list[dict]) -> dict[str, dict]:
    """Return {category: {count, sessions, examples}} ranked by distinct-session count."""
    buckets: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "sessions": set(), "examples": []}
    )
    for c in corrections:
        cat = c["category"]
        buckets[cat]["count"] += 1
        buckets[cat]["sessions"].add(c["session_id"])
        if len(buckets[cat]["examples"]) < 2:
            buckets[cat]["examples"].append(c["correction_text"])
    # Convert sets to counts for serialisability
    return {
        cat: {**v, "session_count": len(v["sessions"]), "sessions": None}
        for cat, v in buckets.items()
    }


def build_mistake_bullets(mistakes: dict[str, dict]) -> str:
    """Build the ranked mistake-based guidance body."""
    ranked = sorted(
        mistakes.items(),
        key=lambda kv: kv[1]["session_count"],
        reverse=True,
    )
    top = ranked[:5]
    lines = []
    for cat, stats in top:
        label = _MISTAKE_LABELS.get(cat, cat.replace("_", " ").title())
        spot = _MISTAKE_SPOT.get(cat, "")
        sc = stats["session_count"]
        lines.append(
            f"- **{label}** — recurred in {sc} session{'s' if sc != 1 else ''}. "
            f"Spot it: you are {spot}"
        )
    return "\n".join(lines)


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
            "drift_rate": round(100 * drift_turns / total_turns, 1)
            if total_turns
            else 0,
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


def build_block(
    stats: dict,
    drifted_turns: list[dict],
    limit_sessions: int,
    mistakes: dict[str, dict] | None = None,
) -> str:
    """Build the CLAUDE.md guidance block string.

    When `mistakes` (from tally_mistakes()) is provided the block leads with
    mistake-based bullets grounded in real user pushback. The stylometric
    component-frequency block is included as a secondary <details> section.
    When `mistakes` is absent or empty the block falls back to the stylometric
    bullets only.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n_sessions = stats.get("sessions", 0)
    drift_rate = stats.get("drift_rate", 0)
    n_drifted = len(drifted_turns)
    n_mistakes = sum(v["count"] for v in mistakes.values()) if mistakes else 0

    # ---- stylometric body ------------------------------------------------- #
    if n_drifted == 0:
        stylometric_body = (
            f"No drift detected across {n_sessions} sessions analysed. "
            "No targeted rules needed yet."
        )
    else:
        comp_counts = tally_components(drifted_turns)
        off_counts = tally_offenders(drifted_turns)
        merged: dict[str, int] = defaultdict(int)
        for k, v in comp_counts.items():
            merged[k] += v * 2
        for k, v in off_counts.items():
            merged[k] += v
        ranked = sorted(merged.items(), key=lambda x: x[1], reverse=True)
        top = [(k, v) for k, v in ranked if v > 0][:5]
        bullets = []
        for cat, cnt in top:
            pct = round(100 * cnt / n_drifted / (2 if cat in comp_counts else 1))
            label = COMPONENT_LABELS.get(cat, cat.title())
            advice = COMPONENT_ADVICE.get(cat, "Reduce this pattern.")
            bullets.append(
                f"- **{label}** (fires in ~{min(pct, 100)}% of drifted turns): {advice}"
            )
        stylometric_body = "\n".join(bullets)

    # ---- assemble block --------------------------------------------------- #
    if mistakes:
        # Mistake-based primary + stylometric secondary
        mistake_body = build_mistake_bullets(mistakes)
        n_correction_sessions = max(
            (v["session_count"] for v in mistakes.values()), default=0
        )
        primary_header = (
            f"## Mistake prevention (calibrated from {n_mistakes} real corrections "
            f"across {n_correction_sessions} sessions)"
        )
        lines = [
            MARKER_START,
            f"<!-- calibrated {now} · {n_mistakes} corrections · "
            f"drift rate {drift_rate}% -->",
            "",
            primary_header,
            "",
            mistake_body,
            "",
            "<details>",
            "<summary>Stylometric drift signal (secondary)</summary>",
            "",
            stylometric_body,
            "",
            "</details>",
            "",
            MARKER_END,
        ]
    else:
        lines = [
            MARKER_START,
            f"<!-- calibrated {now} · last {limit_sessions} sessions · "
            f"{n_sessions} sampled · drift rate {drift_rate}% -->",
            "",
            "## Anti-drift (auto-calibrated from real session data)",
            "",
            f"Drift observed in {drift_rate}% of turns across {n_sessions} recent sessions.",
            "",
            stylometric_body,
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
    ap = argparse.ArgumentParser(
        description="Auto-calibrate CLAUDE.md anti-drift section"
    )
    ap.add_argument("--db", default=None, help="Path to drift.db (stylometric signal)")
    ap.add_argument(
        "--output", required=True, help="Path to CLAUDE.md / AGENTS.md to update"
    )
    ap.add_argument(
        "--sessions",
        type=int,
        default=50,
        help="Recent sessions to analyse (default 50)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print block, don't write")
    ap.add_argument(
        "--min-drift-turns",
        type=int,
        default=5,
        help="Minimum drifted turns required to write non-trivial guidance (default 5)",
    )
    ap.add_argument(
        "--recall-db",
        default=None,
        help="Path to total-recall index.db for mistake-based guidance",
    )
    ap.add_argument(
        "--cwd",
        default=None,
        help="Filter total-recall corrections to this project directory",
    )
    ap.add_argument(
        "--days",
        type=int,
        default=90,
        help="How many days of total-recall history to scan (default 90)",
    )
    args = ap.parse_args()

    if not args.db and not args.recall_db:
        print("update_guidance: provide --db and/or --recall-db", file=sys.stderr)
        sys.exit(1)

    # --- Load stylometric signal -------------------------------------------
    stats = load_session_stats(args.db, args.sessions) if args.db else {}
    drifted = load_drifted_turns(args.db, args.sessions) if args.db else []

    if args.db and not stats:
        print("no sessions in DB yet; skipping", file=sys.stderr)
        sys.exit(0)

    if args.db and len(drifted) < args.min_drift_turns and not args.recall_db:
        print(
            f"only {len(drifted)} drifted turns (< {args.min_drift_turns}); "
            "not enough signal, skipping",
            file=sys.stderr,
        )
        sys.exit(0)

    # --- Load mistake signal from total-recall ----------------------------
    mistakes: dict[str, dict] | None = None
    if args.recall_db:
        corrections = load_mistakes_from_recall(
            args.recall_db, cwd_filter=args.cwd, limit_days=args.days
        )
        if len(set(c["session_id"] for c in corrections)) >= 3:
            mistakes = tally_mistakes(corrections)
        else:
            print(
                f"update_guidance: only {len(corrections)} corrections found in recall DB "
                "(need ≥3 distinct sessions); skipping mistake-based block",
                file=sys.stderr,
            )

    block = build_block(stats, drifted, args.sessions, mistakes=mistakes)

    if args.dry_run:
        print(block)
        return

    action = update_file(args.output, block, dry_run=False)
    print(f"update_guidance: {action} → {args.output}")


if __name__ == "__main__":
    main()
