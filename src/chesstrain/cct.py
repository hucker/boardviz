"""CCT (checks / captures / threats) helpers for the trainer's scan drill.

The *checks* and *captures* a side can play are pure board properties — cheap to
derive from a position with no engine — so they can be computed at drill time and
verified against what the user marks. (*Threats* — loose pieces winnable next
move — are a planned addition and will live here too.)
"""

from __future__ import annotations

import chess


def forcing_moves(board: chess.Board) -> tuple[set[str], set[str]]:
    """The checks and captures the side to move can play, as UCI sets.

    Args:
        board: the position (its side to move is the one being scanned).

    Returns:
        ``(checks, captures)`` — each a set of UCI strings. A move can be in both
        (a capturing check). No engine is used; these are exact board properties.
    """
    checks: set[str] = set()
    captures: set[str] = set()
    for move in board.legal_moves:
        uci = move.uci()
        if board.gives_check(move):
            checks.add(uci)
        if board.is_capture(move):
            captures.add(uci)
    return checks, captures
