"""Trainer helpers: the pre-puzzle replay (intro) construction."""

from chesstrain import trainer
from chesstrain.ui import trainer_page as tp


def test_select_positions_dedups_by_position(conn):
    # Same position blundered in two different games -> two mistakes rows.
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
    ps = trainer.select_positions(conn, n=40, username="alice")
    assert len(ps) == 1  # one puzzle for the position, not one per game
    assert ps[0]["epd"] == "EPD1"


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
