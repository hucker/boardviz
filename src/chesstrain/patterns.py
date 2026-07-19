"""Aggregation over analyzed data: recurring mistakes and the big-think analytic.

Pure reads (pandas over SQLite) — no engine, no re-analysis. Everything here is
powered by the single batch pass that already wrote ``moves`` and ``mistakes``.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from . import db

# Game-level filter columns (scope by account, time control, color, result,
# flag-loss, analysis state). Each value may be a scalar or a list (IN). NB:
# whose *move* a mistake was is tracked by moves/mistakes.is_me, NOT here — a
# game owned by my account contains both my moves and my opponent's.
_GAME_FILTERS = ("username", "tc_class", "my_color", "outcome", "flagged",
                 "analyzed", "eco", "end_state", "end_method")


def _where(game_filter: dict, move_is_me: int | None, move_alias: str,
           game_alias: str = "g") -> tuple[str, list]:
    clauses, params = [], []
    for col in _GAME_FILTERS:
        frag, ps = db.where_in(f"{game_alias}.{col}", game_filter.get(col))
        if frag:
            clauses.append(frag)
            params.extend(ps)
    opening = game_filter.get("opening")
    if opening:  # case-insensitive substring, e.g. 'French'
        clauses.append(f"{game_alias}.opening LIKE ?")
        params.append(f"%{opening}%")
    cframe, cparams = db.clock_where(game_filter.get("clock"), f"{game_alias}.")
    if cframe:  # low-clock-at-end (time scramble) filter
        clauses.append(cframe)
        params.extend(cparams)
    if game_filter.get("time_trouble"):  # losses to the clock (flag or low-clock resign)
        tframe, tparams = db.time_trouble_where(f"{game_alias}.")
        clauses.append(tframe)
        params.extend(tparams)
    min_end = game_filter.get("min_end_time")
    if min_end is not None:  # 'most recent N games' cutoff
        clauses.append(f"{game_alias}.end_time >= ?")
        params.append(min_end)
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
        DataFrame [<by>, count, median_drop, worst_drop, sample_urls] sorted by
        count desc. Severity is the *median* drop (robust) plus the worst single
        drop — the mean is misleading here because a blunder into forced mate is
        clamped near 3000 cp and drags an average far above a typical mistake.
    """
    df = mistakes_df(conn, game_filter, move_is_me=is_me)
    cols = [by, "count", "median_drop", "worst_drop", "sample_urls"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    grouped = df.groupby(by).agg(
        count=("id", "size"),
        median_drop=("drop_cp", "median"),
        worst_drop=("drop_cp", "max"),
        sample_urls=("url", lambda s: list(dict.fromkeys(s.dropna()))[:3]),
    ).reset_index()
    grouped = grouped[grouped["count"] >= min_count]
    grouped["median_drop"] = grouped["median_drop"].round(0).astype(int)
    grouped["worst_drop"] = grouped["worst_drop"].astype(int)
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


def eco_opening_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Map each ECO code to its most common opening name, for display lookups."""
    rows = conn.execute(
        "SELECT eco, opening, COUNT(*) c FROM games "
        "WHERE eco != '' AND opening != '' "
        "GROUP BY eco, opening ORDER BY c DESC").fetchall()
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(r["eco"], r["opening"])  # first = most common per eco
    return out


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
# the same text. Draws collapse to one slice. Resignations split further by
# whether the *resigner* was actually winning (see _resign_bucket) — except a
# resignation that lost the clock race (db.lost_on_clock) is grouped next to the
# actual time-forfeits as 'resign (out of time)', since it was really a time loss.
_TERM_METHODS = ("checkmate", "resign while winning", "resign while losing",
                 "resign (unclear)", "resign (out of time)", "on time",
                 "abandoned", "other")

# A resigner counts as "winning" if the engine had them ahead by at least this
# much at the final position. 0 = literally any advantage; raise it (e.g. to
# config.WIN_THRESHOLD_CP) to only flag clearly-won positions thrown away.
_RESIGN_WINNING_CP = 0


def classify_termination(outcome: str, termination: str) -> tuple[str, str]:
    """Map a game to (outcome, method) for the termination breakdown.

    ``outcome`` is 'win' | 'loss' | 'draw'; ``termination`` is the raw chess.com
    Termination header. Draws return ('draw', 'draw'). Resignations return the
    coarse ('win'|'loss', 'resignation'); termination_breakdown refines those
    into winning/losing once it has the final eval. The method is the same value
    stored on ``games.end_method`` (see ``db.classify_end_method``).
    """
    return (outcome, db.classify_end_method(outcome, termination))


def _resign_bucket(outcome: str, last_eval: int | None,
                   last_is_me: int | None) -> str:
    """Split a resignation by whether the resigner was winning at the end.

    The resigner is the loser of the game. ``last_eval``/``last_is_me`` come from
    the final recorded ply, whose eval is that mover's POV; flip it to the
    resigner's POV. Returns 'resign (unclear)' if the game wasn't analyzed.
    """
    if last_eval is None or last_is_me is None:
        return "resign (unclear)"
    resigner_is_me = 1 if outcome == "loss" else 0
    resigner_eval = last_eval if last_is_me == resigner_is_me else -last_eval
    return ("resign while winning" if resigner_eval > _RESIGN_WINNING_CP
            else "resign while losing")


def termination_breakdown(conn: sqlite3.Connection,
                          game_filter: dict | None = None) -> list[dict]:
    """Per-(outcome, method) game counts for the dashboard termination chart.

    Ordered wins -> draw -> losses. Resignations are split by the resigner's
    final-position eval (winning vs losing), which needs the last analyzed ply,
    so the query LEFT JOINs the max-ply move; unanalyzed games fall to
    'resign (unclear)'. Each record carries a stable ``sort`` index.
    """
    where, params = _where(game_filter or {}, None, "g")
    rows = conn.execute(
        "SELECT g.outcome, g.termination, g.end_clock_me, g.end_clock_opp, "
        "       lm.eval_cp_after AS last_eval, lm.is_me AS last_is_me "
        "FROM games g "
        "LEFT JOIN moves lm ON lm.game_id = g.id AND lm.ply = "
        "    (SELECT MAX(ply) FROM moves WHERE game_id = g.id)"
        + where, params).fetchall()
    counts: dict[tuple[str, str], int] = {}
    for r in rows:
        outcome, method = classify_termination(r["outcome"], r["termination"] or "")
        if method == "resignation":
            # A resignation that lost the clock race is really a time loss, so it
            # groups with the flags rather than splitting by board eval.
            if outcome == "loss" and db.lost_on_clock(
                    "resignation", r["end_clock_me"], r["end_clock_opp"]):
                method = "resign (out of time)"
            else:
                method = _resign_bucket(outcome, r["last_eval"], r["last_is_me"])
        counts[(outcome, method)] = counts.get((outcome, method), 0) + 1

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
