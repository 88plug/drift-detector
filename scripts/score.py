#!/usr/bin/env python3
"""score.py — transcript-tail scorer + SQLite persistence (stdlib only).

This is the glue the Stop hook calls. It:

  1. Reads the last assistant turn (from a transcript path, --text, or stdin).
  2. Loads the active profile and scores the turn with the drift_score engine.
  3. Persists the result to the WAL-mode SQLite index and updates the
     per-session rollup.
  4. Emits a compact badge line ("state|pct") on stdout for the statusline, and
     a richer JSON record on fd 3 if open (so the hook can log it).

Determinism + safety: same transcript + profile => same row. A per-turn hash
dedupes re-scoring if the Stop hook fires twice for one turn. Everything is
wrapped so a failure prints a neutral badge and exits 0 — drift scoring must
never break the user's session.

CLI:
  score.py --transcript <path> --session <id> --db <path> --profile-json <p>
  score.py --text "..." --profile-json <p>        # ad-hoc, no DB
  score.py --rebuild --transcript <path> ...       # rescore whole transcript
  score.py --status --db <path> --session <id>     # JSON rollup for a session
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import List, Optional

# Import the engine. Support both "installed" layout and direct invocation.
_HERE = os.path.dirname(os.path.abspath(__file__))
for cand in (
    os.path.join(_HERE, "..", "src", "lib"),
    os.path.join(_HERE, "..", "lib"),
    _HERE,
):
    if os.path.isfile(os.path.join(cand, "drift_score.py")):
        sys.path.insert(0, os.path.abspath(cand))
        break

import drift_score  # noqa: E402

try:
    import drift_calibrate  # noqa: E402
except ImportError:  # calibration is optional; degrade to no calibration.
    drift_calibrate = None  # type: ignore[assignment]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Transcript extraction
# --------------------------------------------------------------------------- #
def _text_from_message(msg) -> str:
    """Pull plain text out of a Claude transcript message content field."""
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        content = msg.get("content", msg.get("text", ""))
    else:
        content = msg
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") in (None, "text") and "text" in block:
                    parts.append(str(block["text"]))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def last_assistant_turn(transcript_path: str) -> str:
    """Return the text of the final assistant message in a .jsonl transcript.

    Transcript is JSON-lines; we scan from the end for the last record whose
    role/type is assistant. Tolerant of schema variation and trailing garbage.
    """
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = rec.get("role") or rec.get("type")
        msg = rec.get("message", rec)
        if isinstance(msg, dict):
            role = msg.get("role", role)
        if role == "assistant":
            txt = _text_from_message(msg)
            if txt.strip():
                return txt
    return ""


def all_assistant_turns(transcript_path: str) -> List[str]:
    out: List[str] = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message", rec)
                role = (
                    (msg.get("role") if isinstance(msg, dict) else None)
                    or rec.get("role")
                    or rec.get("type")
                )
                if role == "assistant":
                    txt = _text_from_message(msg)
                    if txt.strip():
                        out.append(txt)
    except OSError:
        return []
    return out


# --------------------------------------------------------------------------- #
# Profile loading
# --------------------------------------------------------------------------- #
def load_profile_dict(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
EWMA_ALPHA = 0.4  # weight on the newest turn for the smoothed trend


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    con = sqlite3.connect(db_path, timeout=5.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def ensure_schema(con: sqlite3.Connection, schema_path: Optional[str]) -> None:
    if schema_path and os.path.isfile(schema_path):
        with open(schema_path, "r", encoding="utf-8") as fh:
            con.executescript(fh.read())
    else:
        # Minimal inline fallback so the writer never hard-fails on a missing
        # schema.sql (e.g. partial install).
        con.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS scores(
              id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
              ts TEXT NOT NULL, profile TEXT NOT NULL, engine_version TEXT NOT NULL,
              score REAL NOT NULL, threshold REAL NOT NULL, verdict TEXT NOT NULL,
              word_count INTEGER NOT NULL DEFAULT 0, top_offenders TEXT,
              components TEXT, transcript_hash TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_scores_dedupe
              ON scores(session_id, transcript_hash) WHERE transcript_hash IS NOT NULL;
            CREATE TABLE IF NOT EXISTS sessions(
              session_id TEXT PRIMARY KEY, first_ts TEXT NOT NULL, last_ts TEXT NOT NULL,
              turns INTEGER NOT NULL DEFAULT 0, drift_turns INTEGER NOT NULL DEFAULT 0,
              last_score REAL NOT NULL DEFAULT 0, max_score REAL NOT NULL DEFAULT 0,
              ewma_score REAL NOT NULL DEFAULT 0, profile TEXT);
            """
        )
    con.commit()


