"""Pure classifiers from the analysis pass (no engine)."""

import chess

from chesstrain import analysis_batch as ab


def test_position_key_is_epd():
    b = chess.Board()
    assert ab.position_key(b) == b.epd()


def test_classify_move_type():
    b = chess.Board()
    assert ab.classify_move_type(b, chess.Move.from_uci("e2e4")) == "quiet"
    # A capture.
    b2 = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2")
    assert ab.classify_move_type(b2, chess.Move.from_uci("d4e5")) == "capture"
    # A check (scholar's-mate queen to h5 then check path); use a simple one.
    b3 = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    assert ab.classify_move_type(b3, chess.Move.from_uci("d1h5")) == "quiet"


def test_retreat_detection():
    # White knight on f3 retreating to g1 (backward for White).
    b = chess.Board("rnbqkbnr/pppppppp/8/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 1")
    assert ab.classify_move_type(b, chess.Move.from_uci("f3g1")) == "retreat"


def test_phase_of():
    assert ab.phase_of(chess.Board(), 1) == "opening"
    assert ab.phase_of(chess.Board(), 20) == "middlegame"
    # King + pawn endgame: few pieces.
    end = chess.Board("8/5k2/8/8/8/8/3K1P2/8 w - - 0 40")
    assert ab.phase_of(end, 40) == "endgame"


def test_game_state_thresholds():
    assert ab.game_state(300) == "winning"
    assert ab.game_state(-300) == "losing"
    assert ab.game_state(0) == "equal"
