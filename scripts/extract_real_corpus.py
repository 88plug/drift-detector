#!/usr/bin/env python3
"""extract_real_corpus.py — build real-session eval corpus from ~/.claude/projects/.

Discovers real Claude Code session transcripts, segments each into "assistant
bursts" (consecutive assistant text turns between two real user prompts), labels
each burst drift|ok|unlabeled by whether the next user prompt is a correction,
and writes eval_real_corpus.json.

This is the data layer for backtest_real.py.

Usage:
  python3 extract_real_corpus.py
  python3 extract_real_corpus.py --max-sessions 20   # smoke run
  python3 extract_real_corpus.py --out /tmp/test_corpus.json --window 1
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_LIB  = os.path.join(_REPO, "src", "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from drift_user_correction import classify_user_reply, INTERRUPT_MARKERS  # noqa: E402


# --------------------------------------------------------------------------- #
# Low-level JSONL parsing (mirrors score.py — no import to avoid coupling)
# --------------------------------------------------------------------------- #

def _text_from_message(msg) -> str:
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


def _is_tool_result(rec: dict) -> bool:
    """True if this user-role record is a tool result, not a human prompt."""
    if "toolUseResult" in rec:
        return True
    msg = rec.get("message", rec)
    content = msg.get("content", "") if isinstance(msg, dict) else ""
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return True
    return False


def _get_role(rec: dict) -> Optional[str]:
    msg = rec.get("message")
    if isinstance(msg, dict):
        r = msg.get("role")
        if r:
            return r
    return rec.get("role") or rec.get("type")


def _get_text(rec: dict) -> str:
    return _text_from_message(rec.get("message", rec))


# --------------------------------------------------------------------------- #
# Session segmenter
# --------------------------------------------------------------------------- #

def segment_session(path: str) -> List[dict]:
    """Parse one .jsonl file into a list of assistant burst dicts.

    Each burst:  {preceding_user, assistant_turns, turn_uuids, following_user,
                  interrupt_preceding}
    following_user is None for the last burst in a session (right-censored).
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw_lines = fh.readlines()
    except OSError:
        return []

    # Parse all records
    records = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue

        role = _get_role(rec)
        if role not in ("user", "assistant"):
            continue

        is_sc   = bool(rec.get("isSidechain"))
        is_meta = bool(rec.get("isMeta"))
        text    = _get_text(rec).strip()
        is_tr   = _is_tool_result(rec) if role == "user" else False

        records.append({
            "role":          role,
            "text":          text,
            "is_sidechain":  is_sc,
            "is_meta":       is_meta,
            "is_tool_result": is_tr,
            "uuid":          rec.get("uuid", ""),
            "ts":            rec.get("timestamp", ""),
        })

    # Segment into bursts using a state machine.
    # Interrupt markers are user-type lines we skip in burst-boundary logic but
    # use to set the interrupt_preceding flag for the NEXT real user prompt.
    bursts: List[dict] = []
    cur_burst:  List[str] = []
    cur_uuids:  List[str] = []
    preceding:  Optional[dict] = None
    pending_interrupt = False

    for r in records:
        if r["is_sidechain"] or r["is_meta"]:
            continue

        if r["role"] == "user":
            if not r["text"]:
                continue
            if r["is_tool_result"]:
                continue

            # Interrupt marker: don't treat as a burst boundary; just flag next.
            if r["text"] in INTERRUPT_MARKERS:
                pending_interrupt = True
                continue

            # System-injected messages: task-notifications, hook injections,
            # slash-command UI records, session-summary context blocks, and
            # tool echo blocks — these appear as user-role records but are not
            # human input.
            _tx = r["text"]
            if _tx.startswith("<task-notification") or _tx.startswith("<task-id"):
                continue
            if "<command-name>" in _tx:
                continue
            if "<local-command-stdout>" in _tx or "<local-command-stderr>" in _tx:
                continue
            if _tx.startswith("This session is being continued from a previous"):
                continue
            # Very long "user" messages are almost always pasted log dumps or
            # injected context, not human instructions.  Heuristic: > 2000 chars.
            if len(_tx) > 2000:
                continue

            # Real user prompt — close the current burst (if any) and start next.
            if cur_burst:
                bursts.append({
                    "preceding_user":    preceding,
                    "assistant_turns":   list(cur_burst),
                    "turn_uuids":        list(cur_uuids),
                    "following_user":    r,
                    "interrupt_preceding": pending_interrupt,
                })
                cur_burst  = []
                cur_uuids  = []
            preceding         = r
            pending_interrupt = False

        elif r["role"] == "assistant":
            if not r["text"]:
                continue
            cur_burst.append(r["text"])
            cur_uuids.append(r["uuid"])

    # Final burst is right-censored (no following user prompt).
    if cur_burst:
        bursts.append({
            "preceding_user":    preceding,
            "assistant_turns":   list(cur_burst),
            "turn_uuids":        list(cur_uuids),
            "following_user":    None,
            "interrupt_preceding": pending_interrupt,
        })

    return bursts


# --------------------------------------------------------------------------- #
# Label assignment
# --------------------------------------------------------------------------- #

