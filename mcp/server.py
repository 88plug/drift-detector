#!/usr/bin/env python3
"""server.py — zero-dependency MCP stdio server for drift-detector.

Speaks the Model Context Protocol over stdin/stdout (newline-delimited JSON-RPC
2.0). Pure stdlib — no `mcp` package required, importable under any Python 3.8+.

The drift SQLite index is opened READ-ONLY (mode=ro) for queries so the server
can never corrupt the Stop-hook writer's WAL. The single mutating tool
(drift_set_profile) writes only the small `active-profile` text file, never the
DB. Every tool degrades gracefully when the DB / profiles dir does not exist
yet, returning empty-but-valid results rather than erroring.

Tools exposed:
  drift_status         — rollup for the current/most-recent session
  drift_recent         — the last N scored turns (ts, score, verdict, profile)
  drift_explain        — score arbitrary text against a profile (live, no DB)
  drift_list_profiles  — enumerate available profiles + which is active
  drift_set_profile    — set the active profile (writes active-profile file)

Paths:
  CLAUDE_CONFIG_DIR  — user config root (default ~/.claude)
  CLAUDE_PLUGIN_DATA — writable data root; else
                       <config>/plugins/data/drift-detector-88plug
  DD_DB              — explicit DB path override (set by mcp-server.sh)
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "drift-detector"
SERVER_VERSION = "1.0.0"
PLUGIN_SLUG = "drift-detector-88plug"

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# The drift_score engine lives in the plugin's src/lib. mcp-server.sh adds it to
# PYTHONPATH; we also append a best-effort relative fallback for direct runs.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.abspath(os.path.join(_HERE, "..", "src", "lib"))
if os.path.isdir(_ENGINE_DIR) and _ENGINE_DIR not in sys.path:
    sys.path.append(_ENGINE_DIR)

try:
    import drift_score  # type: ignore
except Exception:  # noqa: BLE001 — engine optional; drift_explain degrades
    drift_score = None  # type: ignore


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #
def config_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def data_root() -> str:
    explicit = os.environ.get("CLAUDE_PLUGIN_DATA")
    if explicit:
        return explicit
    return os.path.join(config_dir(), "plugins", "data", PLUGIN_SLUG)


def db_path() -> str:
    p = os.environ.get("DD_DB")
    if p:
        return p
    return os.path.join(data_root(), "drift.db")


def profiles_dir() -> str:
    return os.path.join(data_root(), "profiles")


def bundled_profiles_dir() -> str:
    return os.path.abspath(os.path.join(_HERE, "..", "profiles"))


def active_profile_file() -> str:
    return os.path.join(data_root(), "active-profile")


def active_profile_name() -> str:
    try:
        with open(active_profile_file(), "r", encoding="utf-8") as fh:
            name = fh.read(64).strip().replace("/", "").replace("\n", "")
        if name:
            return name
    except OSError:
        pass
    return "caveman"


def open_ro() -> Optional[sqlite3.Connection]:
    path = db_path()
    if not os.path.isfile(path):
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=2000")
        return con
    except sqlite3.Error:
        return None


# --------------------------------------------------------------------------- #
# Profile loading (user dir shadows bundled; resolves `extends` shallowly)
# --------------------------------------------------------------------------- #
def _read_profile_json(name: str) -> Optional[dict]:
    for d in (profiles_dir(), bundled_profiles_dir()):
        p = os.path.join(d, f"{name}.json")
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    obj = json.load(fh)
                return obj if isinstance(obj, dict) else None
            except (OSError, json.JSONDecodeError):
                return None
    return None


def resolve_profile(name: str, _seen: Optional[set] = None) -> Optional[dict]:
    """Load a profile dict, deep-merging any `extends` parent (cycle-safe)."""
    _seen = _seen or set()
    if name in _seen:
        return None
    _seen.add(name)
    raw = _read_profile_json(name)
    if raw is None:
        return None
    parent_name = raw.get("extends")
    if parent_name:
        parent = resolve_profile(parent_name, _seen)
        if parent:
            merged = dict(parent)
            for k, v in raw.items():
                if k == "extends":
                    continue
                if isinstance(v, dict) and isinstance(merged.get(k), dict):
                    nested = dict(merged[k])
                    nested.update(v)
                    merged[k] = nested
                else:
                    merged[k] = v
            raw = merged
    raw.setdefault("name", name)
    return raw


def discover_profiles() -> List[Dict[str, Any]]:
    """List profiles across user + bundled dirs, deduped (user shadows bundled)."""
    found: Dict[str, Dict[str, Any]] = {}
    for d in (bundled_profiles_dir(), profiles_dir()):  # user (second) wins
        if not os.path.isdir(d):
            continue
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in names:
            if not fn.endswith(".json"):
                continue
            name = fn[:-5]
            try:
                with open(os.path.join(d, fn), "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, json.JSONDecodeError):
                meta = {}
            found[name] = {
                "name": name,
                "description": meta.get("description", ""),
                "threshold": meta.get("threshold", 70),
            }
    return [found[k] for k in sorted(found)]


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
def _latest_session(con: sqlite3.Connection, session_id: Optional[str]) -> Optional[str]:
    if session_id:
        return session_id
    row = con.execute(
        "SELECT session_id FROM sessions ORDER BY last_ts DESC LIMIT 1"
    ).fetchone()
    return row["session_id"] if row else None


def tool_drift_status(args: Dict[str, Any]) -> Dict[str, Any]:
    empty = {
        "available": False, "score": 0.0, "verdict": "ok", "turns": 0,
        "drift_rate": 0.0, "ewma_score": 0.0, "profile": active_profile_name(),
    }
    con = open_ro()
    if con is None:
        return {**empty, "reason": "no drift data yet"}
    try:
        sid = _latest_session(con, args.get("session_id"))
        if not sid:
            return {**empty, "reason": "no sessions recorded"}
        row = con.execute(
            """SELECT session_id, turns, drift_turns, last_score, max_score,
                      ewma_score, profile, first_ts, last_ts
               FROM sessions WHERE session_id=?""",
            (sid,),
        ).fetchone()
        if not row:
            return {**empty, "reason": "session not found"}
        turns = row["turns"] or 0
        score = float(row["last_score"] or 0.0)
        ewma = float(row["ewma_score"] or 0.0)
        verdict = "drift" if ewma >= 50 else "ok"
        return {
            "available": True,
            "session_id": row["session_id"],
            "score": round(score, 2),
            "verdict": verdict,
            "turns": turns,
            "drift_turns": row["drift_turns"] or 0,
            "drift_rate": round((row["drift_turns"] or 0) / turns, 3) if turns else 0.0,
            "ewma_score": round(ewma, 2),
            "max_score": round(float(row["max_score"] or 0.0), 2),
            "profile": row["profile"] or active_profile_name(),
            "first_ts": row["first_ts"],
            "last_ts": row["last_ts"],
        }
    finally:
        con.close()


def tool_drift_recent(args: Dict[str, Any]) -> Dict[str, Any]:
    con = open_ro()
    if con is None:
        return {"available": False, "events": []}
    try:
        sid = _latest_session(con, args.get("session_id"))
        try:
            limit = int(args.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(50, limit))
        if not sid:
            return {"available": False, "events": []}
        rows = con.execute(
            """SELECT ts, score, verdict, profile
               FROM scores WHERE session_id=? ORDER BY id DESC LIMIT ?""",
            (sid, limit),
        ).fetchall()
        events = [
            {"ts": r["ts"], "score": round(float(r["score"] or 0.0), 2),
             "verdict": r["verdict"], "profile": r["profile"]}
            for r in rows
        ]
        return {"available": True, "session_id": sid, "events": events}
    finally:
        con.close()


def tool_drift_explain(args: Dict[str, Any]) -> Dict[str, Any]:
    text = args.get("text")
    if not isinstance(text, str):
        raise ValueError("'text' (string) is required")
    if drift_score is None:
        return {"available": False, "reason": "scoring engine not importable"}
    name = args.get("profile") or active_profile_name()
    profile_dict = resolve_profile(name)
    result = drift_score.score_text(text, profile_dict)
    return {
        "available": True,
        "profile": result.get("profile", name),
        "score": result.get("score", 0.0),
        "verdict": result.get("verdict", "ok"),
        "threshold": result.get("threshold", 70.0),
        "top_offenders": result.get("top_offenders", []),
        "components": result.get("components", {}),
    }


def tool_drift_list_profiles(args: Dict[str, Any]) -> Dict[str, Any]:
    active = active_profile_name()
    profiles = [
        {**p, "active": p["name"] == active} for p in discover_profiles()
    ]
    return {"active_profile": active, "profiles": profiles}


def tool_drift_set_profile(args: Dict[str, Any]) -> Dict[str, Any]:
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("'name' (string) is required")
    name = name.strip()
    # Reject path traversal / separators; profile names are flat slugs.
    if "/" in name or "\\" in name or name in (".", ".."):
        return {"success": False, "reason": "invalid profile name",
                "active_profile": active_profile_name()}
    if resolve_profile(name) is None:
        return {"success": False, "reason": f"unknown profile: {name}",
                "active_profile": active_profile_name()}
    target = active_profile_file()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(name + "\n")
        os.replace(tmp, target)
    except OSError as exc:
        return {"success": False, "reason": f"write failed: {exc}",
                "active_profile": active_profile_name()}
    return {"success": True, "active_profile": name}


TOOLS = {
    "drift_status": {
        "fn": tool_drift_status,
        "title": "Drift Status",
        "annotations": {"readOnlyHint": True},
        "description": "Current drift rollup for a session: latest score, verdict, "
                       "turn count, drift rate, smoothed EWMA score, and active "
                       "profile. Consult when the user asks about drift or you "
                       "suspect you've drifted from their instructions.",
        "schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string",
                               "description": "Defaults to the most recent session."},
            },
        },
    },
    "drift_recent": {
        "fn": tool_drift_recent,
        "title": "Drift Recent",
        "annotations": {"readOnlyHint": True},
        "description": "The most recent scored assistant turns (newest first), each "
                       "with timestamp, score, verdict, and profile.",
        "schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50,
                          "description": "How many turns to return (default 10)."},
            },
        },
    },
    "drift_explain": {
        "fn": tool_drift_explain,
        "title": "Drift Explain",
        "annotations": {"readOnlyHint": True},
        "description": "Score arbitrary text against a profile right now and return "
                       "the score, verdict, the top drift offenders, and the raw "
                       "per-component breakdown. Useful to check a draft reply "
                       "before sending it.",
        "schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to score."},
                "profile": {"type": "string",
                            "description": "Profile name; defaults to the active one."},
            },
            "required": ["text"],
        },
    },
    "drift_list_profiles": {
        "fn": tool_drift_list_profiles,
        "title": "Drift List Profiles",
        "annotations": {"readOnlyHint": True},
        "description": "List available drift profiles (name, description, threshold) "
                       "and flag which one is currently active.",
        "schema": {"type": "object", "properties": {}},
    },
    "drift_set_profile": {
        "fn": tool_drift_set_profile,
        "title": "Drift Set Profile",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
        "description": "Set the active drift profile by name. Persists to the "
                       "active-profile file so subsequent turns are scored with it.",
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Profile name to activate."},
            },
            "required": ["name"],
        },
    },
}


# --------------------------------------------------------------------------- #
# JSON-RPC plumbing
# --------------------------------------------------------------------------- #
def _result(rid, result) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code, message) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0":
        return _error(req.get("id") if isinstance(req, dict) else None,
                      INVALID_REQUEST, "invalid JSON-RPC 2.0 request")

    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return _result(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "notifications/initialized":
        return None  # notification, no response

    if method == "ping":
        return _result(rid, {})

    if method == "tools/list":
        return _result(rid, {
            "tools": [
                {"name": name, "title": spec["title"], "description": spec["description"],
                 "inputSchema": spec["schema"], "annotations": spec["annotations"]}
                for name, spec in TOOLS.items()
            ]
        })

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        spec = TOOLS.get(name)
        if not spec:
            return _result(rid, {"content": [{"type": "text", "text": f"unknown tool: {name}"}], "isError": True})
        try:
            payload = spec["fn"](args)
            is_error = False
        except ValueError as exc:
            payload = {"error": str(exc)}
            is_error = True
        except Exception as exc:  # noqa: BLE001 — never crash the server
            payload = {"error": str(exc)}
            is_error = True
        return _result(rid, {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
            "isError": is_error,
        })

    if rid is None:
        return None  # unknown notification — silently ignore
    return _error(rid, METHOD_NOT_FOUND, f"method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps(_error(None, PARSE_ERROR, "parse error")) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BrokenPipeError, KeyboardInterrupt):
        sys.exit(0)
