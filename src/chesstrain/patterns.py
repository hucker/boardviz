"""Aggregation over analyzed data: recurring mistakes and the big-think analytic.

Pure reads (pandas over SQLite) — no engine, no re-analysis. Everything here is
powered by the single batch pass that already wrote ``moves`` and ``mistakes``.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

# Game-level filter columns (scope by account, time control, color, result).
# NB: whose *move* a mistake was is tracked by moves/mistakes.is_me, NOT here —
# a game owned by my account contains both my moves and my opponent's.
_GAME_FILTERS = ("username", "tc_class", "my_color", "outcome")


def _where(game_filter: dict, move_is_me: int | None, move_alias: str,
           game_alias: str = "g") -> tuple[str, list]:
    clauses, params = [], []
    for col in _GAME_FILTERS:
        val = game_filter.get(col)
        if val is not None:
            clauses.append(f"{game_alias}.{col}=?")
            params.append(val)
    opening = game_filter.get("opening")
    if opening:  # case-insensitive substring, e.g. 'French'
        clauses.append(f"{game_alias}.opening LIKE ?")
        params.append(f"%{opening}%")
    if move_is_me is not None:
        clauses.append(f"{move_alias}.is_me=?")
        params.append(move_is_me)
    sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return sql, params


def moves_df(conn: sqlite3.Connection, game_filter: dict | None = None,
             move_is_me: int | None = None) -> pd.DataFrame:
    """Per-move rows joined to their game (for the analytic and drill-down).

    ``move_is_me`` filters on the mover: 1 = the tracked player, 0 = opponent.
    """
    where, params = _where(game_filter or {}, move_is_me, "m")
    sql = (
        "SELECT m.*, g.tc_class, g.my_color AS game_color, g.outcome, g.url "
        "FROM moves m JOIN games g ON m.game_id=g.id" + where
    )
    return pd.read_sql_query(sql, conn, params=params)


def mistakes_df(conn: sqlite3.Connection, game_filter: dict | None = None,
                move_is_me: int | None = None) -> pd.DataFrame:
    """Confirmed mistakes joined to their game (``move_is_me`` = the mover)."""
    where, params = _where(game_filter or {}, move_is_me, "k")
    sql = (
        "SELECT k.*, g.tc_class, g.outcome FROM mistakes k "
        "JOIN games g ON k.game_id=g.id" + where
    )
    return pd.read_sql_query(sql, conn, params=params)


def consistent_mistakes(conn: sqlite3.Connection, by: str = "structure",
                        game_filter: dict | None = None, is_me: int = 1,
                        min_count: int = 1) -> pd.DataFrame:
    """Cluster confirmed mistakes by a dimension to surface recurring themes.

    Args:
        by: grouping column — 'structure' | 'move_type' | 'phase' | 'eco'.
        is_me: 1 for the player's mistakes, 0 for an opponent's.

    Returns:
        DataFrame [<by>, count, avg_drop, sample_urls] sorted by count desc.
    """
    df = mistakes_df(conn, game_filter, move_is_me=is_me)
    if df.empty:
        return pd.DataFrame(columns=[by, "count", "avg_drop", "sample_urls"])
    grouped = df.groupby(by).agg(
        count=("id", "size"),
        avg_drop=("drop_cp", "mean"),
        sample_urls=("url", lambda s: list(dict.fromkeys(s.dropna()))[:3]),
    ).reset_index()
    grouped = grouped[grouped["count"] >= min_count]
    grouped["avg_drop"] = grouped["avg_drop"].round(0).astype(int)
    return grouped.sort_values("count", ascending=False).reset_index(drop=True)


def bigthink_vs_state(conn: sqlite3.Connection, game_filter: dict | None = None,
                      is_me: int = 1) -> pd.DataFrame:
    """Headline analytic: do long thinks cause more mistakes, and when?

    Splits the player's moves by ``is_long_think`` x ``game_state`` and reports
    the mistake rate (fraction joined to a confirmed mistake) and mean eval drop.
    Directly tests "big thinks blunder more, especially when winning."

    Returns:
        Tidy DataFrame [game_state, think, n_moves, n_mistakes, mistake_rate,
        avg_drop] with ``think`` in {"normal", "long think"}.
    """
    moves = moves_df(conn, game_filter, move_is_me=is_me)
    if moves.empty:
        return pd.DataFrame(columns=[
            "game_state", "think", "n_moves", "n_mistakes", "mistake_rate",
            "avg_drop"])

    # Mark moves that correspond to a confirmed mistake (join on game_id, ply).
    mk = pd.read_sql_query(
        "SELECT game_id, ply FROM mistakes WHERE is_me=?", conn, params=[is_me])
    keys = set(zip(mk["game_id"], mk["ply"])) if not mk.empty else set()
    moves["is_mistake"] = [
        (gid, ply) in keys for gid, ply in zip(moves["game_id"], moves["ply"])]
    moves["think"] = moves["is_long_think"].map({0: "normal", 1: "long think"})

    grp = moves.groupby(["game_state", "think"]).agg(
        n_moves=("ply", "size"),
        n_mistakes=("is_mistake", "sum"),
        avg_drop=("drop_cp", "mean"),
    ).reset_index()
    grp["mistake_rate"] = (grp["n_mistakes"] / grp["n_moves"]).round(3)
    grp["avg_drop"] = grp["avg_drop"].round(0).astype(int)
    return grp


def summary_counts(conn: sqlite3.Connection, game_filter: dict | None = None
                   ) -> dict:
    """Headline numbers for the dashboard top row."""
    where, params = _where(game_filter or {}, None, "g")
    row = conn.execute(
        "SELECT COUNT(*) n, "
        "SUM(outcome='win') wins, SUM(outcome='loss') losses, "
        "SUM(outcome='draw') draws, SUM(flagged) flagged "
        "FROM games g" + where, params).fetchone()
    return {"games": row[0] or 0, "wins": row[1] or 0, "losses": row[2] or 0,
            "draws": row[3] or 0, "flag_losses": row[4] or 0}


# How a game ended, most-decisive first. chess.com's Termination header names
# the winner's method ("X won on time" / "won by resignation" / "won by
# checkmate"), which reads the same from either side, so a loss classifies off
# the same text. Draws collapse to one slice.
_TERM_METHODS = ("checkmate", "resignation", "on time", "abandoned", "other")


def classify_termination(outcome: str, termination: str) -> tuple[str, str]:
    """Map a game to (outcome, method) for the termination breakdown.

    ``outcome`` is 'win' | 'loss' | 'draw'; ``termination`` is the raw chess.com
    Termination header. Draws return ('draw', 'draw').
    """
    if outcome == "draw":
        return ("draw", "draw")
    t = termination.lower()
    if "on time" in t:
        method = "on time"
    elif "resign" in t:
        method = "resignation"
    elif "checkmate" in t or "checkmated" in t:
        method = "checkmate"
    elif "abandon" in t:
        method = "abandoned"
    else:
        method = "other"
    return (outcome, method)


def termination_breakdown(conn: sqlite3.Connection,
                          game_filter: dict | None = None) -> list[dict]:
    """Per-(outcome, method) game counts for the dashboard termination pie.

    Ordered wins -> draw -> losses so a win/draw/loss color scale never places a
    green slice next to a red one. Each record carries a stable ``sort`` index so
    the chart's arcs and labels stack in the same order.
    """
    where, params = _where(game_filter or {}, None, "g")
    rows = conn.execute(
        "SELECT outcome, termination FROM games g" + where, params).fetchall()
    counts: dict[tuple[str, str], int] = {}
    for r in rows:
        key = classify_termination(r["outcome"], r["termination"] or "")
        counts[key] = counts.get(key, 0) + 1

    outcome_rank = {"win": 0, "draw": 1, "loss": 2}
    method_rank = {m: i for i, m in enumerate(_TERM_METHODS)}
    ordered = sorted(
        counts.items(),
        key=lambda kv: (outcome_rank[kv[0][0]], method_rank.get(kv[0][1], 99)))

    out = []
    for i, ((outcome, method), n) in enumerate(ordered):
        label = "Draw" if outcome == "draw" else f"{outcome.capitalize()} · {method}"
        out.append({"sort": i, "outcome": outcome, "method": method,
                    "category": label, "count": n})
    return out
