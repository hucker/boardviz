"""Trainer position selection and attempt recording.

Positions come from the player's confirmed mistakes (joined to the engine-free
``grades_cache``), so the drill runs with no engine at runtime. Selection modes
support plain review and spaced-repetition-style "repeat my misses".
"""

from __future__ import annotations

import json
import random
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
                     tc_class: str | None = None, structure: str | None = None,
                     move_type: str | None = None, phase: str | None = None,
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
    if username:
        base += " AND g.username = ?"
        params.append(username)
    if tc_class:
        base += " AND g.tc_class = ?"
        params.append(tc_class)

    if structure:
        base += " AND k.structure = ?"
        params.append(structure)

    if move_type:
        base += " AND k.move_type = ?"
        params.append(move_type)

    if phase:
        base += " AND k.phase = ?"
        params.append(phase)

    if repeated_only:  # positions blundered 2+ times across your games
        base += (" AND k.epd IN (SELECT epd FROM mistakes "
                 "WHERE is_me = 1 GROUP BY epd HAVING COUNT(*) >= 2)")

    if mode == "repeat_failures":
        # Only positions the player has attempted and failed most/recently.
        base += (
            " AND k.epd IN (SELECT epd FROM attempts WHERE grade < 1) "
            "ORDER BY (SELECT MAX(created_ts) FROM attempts a WHERE a.epd=k.epd) "
            "DESC LIMIT ?"
        )
        params.append(n)
        rows = conn.execute(base, params).fetchall()
    elif mode == "worst":
        base += " ORDER BY k.drop_cp DESC LIMIT ?"  # your biggest blunders first
        params.append(n)
        rows = conn.execute(base, params).fetchall()
    else:
        # Random modes: pull the whole eligible pool and sample n in Python with
        # a clock-seeded RNG. This guarantees a genuinely fresh draw each drill,
        # independent of SQLite's RNG state, so you don't keep seeing the same
        # openers.
        pool = conn.execute(base, params).fetchall()
        rng = random.Random(time.time_ns())
        rows = rng.sample(pool, min(n, len(pool)))
    return _rows_to_positions(rows)


def record_attempt(conn: sqlite3.Connection, *, epd: str, source: str,
                   played_uci: str, grade: int, elapsed_s: float,
                   time_penalty: int, final_score: int, tc_class: str) -> None:
    """Persist a trainer attempt (used by 'repeat my misses')."""
    db.insert_attempt(
        conn, epd=epd, source=source, played_uci=played_uci, grade=grade,
        time_taken_s=elapsed_s, time_penalty=time_penalty,
        final_score=final_score, tc_class=tc_class, ts=time.time())
