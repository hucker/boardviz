"""Blitz game analysis pipeline for chess.com archives.

Consumes the chess.com monthly archive JSON shape ({"games": [...]}) and
provides: game classification, clock/move-time analysis, engine blunder
detection, pawn-structure classification, puzzle extraction from mistakes,
and full-legal-move grading for the +2/+1/-1/-2 trainer.

Requires: python-chess, and a UCI engine binary (stockfish).

Typical use:
    games = load_games(Path("games.json"), username="hucker233")
    summary = [classify_game(g) for g in games]
    clocks = [move_times(g) for g in games]
    with EnginePool() as eng:
        mistakes = find_mistakes(games[0], eng)
        grades = grade_all_moves(chess.Board(fen), eng)

This module stays domain-pure: no Streamlit, no SQLite. The only project
dependency is ``config`` for locating the engine binary.
"""

from __future__ import annotations

import datetime as dt
import io
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.engine
import chess.pgn

from .config import resolve_engine_path

# Grading thresholds in centipawns of eval loss vs the best move.
GRADE_BEST = 25       # <= this loss: +2
GRADE_GOOD = 100      # <= this loss: +1
GRADE_BAD = 250       # <= this loss: -1; beyond: -2

# Mistake-detection thresholds.
OPENING_MOVES = 12
OPENING_MISTAKE_CP = 100
MISTAKE_CP = 200
DEAD_LOST_CP = -400   # don't flag mistakes in already-dead positions
LONG_THINK_S = 15.0


@dataclass
class GameRecord:
    """Parsed game with metadata resolved to the tracked player's POV."""

    game: chess.pgn.Game
    url: str
    my_color: chess.Color
    outcome: str            # "win" | "loss" | "draw"
    termination: str
    time_control: str
    end_time: dt.datetime
    flagged: bool           # lost on time
    uuid: str = ""          # chess.com game id (for dedup/persistence)
    pgn: str = ""           # original PGN text (lossless %clk for later analysis)


@dataclass
class Mistake:
    """One engine-confirmed error by the tracked player."""

    fen: str
    played: str             # UCI
    best_pv: list[str]      # UCI moves
    fullmove: int
    drop_cp: int
    url: str


class EnginePool:
    """Context manager owning a single UCI engine instance."""

    def __init__(self, path: str | None = None):
        self._path = path or resolve_engine_path()
        self.engine: chess.engine.SimpleEngine | None = None

    def __enter__(self) -> chess.engine.SimpleEngine:
        self.engine = chess.engine.SimpleEngine.popen_uci(self._path)
        return self.engine

    def __exit__(self, *exc) -> None:
        if self.engine is not None:
            self.engine.quit()


def load_games(
    src: Path,
    username: str,
    time_control: str | None = "180",
) -> list[GameRecord]:
    """Load and classify games from a chess.com archive JSON file.

    Args:
        src: Path to JSON with a top-level "games" list.
        username: chess.com username to track (case sensitive per headers).
        time_control: Keep only this TimeControl (e.g. "180"); None keeps all.

    Returns:
        List of GameRecord, in file order.
    """
    raw = json.loads(src.read_text())["games"]
    out: list[GameRecord] = []
    for g in raw:
        if time_control is not None and g.get("time_control") != time_control:
            continue
        game = chess.pgn.read_game(io.StringIO(g["pgn"]))
        if game is None:
            continue
        h = game.headers
        my_color = chess.WHITE if h.get("White") == username else chess.BLACK
        res = h.get("Result", "*")
        won = (res == "1-0" and my_color == chess.WHITE) or (
            res == "0-1" and my_color == chess.BLACK
        )
        outcome = "win" if won else ("draw" if res == "1/2-1/2" else "loss")
        term = h.get("Termination", "")
        out.append(GameRecord(
            game=game,
            url=g.get("url", ""),
            my_color=my_color,
            outcome=outcome,
            termination=term,
            time_control=g.get("time_control", ""),
            end_time=dt.datetime.fromtimestamp(g.get("end_time", 0)),
            flagged=(outcome == "loss" and "won on time" in term),
            uuid=g.get("uuid", ""),
            pgn=g.get("pgn", ""),
        ))
    return out


def summarize(records: list[GameRecord]) -> dict:
    """Aggregate results, terminations, and flag counts."""
    results = Counter(r.outcome for r in records)
    flags = sum(r.flagged for r in records)
    by_color = Counter(
        f"{chess.COLOR_NAMES[r.my_color]}/{r.outcome}" for r in records
    )
    return {
        "games": len(records),
        "results": dict(results),
        "flag_losses": flags,
        "by_color": dict(by_color),
    }


def move_times(rec: GameRecord, base_s: float = 180.0, inc_s: float = 0.0
               ) -> list[tuple[int, float, float]]:
    """Extract per-move time spent by the tracked player.

    Requires %clk annotations in the PGN (chess.com includes them).

    Args:
        rec: A GameRecord.
        base_s: Starting clock in seconds.
        inc_s: Increment per move in seconds.

    Returns:
        List of (fullmove_number, seconds_spent, seconds_remaining).
    """
    out = []
    prev = base_s
    board = rec.game.board()
    for node in rec.game.mainline():
        if board.turn == rec.my_color:
            clk = node.clock()
            if clk is not None:
                spent = prev - clk + inc_s
                out.append((board.fullmove_number, spent, clk))
                prev = clk
        board.push(node.move)
    return out


def long_thinks(rec: GameRecord, threshold_s: float = LONG_THINK_S
                ) -> list[tuple[int, float, float]]:
    """Filter move_times to thinks at or above the threshold."""
    return [t for t in move_times(rec) if t[1] >= threshold_s]


