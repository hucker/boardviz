"""Scoring: time-penalty curve, combined score, win/loss readout."""

from chesstrain import grading


def test_blitz_time_penalty_curve():
    assert grading.time_penalty(3, "blitz") == 0
    assert grading.time_penalty(10, "blitz") == -1
    assert grading.time_penalty(19.9, "blitz") == -1
    assert grading.time_penalty(20, "blitz") == -2
    assert grading.time_penalty(99, "blitz") == -2


def test_rapid_curve_is_more_lenient():
    assert grading.time_penalty(20, "rapid") == 0
    assert grading.time_penalty(30, "rapid") == -1
    assert grading.time_penalty(60, "rapid") == -2


def test_daily_has_no_penalty():
    assert grading.time_penalty(9999, "daily") == 0


def test_score_combines_grade_and_penalty_clamped():
    # Best move but slow: +2 grade, -2 penalty -> 0.
    assert grading.score_attempt({"e2e4": 2}, "e2e4", 25, "blitz") == {
        "grade": 2, "time_penalty": -2, "final_score": 0}
    # Best move fast: stays +2.
    assert grading.score_attempt({"e2e4": 2}, "e2e4", 2, "blitz")["final_score"] == 2
    # Unknown/illegal move -> -2, clamp floor holds.
    assert grading.score_attempt({}, "a2a3", 25, "blitz")["final_score"] == -2


def test_win_loss_readout():
    assert "winning" in grading.win_loss_readout(400)
    assert "losing" in grading.win_loss_readout(-400)
    assert "equal" in grading.win_loss_readout(50)
    assert grading.win_loss_readout(400, pov="you").startswith("You are")
    assert grading.win_loss_readout(400, pov="White").startswith("White is")
