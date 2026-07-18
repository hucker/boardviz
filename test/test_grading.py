"""Trainer scoring: time-penalty curve, combined score, win/loss readout."""

import pytest

from chesstrain import grading


class TestTimePenalty:
    """The per-time-control speed penalty applied to a trainer answer."""

    @pytest.mark.spec("TRN-SCORE")
    def test_blitz_penalty_steps_down_with_time(self):
        """Blitz: 0 while quick, -1 past 10s, -2 past 20s."""
        assert grading.time_penalty(3, "blitz") == 0
        assert grading.time_penalty(10, "blitz") == -1
        assert grading.time_penalty(19.9, "blitz") == -1
        assert grading.time_penalty(20, "blitz") == -2
        assert grading.time_penalty(99, "blitz") == -2

    @pytest.mark.spec("TRN-SCORE")
    def test_rapid_curve_is_more_lenient_than_blitz(self):
        """Rapid tolerates longer thinks before penalising."""
        assert grading.time_penalty(20, "rapid") == 0
        assert grading.time_penalty(30, "rapid") == -1
        assert grading.time_penalty(60, "rapid") == -2

    @pytest.mark.spec("TRN-SCORE")
    def test_daily_has_no_time_penalty(self):
        """Daily games are untimed, so there's no speed penalty."""
        assert grading.time_penalty(9999, "daily") == 0


class TestScoring:
    """Combining eval grade with the time penalty, and the win/loss readout."""

    @pytest.mark.spec("TRN-SCORE")
    def test_score_combines_grade_and_penalty_clamped(self):
        """Final score = grade + penalty, clamped to [-2, +2]."""
        # Best move but slow: +2 grade, -2 penalty -> 0.
        assert grading.score_attempt({"e2e4": 2}, "e2e4", 25, "blitz") == {
            "grade": 2, "time_penalty": -2, "final_score": 0}
        # Best move, fast: stays +2.
        assert grading.score_attempt(
            {"e2e4": 2}, "e2e4", 2, "blitz")["final_score"] == 2
        # Unknown/illegal move -> -2, and the clamp floor holds.
        assert grading.score_attempt({}, "a2a3", 25, "blitz")["final_score"] == -2

    @pytest.mark.spec("TRN-SCORE")
    def test_win_loss_readout_phrasing(self):
        """The readout names winning/equal/losing and respects the POV label."""
        assert "winning" in grading.win_loss_readout(400)
        assert "losing" in grading.win_loss_readout(-400)
        assert "equal" in grading.win_loss_readout(50)
        assert grading.win_loss_readout(400, pov="you").startswith("You are")
        assert grading.win_loss_readout(400, pov="White").startswith("White is")
