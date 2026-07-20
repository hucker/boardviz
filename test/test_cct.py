"""CCT scan helpers: the checks and captures a side can play (for the drill)."""

import chess
import pytest

from chesstrain import cct


class TestForcingMoves:
    """Deriving the checks and captures available to the side to move (TRN-CCT)."""

    @pytest.mark.spec("TRN-CCT")
    def test_checks_and_captures_are_split_out(self):
        """A position yields exactly its checking and capturing moves."""
        # Arrange: after 1.e4 e5 2.Bc4 Nc6 3.Qh5, White to move next would be
        # black; use a position where the side to move has both a check and a
        # capture. 1.e4 d5 2.exd5 — Black to move: ...Qxd5 is a capture (not check).
        board = chess.Board(
            "rnbqkbnr/ppp1pppp/8/3P4/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2")
        # Act.
        checks, captures = cct.forcing_moves(board)
        # Assert: Qxd5 and Nxd5? only ...Qxd5 (queen) captures the d5 pawn here.
        assert "d8d5" in captures        # Qxd5
        assert not checks                 # no checks available to Black here

    @pytest.mark.spec("TRN-CCT")
    def test_a_capturing_check_lands_in_both_sets(self):
        """A move that both captures and checks appears in checks and captures."""
        # Arrange: White Rook e1 can take on e8 with check (back-rank), king g8.
        board = chess.Board("4r1k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1")
        # Act.
        checks, captures = cct.forcing_moves(board)
        # Assert: Rxe8+ is both.
        assert "e1e8" in checks
        assert "e1e8" in captures

    @pytest.mark.spec("TRN-CCT")
    def test_quiet_position_has_no_forcing_moves(self):
        """The opening position has no checks and no captures."""
        # Act.
        checks, captures = cct.forcing_moves(chess.Board())
        # Assert.
        assert checks == set()
        assert captures == set()
