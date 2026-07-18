"""Pure classifiers from the analysis pass — the tags mistakes are clustered by."""

import chess
import pytest

from chesstrain import analysis_batch as ab


class TestClassifiers:
    """Position key and the structure/move-type/phase tags used to cluster."""

    @pytest.mark.spec("TRN-UNIQ")
    def test_position_key_is_the_epd(self):
        """A position's key is its EPD (layout only), so it recurs across games."""
        board = chess.Board()
        assert ab.position_key(board) == board.epd()

    @pytest.mark.spec("REV-CLUST")
    def test_classify_move_type_ranks_capture_check_over_quiet(self):
        """A move is tagged capture/check/quiet by priority."""
        # Arrange: opening move, a capture, and a queen sortie.
        start = chess.Board()
        after_e5 = chess.Board(
            "rnbqkbnr/pppp1ppp/8/4p3/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2")
        pre_qh5 = chess.Board(
            "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
        # Act + Assert.
        assert ab.classify_move_type(start, chess.Move.from_uci("e2e4")) == "quiet"
        assert ab.classify_move_type(after_e5, chess.Move.from_uci("d4e5")) == "capture"
        assert ab.classify_move_type(pre_qh5, chess.Move.from_uci("d1h5")) == "quiet"

    @pytest.mark.spec("REV-CLUST")
    def test_classify_move_type_detects_a_retreat(self):
        """A non-pawn moving backward is a retreat."""
        # Arrange: white knight f3 -> g1 (backward for White).
        board = chess.Board(
            "rnbqkbnr/pppppppp/8/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 1")
        # Act + Assert.
        assert ab.classify_move_type(board, chess.Move.from_uci("f3g1")) == "retreat"

    @pytest.mark.spec("REV-CLUST")
    def test_phase_of_splits_opening_middlegame_endgame(self):
        """Phase is opening (early), endgame (few pieces), else middlegame."""
        assert ab.phase_of(chess.Board(), 1) == "opening"
        assert ab.phase_of(chess.Board(), 20) == "middlegame"
        end = chess.Board("8/5k2/8/8/8/8/3K1P2/8 w - - 0 40")  # K+P vs K
        assert ab.phase_of(end, 40) == "endgame"


class TestGameState:
    """Winning/equal/losing labelling that drives the big-think analytic."""

    @pytest.mark.spec("REV-THINK")
    def test_game_state_thresholds(self):
        """Eval maps to winning/losing past the threshold, else equal."""
        assert ab.game_state(300) == "winning"
        assert ab.game_state(-300) == "losing"
        assert ab.game_state(0) == "equal"