def _label_burst(burst: dict, window: int, next_burst: Optional[dict]) -> dict:
    """Return (label, label_subtype, label_hop, confidence)."""
    following = burst["following_user"]
    interrupt  = burst["interrupt_preceding"]

    if following is None:
        return dict(label="unlabeled", label_subtype="censored",
                    label_hop=None, confidence=1.0)

    following_text = following["text"]
    subtype = classify_user_reply(following_text, interrupt_preceding=interrupt)

    _STRONG_APPROVALS = frozenset(["yes", "yeah", "yep", "yup", "lgtm", "perfect",
                                    "great", "good", "nice", "cool", "correct"])

    if subtype in ("correction_style", "correction_substance", "frustration"):
        hop = 1
    elif subtype in ("approval", "continuation", "new_task") and window >= 2 and next_burst:
        # W=2: check one burst further — but ONLY for "new_task" hops.
        # If the user's immediate reaction is an approval ("yes ...", "great", single-word
        # continuation like "go") the burst was accepted; don't propagate blame backwards.
        # Only "new_task" allows W=2 propagation (user gave a new instruction, then the
        # next burst drew a correction — the original burst may have contributed).
        _skip_w2 = subtype in ("approval", "continuation")
        if not _skip_w2:
            next_following = next_burst.get("following_user")
            if next_following:
                st2 = classify_user_reply(next_following["text"],
                                          interrupt_preceding=next_burst.get("interrupt_preceding", False))
                if st2 in ("correction_style", "correction_substance", "frustration"):
                    subtype = st2
                    hop = 2
                else:
                    hop = None
            else:
                hop = None
        else:
            hop = None
    else:
        hop = None

    if subtype in ("correction_style", "correction_substance", "frustration"):
        label = "drift"
    else:
        label = "ok"

    # Confidence: higher for hard signals
    if interrupt or (following_text and following_text.split()[0].lower() in
                     {"no", "nope", "stop", "wait", "undo", "revert", "wrong"}):
        confidence = 1.0
    elif subtype in ("correction_style", "correction_substance"):
        confidence = 0.8
    else:
        confidence = 0.9

    return dict(label=label, label_subtype=subtype, label_hop=hop, confidence=confidence)


# --------------------------------------------------------------------------- #
# Corpus builder
# --------------------------------------------------------------------------- #

def extract_corpus(
    projects_dir: str,
    max_sessions: Optional[int],
    window: int,
    extracted_at: str,
) -> tuple:
    entries = []
    session_count = 0

    # Top-level .jsonl files only (avoids subagent/workflow journals deeper in tree).
    # Sort largest-first: real conversational sessions are 1-3 MB; subagent journals
    # are ~10-100 KB. This ensures real sessions are processed first when max_sessions
    # is used, and avoids wasting quota on tiny non-standard files.
    pattern = os.path.join(projects_dir, "*", "*.jsonl")
    files   = sorted(
        (f for f in glob.glob(pattern) if "/subagents/" not in f),
        key=os.path.getsize, reverse=True,
    )

    for path in files:
        if max_sessions is not None and session_count >= max_sessions:
            break

        bursts = segment_session(path)
        if not bursts:
            continue
        session_count += 1

        base       = os.path.basename(path)
        session_id = base.replace(".jsonl", "")

        for i, burst in enumerate(bursts):
            next_b = bursts[i + 1] if i + 1 < len(bursts) else None
            lb     = _label_burst(burst, window, next_b)

            entries.append({
                "id":                      f"real_{session_id[:8]}_{i}",
                "source_file":             path,
                "session_id":              session_id,
                "cwd":                     None,
                "git_branch":              None,
                "burst_index":             i,
                "assistant_turns":         burst["assistant_turns"],
                "turn_uuids":              burst["turn_uuids"],
                "preceding_user_prompt":   burst["preceding_user"]["text"] if burst["preceding_user"] else None,
                "following_user_prompt":   burst["following_user"]["text"] if burst["following_user"] else None,
                "following_user_uuid":     burst["following_user"]["uuid"] if burst["following_user"] else None,
                "interrupt_preceding":     burst["interrupt_preceding"],
                "label":                   lb["label"],
                "label_subtype":           lb["label_subtype"],
                "label_source":            "lexical",
                "label_hop":               lb["label_hop"],
                "correction_window":       window,
                "expected_should_correct": lb["label"] == "drift",
                "confidence":              lb["confidence"],
                "extracted_at":            extracted_at,
                "engine_version_at_extraction": None,
                # Synthetic-corpus compat fields (null for real sessions)
                "drift_type":              None,
                "targets_blind_spot":      None,
                "expected_trajectory":     None,
            })

    return entries, session_count


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(
        description="Build real-session eval corpus from Claude Code transcripts")
    ap.add_argument("--projects-dir",
                    default=os.path.expanduser("~/.claude/projects"),
                    help="Root of Claude Code projects (default: ~/.claude/projects)")
    ap.add_argument("--out",
                    default=os.path.join(_REPO, "eval_real_corpus.json"),
                    help="Output path (default: <repo>/eval_real_corpus.json)")
    ap.add_argument("--window", type=int, default=2, choices=[1, 2],
                    help="Correction-window size in user prompts (default: 2)")
    ap.add_argument("--max-sessions", type=int, default=None,
                    help="Cap session count — for smoke runs")
    args = ap.parse_args()

    if not os.path.isdir(args.projects_dir):
        print(f"extract_real_corpus: projects dir not found: {args.projects_dir}",
              file=sys.stderr)
        sys.exit(1)

    extracted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries, session_count = extract_corpus(
        args.projects_dir, args.max_sessions, args.window, extracted_at)

    # Sort for determinism
    entries.sort(key=lambda e: (e["session_id"], e["burst_index"]))

    n       = len(entries)
    drift_n = sum(1 for e in entries if e["label"] == "drift")
    ok_n    = sum(1 for e in entries if e["label"] == "ok")
    unlab_n = sum(1 for e in entries if e["label"] == "unlabeled")

    print(
        f"extract_real_corpus: {session_count} sessions → {n} bursts "
        f"(drift={drift_n} ok={ok_n} unlabeled={unlab_n})",
        file=sys.stderr,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)

    print(f"extract_real_corpus: wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
