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


class TestThreats:
    """Enemy pieces the side to move can win material on (TRN-CCT)."""

    @pytest.mark.spec("TRN-CCT")
    def test_hanging_enemy_piece_is_a_threat(self):
        """An undefended enemy piece the side attacks is a threat."""
        # Arrange: White Bc3 attacks the undefended black knight on e5.
        board = chess.Board("4k3/8/8/4n3/8/2B5/8/4K3 w - - 0 1")
        # Act.
        won = cct.threats(board)
        # Assert.
        assert won == {"e5"}

    @pytest.mark.spec("TRN-CCT")
    def test_defended_equal_piece_is_not_a_threat(self):
        """An enemy piece defended and only attacked at equal value is safe."""
        # Arrange: White Nf3 attacks a black knight on e5 defended by the d6 pawn.
        board = chess.Board("4k3/8/3p4/4n3/8/5N2/8/4K3 w - - 0 1")
        # Act.
        won = cct.threats(board)
        # Assert: knight-for-knight is not winning material.
        assert won == set()

    @pytest.mark.spec("TRN-CCT")
    def test_winning_the_exchange_is_a_threat(self):
        """A defended queen attacked by a cheaper rook is still a threat."""
        # Arrange: White Re1 attacks a black queen on e5 defended by the f6 pawn.
        board = chess.Board("4k3/8/5p2/4q3/8/8/8/4R1K1 w - - 0 1")
        # Act.
        won = cct.threats(board)
        # Assert: rook (5) wins the queen (9) even though it is defended.
        assert won == {"e5"}

    @pytest.mark.spec("TRN-CCT")
    def test_enemy_king_is_never_a_threat(self):
        """The king can be checked but not won, so it is never in the set."""
        # Arrange: White Bc3 attacks e5; the black king sits on e8.
        board = chess.Board("4k3/8/8/4n3/8/2B5/8/4K3 w - - 0 1")
        # Act.
        won = cct.threats(board)
        # Assert.
        assert "e8" not in won

    @pytest.mark.spec("TRN-CCT")
    def test_quiet_position_has_no_threats(self):
        """The opening position has nothing to win."""
        # Act & Assert.
        assert cct.threats(chess.Board()) == set()


class TestScanBoth:
    """Both-ways scan: my forcing moves plus the opponent's (TRN-CCT)."""

    @pytest.mark.spec("TRN-CCT")
    def test_each_side_gets_its_own_check(self):
        """A check is available to each side, landing in me vs opp respectively."""
        # Arrange: White Qxf7+ (h5f7); after a null flip, Black has Qxf2+ (h4f2).
        board = chess.Board("4k3/5p2/8/7Q/7q/8/5P2/4K3 w - - 0 1")
        # Act.
        scan = cct.scan_both(board)
        # Assert.
        assert "h5f7" in scan["me"]["checks"]
        assert "h4f2" in scan["opp"]["checks"]

    @pytest.mark.spec("TRN-CCT")
    def test_threats_point_at_opposite_colours(self):
        """My threats are enemy squares; the opponent's are my own squares."""
        # Arrange: White Bb2 wins the hanging Ne5; after a flip, Black Bh1 wins Nd5.
        board = chess.Board("6k1/8/8/3Nn3/8/8/1B6/4K2b w - - 0 1")
        # Act.
        scan = cct.scan_both(board)
        # Assert: e5 is Black's square (my threat), d5 is White's square (theirs).
        assert "e5" in scan["me"]["threats"]
        assert "d5" in scan["opp"]["threats"]

    @pytest.mark.spec("TRN-CCT")
    def test_opponent_scan_is_empty_when_in_check(self):
        """You cannot null-move out of check, so the opponent scan is degenerate."""
        # Arrange: a black rook checks the white king down the open e-file.
        board = chess.Board("4r3/8/8/8/8/8/8/4K3 w - - 0 1")
        # Act.
        scan = cct.scan_both(board)
        # Assert.
        assert scan["opp"] == {"checks": set(), "captures": set(), "threats": set()}
