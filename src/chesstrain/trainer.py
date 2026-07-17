"""Trainer position selection and attempt recording.

Positions come from the player's confirmed mistakes (joined to the engine-free
``grades_cache``), so the drill runs with no engine at runtime. Selection modes
support plain review and spaced-repetition-style "repeat my misses".
"""

from __future__ import annotations

import json
import sqlite3
import time

from . import db


def _rows_to_positions(rows: list[sqlite3.Row]) -> list[dict]:
    out = []
    for r in rows:
        grades = json.loads(r["grades_json"]) if r["grades_json"] else {}
        out.append({
            "epd": r["epd"], "fen": r["fen"], "grades": grades,
            "best_uci": r["best_uci"], "eval_cp": r["eval_cp"],
            "structure": r["structure"], "move_type": r["move_type"],
            "phase": r["phase"], "played_uci": r["played_uci"],
            "url": r["url"], "tc_class": r["tc_class"],
        })
    return out


def select_positions(conn: sqlite3.Connection, n: int = 20,
                     mode: str = "my_mistakes", *, username: str | None = None,
                     tc_class: str | None = None,
                     structure: str | None = None) -> list[dict]:
    """Return up to `n` trainer positions with grades attached.

    Modes:
        my_mistakes    — the player's mistakes that have a cached grade.
        repeat_failures — positions previously drilled and failed (grade < 1),
                          weighted toward recent, frequent misses.
        by_structure   — my_mistakes filtered to one pawn structure.
        random         — any graded mistake position, arbitrary order.
    """
    base = (
        "SELECT k.epd, k.fen, k.played_uci, k.structure, k.move_type, k.phase, "
        "k.url, gc.grades_json, gc.best_uci, gc.eval_cp, g.tc_class "
        "FROM mistakes k "
        "JOIN grades_cache gc ON gc.epd = k.epd "
        "JOIN games g ON g.id = k.game_id "
        "WHERE k.is_me = 1"
    )
    params: list = []
    if username:
        base += " AND g.username = ?"
        params.append(username)
    if tc_class:
        base += " AND g.tc_class = ?"
        params.append(tc_class)

    if mode == "by_structure" and structure:
        base += " AND k.structure = ?"
        params.append(structure)

    if mode == "repeat_failures":
        # Only positions the player has attempted and failed most/recently.
        base += (
            " AND k.epd IN (SELECT epd FROM attempts WHERE grade < 1) "
            "ORDER BY (SELECT MAX(created_ts) FROM attempts a WHERE a.epd=k.epd) DESC"
        )
    elif mode == "random":
        base += " ORDER BY k.drop_cp DESC"  # deterministic; UI shuffles if wanted
    else:
        base += " ORDER BY k.drop_cp DESC"

    base += " LIMIT ?"
    params.append(n)
    rows = conn.execute(base, params).fetchall()
    return _rows_to_positions(rows)


def record_attempt(conn: sqlite3.Connection, *, epd: str, source: str,
                   played_uci: str, grade: int, elapsed_s: float,
                   time_penalty: int, final_score: int, tc_class: str) -> None:
    """Persist a trainer attempt (used by 'repeat my misses')."""
    db.insert_attempt(
        conn, epd=epd, source=source, played_uci=played_uci, grade=grade,
        time_taken_s=elapsed_s, time_penalty=time_penalty,
        final_score=final_score, tc_class=tc_class, ts=time.time())
