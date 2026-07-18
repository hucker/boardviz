"""Trainer: position selection (dedup) and the pre-puzzle replay (intro)."""

import pytest

from chesstrain import trainer
from chesstrain.ui import trainer_page as tp


class TestPositionSelection:
    """Choosing drill positions from the player's graded mistakes."""

    @pytest.mark.spec("TRN-UNIQ")
    def test_a_position_blundered_twice_yields_one_puzzle(self, conn):
        """The same position across two games drills once, not once per game."""
        # Arrange: the same position (EPD1) blundered in two games.
        for gid in (1, 2):
            conn.execute(
                "INSERT INTO games(id, game_uuid, username, is_me, tc_class) "
                "VALUES(?,?,?,1,'blitz')", (gid, f"g{gid}", "alice"))
            conn.execute(
                "INSERT INTO mistakes(game_id, is_me, epd, fen, played_uci, "
                "structure, move_type, phase, drop_cp, ply) "
                "VALUES(?,1,'EPD1','fen1','e2e4','open center','quiet',"
                "'middlegame',300,10)", (gid,))
        conn.execute(
            "INSERT INTO grades_cache(epd, grades_json, best_uci, eval_cp, depth, "
            "created_ts) VALUES('EPD1','{}','d2d4',0,12,1.0)")
        conn.commit()
        # Act.
        positions = trainer.select_positions(conn, n=40, username="alice")
        # Assert.
        assert len(positions) == 1
        assert positions[0]["epd"] == "EPD1"


class TestIntroReplay:
    """The opponent-move replay that plays before the clock starts."""

    @pytest.mark.spec("TRN-INTRO")
    def test_no_intro_when_there_is_no_prior_ply(self):
        """A first-move mistake (no prior ply) has no replay."""
        assert tp._intro_for({"prev_epd": None, "opp_move": None}) is None
        assert tp._intro_for({"prev_epd": "x", "opp_move": None}) is None

    @pytest.mark.spec("TRN-INTRO")
    def test_intro_replay_clamps_the_delay_to_the_opponent_pace(self):
        """The replay delay follows the opponent's time, clamped to 0.5-8s."""
        # Arrange.
        base = {"prev_epd": "8/8/8/8/8/8/8/8 w - -", "opp_move": "e2e4"}

        def delay(secs):
            intro = tp._intro_for({**base, "opp_seconds": secs})
            assert intro is not None
            return intro["delayMs"]

        # Act + Assert: within range, capped, floored, and default.
        assert delay(3.0) == 3000
        assert delay(30) == 8000    # cap 8s
        assert delay(0.1) == 500    # floor 0.5s
        assert delay(None) == 1000  # default when the opponent's time is unknown
        # And the replayed move / position are carried through.
        intro = tp._intro_for({**base, "opp_seconds": 2.0})
        assert intro is not None
        assert intro["move"] == "e2e4"
        assert intro["prevFen"].startswith("8/8/8/8/8/8/8/8 w - -")
