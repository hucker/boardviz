"""Trainer helpers: the pre-puzzle replay (intro) construction."""

from chesstrain.ui import trainer_page as tp


def test_intro_for_none_without_prior_ply():
    assert tp._intro_for({"prev_epd": None, "opp_move": None}) is None
    assert tp._intro_for({"prev_epd": "x", "opp_move": None}) is None


def test_intro_for_builds_replay_and_clamps_delay():
    base = {"prev_epd": "8/8/8/8/8/8/8/8 w - -", "opp_move": "e2e4"}

    def delay(secs):
        intro = tp._intro_for({**base, "opp_seconds": secs})
        assert intro is not None
        return intro["delayMs"]

    assert delay(3.0) == 3000
    assert delay(30) == 8000    # cap 8s
    assert delay(0.1) == 500    # floor 0.5s
    assert delay(None) == 1000  # default when the opponent's time is unknown

    intro = tp._intro_for({**base, "opp_seconds": 2.0})
    assert intro is not None
    assert intro["move"] == "e2e4"
    assert intro["prevFen"].startswith("8/8/8/8/8/8/8/8 w - -")  # EPD + counters