def classify_structure(board: chess.Board) -> str:
    """Coarse center-structure taxonomy used for clustering mistakes."""
    wp = {chess.square_name(s) for s in board.pieces(chess.PAWN, chess.WHITE)}
    bp = {chess.square_name(s) for s in board.pieces(chess.PAWN, chess.BLACK)}
    wc = wp & {"c3", "c4", "d3", "d4", "d5", "e3", "e4", "e5", "f4"}
    bc = bp & {"c5", "c6", "d4", "d5", "e5", "e6", "f5", "f6"}
    locked = ("e5" in wp and "e6" in bp and "d4" in wp and "d5" in bp) or (
        "e4" in wp and "d5" in bp and "e6" in bp
    )
    if locked:
        return "locked chain center"
    if not wc and not bc:
        return "open center"
    if len(wc) <= 1 and len(bc) <= 1:
        return "mostly open center"
    if "d4" in wp and "d5" in bp and "e4" not in wp and "e5" not in bp:
        return "symmetric d-pawns"
    return "mixed/closed center"


def _pov(info, color) -> int:
    """PovScore for `color` in centipawns, mate clamped to a large finite cp."""
    return info["score"].pov(color).score(mate_score=3000)


def confirm_mistake(
    fen: str,
    played: str,
    fullmove: int,
    url: str,
    engine: chess.engine.SimpleEngine,
    verify: chess.engine.Limit = chess.engine.Limit(depth=14),
) -> Mistake | None:
    """Deep-verify a single candidate error and require a distinctly-best reply.

    A candidate is confirmed only when the best move is meaningfully better than
    the played one *and* clearly better than the second-best alternative, so the
    resulting trainer puzzle has a real, unique answer.

    Returns:
        A Mistake if confirmed, else None.
    """
    board = chess.Board(fen)
    infos = engine.analyse(board, verify, multipv=2)
    best_cp = _pov(infos[0], board.turn)
    second_cp = _pov(infos[1], board.turn) if len(infos) > 1 else -3000
    board.push(chess.Move.from_uci(played))
    after_cp = _pov(engine.analyse(board, verify), not board.turn)
    drop = best_cp - after_cp
    thresh = OPENING_MISTAKE_CP if fullmove <= OPENING_MOVES else MISTAKE_CP
    if drop >= thresh and best_cp > -300 and best_cp - second_cp >= 80:
        return Mistake(
            fen=fen, played=played,
            best_pv=[m.uci() for m in infos[0]["pv"][:4]],
            fullmove=fullmove, drop_cp=drop, url=url,
        )
    return None


def find_mistakes(
    rec: GameRecord,
    engine: chess.engine.SimpleEngine,
    scan: chess.engine.Limit = chess.engine.Limit(depth=8),
    verify: chess.engine.Limit = chess.engine.Limit(depth=14),
    max_fullmove: int = 40,
) -> list[Mistake]:
    """Two-pass mistake detection for one game.

    Pass 1 flags candidate eval drops at low depth; pass 2 confirms each at
    higher depth via :func:`confirm_mistake` (which also requires a distinctly
    best alternative, so the resulting puzzle has a real answer).
    """
    candidates = []
    board = rec.game.board()
    for node in rec.game.mainline():
        if board.turn == rec.my_color and board.fullmove_number <= max_fullmove:
            before = _pov(engine.analyse(board, scan), rec.my_color)
            fen, fullmove = board.fen(), board.fullmove_number
            board.push(node.move)
            after = _pov(engine.analyse(board, scan), rec.my_color)
            thresh = OPENING_MISTAKE_CP if fullmove <= OPENING_MOVES else MISTAKE_CP
            if before - after >= thresh and before > DEAD_LOST_CP:
                candidates.append((fen, node.move.uci(), fullmove))
        else:
            board.push(node.move)

    mistakes = []
    for fen, played, fullmove in candidates:
        m = confirm_mistake(fen, played, fullmove, rec.url, engine, verify)
        if m is not None:
            mistakes.append(m)
    return mistakes


def grade_all_moves(
    board: chess.Board,
    engine: chess.engine.SimpleEngine,
    limit: chess.engine.Limit = chess.engine.Limit(depth=12),
) -> dict[str, int]:
    """Grade every legal move in a position for the trainer.

    One MultiPV search wide enough to rank all root moves; each move's grade
    derives from its eval loss versus the best move:
        +2  loss <= GRADE_BEST
        +1  loss <= GRADE_GOOD
        -1  loss <= GRADE_BAD
        -2  otherwise

    Returns:
        Mapping of UCI move -> grade. Includes every legal move, so the
        trainer needs no engine at runtime.
    """
    n = board.legal_moves.count()
    if n == 0:
        return {}
    infos = engine.analyse(board, limit, multipv=n)
    scores = {
        info["pv"][0].uci(): info["score"].pov(board.turn).score(mate_score=3000)
        for info in infos if "pv" in info and info["pv"]
    }
    best = max(scores.values())
    grades = {}
    for uci, cp in scores.items():
        loss = best - cp
        if loss <= GRADE_BEST:
            grades[uci] = 2
        elif loss <= GRADE_GOOD:
            grades[uci] = 1
        elif loss <= GRADE_BAD:
            grades[uci] = -1
        else:
            grades[uci] = -2
    return grades


if __name__ == "__main__":
    import sys

    src = Path(sys.argv[1] if len(sys.argv) > 1 else "games.json")
    user = sys.argv[2] if len(sys.argv) > 2 else "hucker233"
    records = load_games(src, username=user)
    print(json.dumps(summarize(records), indent=2))
    total_lt = sum(len(long_thinks(r)) for r in records)
    print(f"long thinks (>= {LONG_THINK_S:.0f}s): {total_lt}")
