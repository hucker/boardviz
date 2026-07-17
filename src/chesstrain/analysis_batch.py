"""The single per-move analysis pass — the crux of the pipeline.

One mainline walk per game subsumes clock analysis, big-think flagging, mistake
detection, and per-move eval. It emits a ``moves`` row for *every* ply (both
colors, so opponent grading is free), a ``mistakes`` row for each confirmed
error, and precomputes ``grades_cache`` for the player's own mistake positions
so the trainer needs no engine at runtime.

Key efficiency trick: eval is computed **once per position**, not twice per move.
For a move from P_k to P_{k+1}, the eval loss to the mover is
``e(P_k) + e(P_{k+1})`` where ``e(P)`` is the mover-POV score at P (the sign flip
across the turn boundary makes the two terms add). This halves engine calls
versus a naive before/after scan.

Runs only inside the CLI subprocess (see ``cli.py``); never on a Streamlit rerun.
"""

from __future__ import annotations

import datetime as dt
import io
import time
from dataclasses import dataclass

import chess
import chess.engine
import chess.pgn

from . import config, db
from .blitz_analysis import (
    DEAD_LOST_CP,
    MISTAKE_CP,
    OPENING_MISTAKE_CP,
    OPENING_MOVES,
    GameRecord,
    classify_structure,
    confirm_mistake,
    grade_all_moves,
)

SCAN_DEPTH = 8
VERIFY_DEPTH = 14
GRADE_DEPTH = 12


# --- small classifiers -----------------------------------------------------
def position_key(board: chess.Board) -> str:
    """Cache/join key for a position: EPD (placement+turn+castling+ep)."""
    return board.epd()


def classify_move_type(board: chess.Board, move: chess.Move) -> str:
    """Coarse move type: capture > check > retreat > quiet (priority order)."""
    if board.is_capture(move):
        return "capture"
    if board.gives_check(move):
        return "check"
    piece = board.piece_at(move.from_square)
    if piece is not None and piece.piece_type != chess.PAWN:
        fr = chess.square_rank(move.from_square)
        tr = chess.square_rank(move.to_square)
        backward = tr < fr if board.turn == chess.WHITE else tr > fr
        if backward:
            return "retreat"
    return "quiet"


def phase_of(board: chess.Board, fullmove: int) -> str:
    """Opening (early), endgame (few pieces), else middlegame."""
    if fullmove <= OPENING_MOVES:
        return "opening"
    if chess.popcount(board.occupied) <= 12:
        return "endgame"
    return "middlegame"


def game_state(eval_cp: int, threshold: int = config.WIN_THRESHOLD_CP) -> str:
    """Mover-POV game state from an eval: winning / equal / losing."""
    if eval_cp >= threshold:
        return "winning"
    if eval_cp <= -threshold:
        return "losing"
    return "equal"


# Plain-English definitions for the classifier vocabularies, surfaced in the
# Review UI so the cluster tables aren't a guessing game. Kept next to the
# classifiers so the words and their meanings can't drift apart.
MOVE_TYPE_DEFS = {
    "capture": "The move takes a piece or pawn.",
    "check": "The move gives check (and isn't also a capture).",
    "retreat": "A piece — not a pawn — moving backward toward your own side.",
    "quiet": "A normal move: no capture, check, or retreat.",
}
PHASE_DEFS = {
    "opening": f"The first {OPENING_MOVES} full moves.",
    "middlegame": "Past the opening, with more than 12 pieces on the board.",
    "endgame": "12 or fewer pieces left on the board.",
}
_WIN_PAWNS = config.WIN_THRESHOLD_CP / 100
GAME_STATE_DEFS = {
    "winning": f"Engine has the side to move ahead by ≥ {_WIN_PAWNS:.0f} pawns "
               f"(+{config.WIN_THRESHOLD_CP} cp).",
    "equal": f"Within ±{_WIN_PAWNS:.0f} pawns.",
    "losing": f"Behind by ≥ {_WIN_PAWNS:.0f} pawns.",
}


# --- clock parsing ---------------------------------------------------------
def _increment(time_control: str) -> float:
    return float(time_control.split("+", 1)[1]) if "+" in time_control else 0.0


