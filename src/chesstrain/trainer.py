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
            # Lead-in: the opponent's move that reached this position, for the
            # trainer's pre-puzzle replay. prev_epd/opp_move/opp_seconds are None
            # when the mistake was the game's first move (no prior ply).
            "prev_epd": r["prev_epd"], "opp_move": r["opp_move"],
            "opp_seconds": r["opp_seconds"],
        })
    return out


def select_positions(conn: sqlite3.Connection, n: int = 20,
                     mode: str = "my_mistakes", *, username: str | None = None,
                     tc_class: str | list[str] | None = None,
                     structure: str | list[str] | None = None,
                     move_type: str | list[str] | None = None,
                     phase: str | list[str] | None = None,
                     repeated_only: bool = False) -> list[dict]:
    """Return up to `n` trainer positions with grades attached.

    ``structure`` / ``move_type`` / ``phase`` are independent, composable
    filters on the mistake's pattern (a recurring cluster is just some
    combination of these). ``repeated_only`` keeps only positions you blundered
    2+ times across your games — the exact same mistake, made again.

    Modes control ordering only:
        my_mistakes / (default) — a fresh random sample.
        worst          — biggest eval drops first.
        repeat_failures — positions you drilled and failed, recent first.
    """
    base = (
        "SELECT k.epd, k.fen, k.played_uci, k.structure, k.move_type, k.phase, "
        "k.url, gc.grades_json, gc.best_uci, gc.eval_cp, g.tc_class, "
        "pm.epd_before AS prev_epd, pm.uci AS opp_move, "
        "pm.seconds_spent AS opp_seconds "
        "FROM mistakes k "
        "JOIN grades_cache gc ON gc.epd = k.epd "
        "JOIN games g ON g.id = k.game_id "
        "LEFT JOIN moves pm ON pm.game_id = k.game_id AND pm.ply = k.ply - 1 "
        "WHERE k.is_me = 1"
    )
    params: list = []
    # Each filter accepts a scalar or a list (multi-select) via db.where_in.
    for col, val in (("g.username", username), ("g.tc_class", tc_class),
                     ("k.structure", structure), ("k.move_type", move_type),
                     ("k.phase", phase)):
        frag, ps = db.where_in(col, val)
        if frag:
            base += " AND " + frag
            params.extend(ps)

    if repeated_only:  # positions blundered 2+ times across your games
        base += (" AND k.epd IN (SELECT epd FROM mistakes "
                 "WHERE is_me = 1 GROUP BY epd HAVING COUNT(*) >= 2)")

    if mode == "repeat_failures":  # only positions attempted and failed
        base += " AND k.epd IN (SELECT epd FROM attempts WHERE grade < 1)"

    # GROUP BY epd = one puzzle per position (a position blundered in several
    # games has one mistakes row per game); ORDER + LIMIT pick and cap in SQL.
    base += " GROUP BY k.epd"
    base += {
        "repeat_failures": " ORDER BY (SELECT MAX(created_ts) FROM attempts a "
                           "WHERE a.epd = k.epd) DESC",  # recent misses first
        "worst": " ORDER BY MAX(k.drop_cp) DESC",        # biggest blunders first
    }.get(mode, " ORDER BY RANDOM()")                    # else a fresh shuffle
    base += " LIMIT ?"
    params.append(n)
    return _rows_to_positions(conn.execute(base, params).fetchall())


def record_attempt(conn: sqlite3.Connection, *, epd: str, source: str,
                   played_uci: str, grade: int, elapsed_s: float,
                   time_penalty: int, final_score: int, tc_class: str) -> None:
    """Persist a trainer attempt (used by 'repeat my misses')."""
    db.insert_attempt(
        conn, epd=epd, source=source, played_uci=played_uci, grade=grade,
        time_taken_s=elapsed_s, time_penalty=time_penalty,
        final_score=final_score, tc_class=tc_class, ts=time.time())
