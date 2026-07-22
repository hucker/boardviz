"""Forced-mate detection and motif classification (pure, engine-free).

The analysis pass stores mate scores clamped to ±3000 (see ``blitz_analysis._pov``),
so a position with a forced mate in N has ``eval_cp_before ≈ 3000 - N``. This module
turns a game's stored move evals into *mate chances* — grouped opportunities scored
by whether the player finished the mate — and classifies a mate's *motif* from the
final checkmate position. The engine-bound step (recovering the forced line to feed
``classify_mate_motif``) lives in ``analysis_batch``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import chess

# A stored eval at or above this is a forced mate for the mover (distance = 3000-cp:
# M1=2999, M2=2998, …). Comfortably below the shallowest real mate, above any
# ordinary evaluation.
MATE_CP = 2900


@dataclass
class Chance:
    """One forced-mate opportunity for a side, from where the mate first appeared.

    ``converted`` is True when the player never dropped the forced mate on any of
    their moves in the run (they delivered or held it); ``drop_ply`` marks the move
    that blew it otherwise. ``motif`` / ``mate_pv`` are filled later (engine-bound).
    """

    ply: int
    fen: str
    distance: int
    key_uci: str
    converted: bool
    drop_ply: int | None


def _has_mate(cp: int | None) -> bool:
    """True if a mover-POV eval is a forced mate (and not already delivered)."""
    return cp is not None and MATE_CP <= cp < 3000


def detect_chances(rows: Sequence) -> list[Chance]:
    """Group one side's move rows (ply order) into forced-mate chances.

    Each row needs ``ply``, ``eval_cp_before``, ``eval_cp_after``, ``epd_before``,
    ``best_uci``. A chance is a maximal run of consecutive moves where the player
    held a forced mate; it is *blown* if any move in the run dropped the eval out
    of mate (``eval_cp_after`` below the mate threshold), else *converted*.
    """
    chances: list[Chance] = []
    run: dict | None = None

    def close(r: dict) -> None:
        nonlocal run
        chances.append(Chance(
            ply=r["ply"], fen=r["fen"], distance=r["distance"],
            key_uci=r["key_uci"], converted=r["drop_ply"] is None,
            drop_ply=r["drop_ply"]))
        run = None

    for row in rows:
        before, after = row["eval_cp_before"], row["eval_cp_after"]
        if _has_mate(before) and run is None:
            run = {"ply": row["ply"], "fen": f"{row['epd_before']} 0 1",
                   "distance": 3000 - before, "key_uci": row["best_uci"],
                   "drop_ply": None}
        if run is not None:
            if _has_mate(before):
                # Kept the mate iff the move left a forced mate on the board
                # (delivering mate lands at ~3000, also "kept").
                if not (after is not None and after >= MATE_CP):
                    run["drop_ply"] = row["ply"]
                    close(run)
            else:  # the mate is gone without the player blowing it -> held/delivered
                close(run)
    if run is not None:
        close(run)
    return chances


# --- motif classification --------------------------------------------------
def _square_zone(rank: int, file: int) -> str:
    """Where the mated king sits: corner / edge / centre."""
    on_edge_rank, on_edge_file = rank in (0, 7), file in (0, 7)
    if on_edge_rank and on_edge_file:
        return "corner"
    return "edge" if (on_edge_rank or on_edge_file) else "centre"


def classify_mate_motif(fen: str, mate_line: Sequence[str]) -> str:
    """Classify a forced mate by its final checkmate position.

    ``fen`` is the position at the chance start; ``mate_line`` is the forced line
    (UCI) that ends in checkmate. Returns a motif label — ``back-rank``,
    ``smothered``, ``double-check``, or ``<piece> (corner|edge|centre)`` — or
    ``unknown`` when the line doesn't resolve to mate (e.g. truncated PV).
    """
    board = chess.Board(fen)
    try:
        for uci in mate_line:
            board.push_uci(uci)
    except (ValueError, AssertionError):
        return "unknown"
    if not board.is_checkmate():
        return "unknown"

    mated = board.turn  # the side to move is the one checkmated
    ksq = board.king(mated)
    if ksq is None:
        return "unknown"
    krank, kfile = chess.square_rank(ksq), chess.square_file(ksq)
    neighbours = [
        chess.square(f, r)
        for f in range(max(0, kfile - 1), min(8, kfile + 2))
        for r in range(max(0, krank - 1), min(8, krank + 2))
        if chess.square(f, r) != ksq
    ]
    own_around = sum(
        1 for s in neighbours
        if (p := board.piece_at(s)) is not None and p.color == mated)

    checkers = list(board.checkers())
    checker = board.piece_at(checkers[0]) if checkers else None
    ptype = checker.piece_type if checker else None

    if ptype == chess.KNIGHT and own_around == len(neighbours):
        return "smothered"
    # Back-rank: king on its edge rank, checked *along* that rank by a rook/queen
    # (the checker shares the king's rank), with escape blocked toward the board.
    if (krank in (0, 7) and ptype in (chess.ROOK, chess.QUEEN)
            and chess.square_rank(checkers[0]) == krank):
        return "back-rank"
    if len(checkers) >= 2:
        return "double-check"
    name = chess.piece_name(ptype) if ptype else "?"
    return f"{name} ({_square_zone(krank, kfile)})"