# --- the pass --------------------------------------------------------------
@dataclass
class MoveEval:
    """Everything recorded for one ply (mirrors the ``moves`` table)."""

    ply: int
    fullmove: int
    color: str
    is_me: int
    uci: str
    san: str
    epd_before: str
    fen_before: str
    eval_cp_before: int
    eval_cp_after: int
    drop_cp: int
    best_uci: str
    phase: str
    structure: str
    move_type: str
    seconds_spent: float | None
    seconds_remaining: float | None
    is_long_think: int
    game_state: str


def record_from_row(row) -> GameRecord | None:
    """Reconstruct a GameRecord from a stored ``games`` row (has pgn).

    Returns None if the stored PGN won't parse — the caller skips the game
    rather than aborting the whole batch.
    """
    game = chess.pgn.read_game(io.StringIO(row["pgn"]))
    if game is None:
        return None
    my_color = chess.WHITE if row["my_color"] == "white" else chess.BLACK
    return GameRecord(
        game=game, url=row["url"], my_color=my_color, outcome=row["outcome"],
        termination=row["termination"], time_control=row["time_control"],
        end_time=dt.datetime.fromtimestamp(row["end_time"] or 0),
        flagged=bool(row["flagged"]), uuid=row["game_uuid"] or "",
        pgn=row["pgn"] or "",
    )


def scan_game(rec: GameRecord, engine: chess.engine.SimpleEngine,
              scan_depth: int = SCAN_DEPTH) -> list[MoveEval]:
    """Walk the game once, evaluating each position, and emit a MoveEval/ply."""
    tc = rec.time_control
    tc_cls = config.tc_class(tc)
    base = config.base_seconds(tc)
    inc = _increment(tc)
    long_think_s = config.LONG_THINK_S.get(tc_cls, 15.0)

    limit = chess.engine.Limit(depth=scan_depth)
    board = rec.game.board()
    prev_clk = {chess.WHITE: float(base) if base else None,
                chess.BLACK: float(base) if base else None}

    # Pass 1: per-position eval + move context.
    steps: list[dict] = []
    for node in rec.game.mainline():
        color = board.turn
        info = engine.analyse(board, limit)
        e = info["score"].pov(color).score(mate_score=3000)
        best = info["pv"][0].uci() if info.get("pv") else node.move.uci()
        san = board.san(node.move)

        clk = node.clock()
        spent = remaining = None
        prev = prev_clk[color]
        if base is not None and clk is not None and prev is not None:
            spent = prev - clk + inc
            remaining = clk
            prev_clk[color] = clk

        steps.append({
            "epd": board.epd(), "fen": board.fen(), "uci": node.move.uci(),
            "san": san, "color": color, "fullmove": board.fullmove_number,
            "e": e, "best": best, "spent": spent, "remaining": remaining,
            "structure": classify_structure(board),
            "move_type": classify_move_type(board, node.move),
            "phase": phase_of(board, board.fullmove_number),
        })
        board.push(node.move)

    # Eval of the final position (mover-POV) closes the last move's "after".
    final_info = engine.analyse(board, limit)
    e_final = final_info["score"].pov(board.turn).score(mate_score=3000)

    # Pass 2: assemble MoveEvals using drop = e(P_k) + e(P_{k+1}).
    out: list[MoveEval] = []
    for k, s in enumerate(steps):
        next_e = steps[k + 1]["e"] if k + 1 < len(steps) else e_final
        before = s["e"]
        after = -next_e            # same position, mover's perspective
        drop = before + next_e     # = before - after
        spent = s["spent"]
        out.append(MoveEval(
            ply=k, fullmove=s["fullmove"],
            color=chess.COLOR_NAMES[s["color"]],
            is_me=int(s["color"] == rec.my_color),
            uci=s["uci"], san=s["san"], epd_before=s["epd"], fen_before=s["fen"],
            eval_cp_before=before, eval_cp_after=after, drop_cp=drop,
            best_uci=s["best"], phase=s["phase"], structure=s["structure"],
            move_type=s["move_type"], seconds_spent=spent,
            seconds_remaining=s["remaining"],
            is_long_think=int(spent is not None and spent >= long_think_s),
            game_state=game_state(before),
        ))
    return out


