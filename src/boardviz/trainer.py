"""Trainer position selection and attempt recording.

Positions come from the player's confirmed mistakes (joined to the engine-free
``grades_cache``), so the drill runs with no engine at runtime. Selection modes
support plain review and spaced-repetition-style "repeat my misses".
"""

from __future__ import annotations

import json
import sqlite3
import time

import chess

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
            "opp_seconds": r["opp_seconds"], "solve_depth": r["solve_depth"],
        })
    return out


def select_mate_positions(conn: sqlite3.Connection, *, username: str,
                          deep: bool = False, missed_only: bool = False,
                          n: int = 20) -> list[dict]:
    """Positions where the profile had a forced mate, for the mate drill.

    ``deep=False`` selects mate-in-1 (deliver it); ``deep=True`` mate-in-2+ (find
    the key move). ``missed_only`` keeps only blown chances (converted=0). A fresh
    random sample of up to ``n``. Each position carries the mating ``key_uci`` and
    the forced line (``mate_pv``) so the drill can score and review without an
    engine.
    """
    where = ["m.is_me = 1", "g.username = ?",
             "m.distance >= 2" if deep else "m.distance = 1"]
    params: list = [username]
    if missed_only:
        where.append("m.converted = 0")
    sql = ("SELECT m.fen, m.distance, m.key_uci, m.mate_pv_json, m.motif, "
           "m.converted, m.url, g.tc_class FROM mate_chances m "
           "JOIN games g ON g.id = m.game_id "
           f"WHERE {' AND '.join(where)} ORDER BY RANDOM() LIMIT ?")
    params.append(n)
    out = []
    for r in conn.execute(sql, params).fetchall():
        out.append({
            "mate": True, "fen": r["fen"], "distance": r["distance"],
            "key_uci": r["key_uci"], "motif": r["motif"],
            "mate_pv": json.loads(r["mate_pv_json"]) if r["mate_pv_json"] else [],
            "converted": r["converted"], "url": r["url"],
            "tc_class": r["tc_class"], "opp_move": None,
            "epd": chess.Board(r["fen"]).epd(),
        })
    return out


def select_positions(conn: sqlite3.Connection, n: int = 20,
                     mode: str = "my_mistakes", *, username: str | None = None,
                     tc_class: str | list[str] | None = None,
                     structure: str | list[str] | None = None,
                     move_type: str | list[str] | None = None,
                     phase: str | list[str] | None = None,
                     opening: str | list[str] | None = None,
                     opening_like: str | None = None,
                     max_fullmove: int | None = None,
                     min_solve_depth: int | None = None,
                     repeated_only: bool = False) -> list[dict]:
    """Return up to `n` trainer positions with grades attached.

    ``structure`` / ``move_type`` / ``phase`` are independent, composable
    filters. Opening scope is either ``opening`` (exact name, or any of a list)
    or ``opening_like`` — a space-separated set of words all matched as a
    case-insensitive substring of the opening name, in order (so "french advance"
    matches "French Defense Advance …", catching every variant at once).
    ``max_fullmove`` caps positions to the first N moves (the early opening, where
    the structure/theory lives). ``min_solve_depth`` keeps only harder finds — the
    best move must need at least that search depth to surface (skips the obvious
    recaptures). ``repeated_only`` keeps only positions you blundered 2+ times
    across your games — the exact same mistake, made again.

    Modes control ordering only:
        my_mistakes / (default) — a fresh random sample.
        worst          — biggest eval drops first.
        repeat_failures — positions you drilled and failed, recent first.
    """
    base = (
        "SELECT k.epd, k.fen, k.played_uci, k.structure, k.move_type, k.phase, "
        "k.url, gc.grades_json, gc.best_uci, gc.eval_cp, gc.solve_depth, "
        "g.tc_class, pm.epd_before AS prev_epd, pm.uci AS opp_move, "
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
                     ("k.phase", phase), ("g.opening", opening)):
        frag, ps = db.where_in(col, val)
        if frag:
            base += " AND " + frag
            params.extend(ps)

    if opening_like:  # all words present, in order, anywhere in the opening name
        base += " AND LOWER(g.opening) LIKE ?"
        params.append("%" + "%".join(opening_like.lower().split()) + "%")

    if max_fullmove:  # only early positions — the opening's structure/theory
        base += " AND k.fullmove <= ?"
        params.append(max_fullmove)

    if min_solve_depth:  # only harder finds — the best move needs real calculation
        base += " AND gc.solve_depth >= ?"
        params.append(min_solve_depth)

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
                   time_penalty: int, final_score: float, tc_class: str) -> None:
    """Persist a trainer attempt (used by 'repeat my misses')."""
    db.insert_attempt(
        conn, epd=epd, source=source, played_uci=played_uci, grade=grade,
        time_taken_s=elapsed_s, time_penalty=time_penalty,
        final_score=final_score, tc_class=tc_class, ts=time.time())
