"""CCT (checks / captures / threats) helpers for the trainer's scan drill.

The *checks*, *captures* and *threats* a side can find are all pure board
properties — cheap to derive with no engine — so they can be computed at drill
time and verified against what the user marks. The drill scans **both ways**:
your own forcing moves (offense) and the opponent's (safety — what they can do to
you). The opponent's view is the same functions on a null-move flip of the board.
"""

from __future__ import annotations

import chess

# Material values for the one-ply "can I win this piece?" test (king excluded —
# it can't be won, only checked).
PIECE_VALUES: dict[chess.PieceType, int] = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9,
}


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


def threats(board: chess.Board) -> set[str]:
    """The enemy pieces the side to move can win material on, as square names.

    A deterministic one-ply heuristic (no engine): an enemy non-king piece counts
    as a threat if the side to move attacks it and it is either **hanging**
    (undefended) or a **favourable exchange** (my cheapest attacker is worth less
    than the piece, so I win material even if it is defended). This ignores pins
    (``attackers`` still counts a pinned attacker) and deeper exchange sequences —
    a full static-exchange evaluation is a later refinement.

    Args:
        board: the position (its side to move is the one doing the winning).

    Returns:
        A set of square names (e.g. ``"e5"``) — the enemy pieces on the board that
        the side to move can win.
    """
    me = board.turn
    won: set[str] = set()
    for sq, piece in board.piece_map().items():
        if piece.color == me or piece.piece_type == chess.KING:
            continue
        my_attackers = board.attackers(me, sq)
        if not my_attackers:
            continue
        victim = PIECE_VALUES[piece.piece_type]
        if not board.attackers(not me, sq):  # undefended — any attacker wins it
            won.add(chess.square_name(sq))
            continue
        # Attacker squares are always occupied; the guard just satisfies typing.
        cheapest = min(PIECE_VALUES[pt] for a in my_attackers
                       if (pt := board.piece_type_at(a)) is not None)
        if cheapest < victim:  # win the exchange (e.g. rook takes a defended queen)
            won.add(chess.square_name(sq))
    return won


def scan_both(board: chess.Board) -> dict[str, dict[str, set[str]]]:
    """Both sides' checks, captures and threats for the both-ways CCT drill.

    Returns ``{"me": {...}, "opp": {...}}``, each a dict of ``checks``/``captures``
    (UCI sets) and ``threats`` (square-name set). ``me`` is the side to move.
    ``opp`` is derived from a null-move flip so it reads *their* forcing moves —
    including ``threats`` that are squares of **my** pieces the opponent can win.

    When the side to move is in check a null move is illegal (you cannot pass out
    of check), so ``opp`` comes back empty — you are already answering a check and
    the safety scan is degenerate.
    """
    me_checks, me_captures = forcing_moves(board)
    me = {"checks": me_checks, "captures": me_captures, "threats": threats(board)}

    if board.is_check():
        opp = {"checks": set(), "captures": set(), "threats": set()}
    else:
        flipped = board.copy(stack=False)
        flipped.push(chess.Move.null())
        opp_checks, opp_captures = forcing_moves(flipped)
        opp = {"checks": opp_checks, "captures": opp_captures,
               "threats": threats(flipped)}
    return {"me": me, "opp": opp}