def persist(
    con: sqlite3.Connection,
    session_id: str,
    result: dict,
    transcript_hash: Optional[str],
) -> bool:
    """Insert a score row + update the session rollup. Returns True if a new
    row was written, False if it was a dedupe no-op."""
    ts = _now_iso()
    try:
        cur = con.execute(
            """INSERT INTO scores(session_id, ts, profile, engine_version, score,
                   threshold, verdict, word_count, top_offenders, components,
                   transcript_hash)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id,
                ts,
                result.get("profile", "caveman"),
                result.get("engine_version", drift_score.ENGINE_VERSION),
                float(result.get("score", 0.0)),
                float(result.get("threshold", 70.0)),
                result.get("verdict", "ok"),
                int(result.get("stats", {}).get("word_count", 0)),
                json.dumps(result.get("top_offenders", [])),
                json.dumps(result.get("components", {})),
                transcript_hash,
            ),
        )
    except sqlite3.IntegrityError:
        return False  # dedupe: this exact turn already scored

    score = float(result.get("score", 0.0))
    is_drift = 1 if result.get("verdict") == "drift" else 0
    row = con.execute(
        "SELECT turns, ewma_score, max_score FROM sessions WHERE session_id=?",
        (session_id,),
    ).fetchone()
    if row is None:
        con.execute(
            """INSERT INTO sessions(session_id, first_ts, last_ts, turns,
                   drift_turns, last_score, max_score, ewma_score, profile)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                session_id,
                ts,
                ts,
                1,
                is_drift,
                score,
                score,
                score,
                result.get("profile", "caveman"),
            ),
        )
    else:
        turns, ewma, mx = row
        new_ewma = EWMA_ALPHA * score + (1 - EWMA_ALPHA) * float(ewma)
        con.execute(
            """UPDATE sessions SET last_ts=?, turns=turns+1,
                   drift_turns=drift_turns+?, last_score=?,
                   max_score=MAX(max_score, ?), ewma_score=?, profile=?
               WHERE session_id=?""",
            (
                ts,
                is_drift,
                score,
                score,
                new_ewma,
                result.get("profile", "caveman"),
                session_id,
            ),
        )
    con.commit()
    _ = cur  # silence linters
    return True


def session_status(con: sqlite3.Connection, session_id: str) -> dict:
    row = con.execute(
        """SELECT turns, drift_turns, last_score, max_score, ewma_score, profile,
                  first_ts, last_ts FROM sessions WHERE session_id=?""",
        (session_id,),
    ).fetchone()
    if not row:
        return {
            "session_id": session_id,
            "turns": 0,
            "drift_turns": 0,
            "last_score": 0.0,
            "max_score": 0.0,
            "ewma_score": 0.0,
            "profile": None,
        }
    turns, drift_turns, last_score, max_score, ewma, profile, first_ts, last_ts = row
    return {
        "session_id": session_id,
        "turns": turns,
        "drift_turns": drift_turns,
        "last_score": last_score,
        "max_score": max_score,
        "ewma_score": round(ewma, 2),
        "profile": profile,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "drift_rate": round(drift_turns / turns, 3) if turns else 0.0,
    }


