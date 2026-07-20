"""Trainer scoring: time-free move score and the win/loss readout."""

import pytest

from chesstrain import grading


class TestScoring:
    """Move scored by quality alone (no time), and the win/loss readout."""

    @pytest.mark.spec("TRN-SCORE")
    def test_score_is_move_quality_only(self):
        """Monotonic in grade: +1 good, -0.5 inaccuracy, -1 blunder — no time."""
        # Good moves (best or a sound alternative) score +1.
        assert grading.score_attempt({"e2e4": 2}, "e2e4")["final_score"] == 1.0
        assert grading.score_attempt({"e2e4": 1}, "e2e4")["final_score"] == 1.0
        # An inaccuracy costs half a point; a blunder a whole one.
        assert grading.score_attempt({"e2e4": -1}, "e2e4")["final_score"] == -0.5
        assert grading.score_attempt({"e2e4": -2}, "e2e4")["final_score"] == -1.0
        # An unknown/illegal move grades -2 and so scores -1.
        assert grading.score_attempt({}, "a2a3")["final_score"] == -1.0

    @pytest.mark.spec("TRN-SCORE")
    def test_win_loss_readout_phrasing(self):
        """The readout names winning/equal/losing and respects the POV label."""
        assert "winning" in grading.win_loss_readout(400)
        assert "losing" in grading.win_loss_readout(-400)
        assert "equal" in grading.win_loss_readout(50)
        assert grading.win_loss_readout(400, pov="you").startswith("You are")
        assert grading.win_loss_readout(400, pov="White").startswith("White is")


class TestDeterminism:
    """Scoring is a pure function of the position and move."""

    @pytest.mark.spec("NFR-DETER")
    def test_score_attempt_is_deterministic(self):
        """The same move scores the same every time (no time, no engine)."""
        # Arrange.
        grades = {"e2e4": 2, "d2d4": -1}
        # Act + Assert.
        assert (grading.score_attempt(grades, "e2e4")
                == grading.score_attempt(grades, "e2e4"))