def _is_candidate(mv: MoveEval) -> bool:
    """Cheap first-pass flag: a real eval drop from a not-already-lost spot."""
    thresh = OPENING_MISTAKE_CP if mv.fullmove <= OPENING_MOVES else MISTAKE_CP
    return mv.drop_cp >= thresh and mv.eval_cp_before > DEAD_LOST_CP


def analyze_game(conn, row, engine: chess.engine.SimpleEngine, *,
                 verify_depth: int = VERIFY_DEPTH,
                 grade_depth: int = GRADE_DEPTH) -> dict:
    """Analyze one game and persist moves + mistakes + grade cache.

    Returns counts: {moves, mistakes, graded}. Commits per game and flags the
    game analyzed — the incremental unit that lets the UI work off partial data.
    """
    rec = record_from_row(row)
    if rec is None:
        db.mark_analyzed(conn, row["id"])
        return {"moves": 0, "mistakes": 0, "graded": 0}

    game_id = row["id"]
    evals = scan_game(rec, engine)

    move_rows = [{
        "game_id": game_id, "ply": mv.ply, "fullmove": mv.fullmove,
        "color": mv.color, "is_me": mv.is_me, "uci": mv.uci, "san": mv.san,
        "epd_before": mv.epd_before, "eval_cp_before": mv.eval_cp_before,
        "eval_cp_after": mv.eval_cp_after, "drop_cp": mv.drop_cp,
        "best_uci": mv.best_uci, "phase": mv.phase, "structure": mv.structure,
        "move_type": mv.move_type, "seconds_spent": mv.seconds_spent,
        "seconds_remaining": mv.seconds_remaining,
        "is_long_think": mv.is_long_think, "game_state": mv.game_state,
    } for mv in evals]

    # --- engine-bound pass: do ALL Stockfish work first, into memory. No DB
    # writes here, so a worker never holds the SQLite write lock across a search.
    # With parallel workers on one WAL database, this is what keeps them off each
    # other's write lock — see the persist pass below. (get_grade is a read; in
    # WAL it doesn't take the write lock, so it's fine to keep in this loop.)
    pending_mistakes = []  # (Mistake, MoveEval) confirmed to persist
    pending_grades = []    # (MoveEval, grades, best_uci) trainer grades to cache
    for mv in evals:
        if not _is_candidate(mv):
            continue
        mistake = confirm_mistake(
            mv.fen_before, mv.uci, mv.fullmove, rec.url, engine,
            chess.engine.Limit(depth=verify_depth),
        )
        if mistake is None:
            continue
        pending_mistakes.append((mistake, mv))
        # Precompute engine-free trainer grades only for the player's mistakes.
        if mv.is_me and db.get_grade(conn, mv.epd_before) is None:
            board = chess.Board(mv.fen_before)
            grades = grade_all_moves(
                board, engine, chess.engine.Limit(depth=grade_depth))
            if grades:
                best = max(grades, key=lambda m: grades[m])
                pending_grades.append((mv, grades, best))

    # --- persist pass: one short transaction. The write lock is now held only
    # for these fast INSERTs, not for any engine search, so a 30s busy_timeout
    # (see db.connect) trivially covers contention between workers.
    db.insert_moves(conn, move_rows)
    for mistake, mv in pending_mistakes:
        db.insert_mistake(
            conn, game_id, mistake, epd=mv.epd_before, is_me=mv.is_me,
            structure=mv.structure, move_type=mv.move_type, phase=mv.phase,
            eco=row["eco"] or "", game_state=mv.game_state, ply=mv.ply,
        )
    for mv, grades, best in pending_grades:
        db.upsert_grade(conn, mv.epd_before, grades, best,
                        mv.eval_cp_before, grade_depth, time.time())
    db.mark_analyzed(conn, game_id)
    conn.commit()
    return {"moves": len(move_rows), "mistakes": len(pending_mistakes),
            "graded": len(pending_grades)}