# --------------------------------------------------------------------------- #
# Badge
# --------------------------------------------------------------------------- #
def badge_line(result: dict) -> str:
    """'state|pct' — <=32 bytes. state in {ok,warn,drift}."""
    score = float(result.get("score", 0.0))
    threshold = float(result.get("threshold", 70.0))
    if score >= threshold:
        state = "drift"
    elif score >= threshold * 0.6:
        state = "warn"
    else:
        state = "ok"
    return f"{state}|{int(round(score))}"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="drift-detector scorer + persistence")
    ap.add_argument("--transcript")
    ap.add_argument("--text")
    ap.add_argument("--session", default="unknown")
    ap.add_argument("--db")
    ap.add_argument("--schema")
    ap.add_argument("--profile-json")
    ap.add_argument(
        "--rebuild",
        action="store_true",
        help="rescore every assistant turn in the transcript",
    )
    ap.add_argument(
        "--status", action="store_true", help="print session rollup JSON and exit"
    )
    ap.add_argument("--no-persist", action="store_true")
    ap.add_argument(
        "--calibrate",
        action="store_true",
        help="estimate the verbosity baseline from the first turns "
        "of the transcript and apply it before scoring",
    )
    ap.add_argument(
        "--calibrate-n",
        type=int,
        default=5,
        help="how many early turns to calibrate from (default 5)",
    )
    ap.add_argument(
        "--emit-detail",
        action="store_true",
        help="print a second stdout line of detail JSON "
        "(score/threshold/top_offenders) for the controller",
    )
    args = ap.parse_args(argv)

    # Status query path — no scoring.
    if args.status:
        if not args.db or not os.path.isfile(args.db):
            print(json.dumps({"session_id": args.session, "turns": 0}))
            return 0
        try:
            con = _connect(args.db)
            ensure_schema(con, args.schema)
            print(json.dumps(session_status(con, args.session)))
            con.close()
        except sqlite3.Error as exc:
            print(json.dumps({"error": str(exc), "session_id": args.session}))
        return 0

    profile_dict = load_profile_dict(args.profile_json)

    # Self-calibrating baseline: estimate the session's own verbosity register
    # from its first N in-contract turns and override the profile's length
    # parameters before scoring. Lexical drift markers stay as-is. Best-effort —
    # any failure leaves the profile untouched.
    if (
        args.calibrate
        and drift_calibrate is not None
        and args.transcript
        and os.path.isfile(args.transcript)
    ):
        try:
            cal = drift_calibrate.calibrate_from_transcript(
                args.transcript, n_sample=args.calibrate_n
            )
            if cal:
                profile_dict = drift_calibrate.apply_calibration(
                    profile_dict or {}, cal
                )
        except Exception:  # noqa: BLE001 — calibration must never break scoring.
            pass

    # Gather the text(s) to score.
    if args.text is not None:
        turns = [args.text]
    elif args.transcript and os.path.isfile(args.transcript):
        turns = (
            all_assistant_turns(args.transcript)
            if args.rebuild
            else [last_assistant_turn(args.transcript)]
        )
    elif not sys.stdin.isatty():
        turns = [sys.stdin.read()]
    else:
        turns = [""]

    last_result: Optional[dict] = None
    con = None
    if args.db and not args.no_persist:
        try:
            con = _connect(args.db)
            ensure_schema(con, args.schema)
        except sqlite3.Error:
            con = None  # degrade to score-only

    for text in turns:
        result = drift_score.score_text(text, profile_dict)
        last_result = result
        if con is not None:
            thash = hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[
                :16
            ]
            try:
                persist(con, args.session, result, thash)
            except sqlite3.Error:
                pass  # never break the session over a write failure

    if con is not None:
        con.close()

    if last_result is None:
        last_result = drift_score.score_text("", profile_dict)
    result_out: dict = last_result

    # Badge to stdout (the Stop hook captures this) on line 1.
    print(badge_line(result_out))

    # Optional detail line for the controller: a compact JSON of the raw
    # score/threshold and the top offenders, so the hook can drive the
    # trajectory controller and build proportional correction text.
    if args.emit_detail:
        offenders = result_out.get("top_offenders", []) or []
        print(
            json.dumps(
                {
                    "score": float(result_out.get("score", 0.0)),
                    "threshold": float(result_out.get("threshold", 70.0)),
                    "verdict": result_out.get("verdict", "ok"),
                    "top_offenders": offenders,
                }
            )
        )

    # Rich record to fd 3 if the caller opened it.
    try:
        with os.fdopen(3, "w") as fh3:
            json.dump(result_out, fh3)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — last-resort guard; never break Stop
        # Neutral badge + non-failing exit.
        sys.stderr.write(f"score.py: {exc}\n")
        print("ok|0")
        sys.exit(0)
