"""Trainer: position selection (modes, filters, dedup) and the replay (intro)."""

import pytest

from chesstrain import trainer
from chesstrain.ui import trainer_page as tp


def _add_position(conn, *, game_id, epd, structure, move_type, phase, drop_cp,
                  username="alice", ply=10, opening=None, fullmove=None):
    """Seed one drillable position: a game, my mistake there, and its grades."""
    conn.execute(
        "INSERT OR IGNORE INTO games(id, game_uuid, username, is_me, tc_class, "
        "opening) VALUES(?,?,?,1,'blitz',?)",
        (game_id, f"g{game_id}", username, opening))
    conn.execute(
        "INSERT INTO mistakes(game_id, is_me, epd, fen, played_uci, structure, "
        "move_type, phase, drop_cp, ply, fullmove) VALUES(?,1,?,?,'e2e4',?,?,?,?,?,?)",
        (game_id, epd, f"fen-{epd}", structure, move_type, phase, drop_cp, ply,
         fullmove))
    conn.execute(
        "INSERT OR IGNORE INTO grades_cache(epd, grades_json, best_uci, eval_cp, "
        "depth, created_ts) VALUES(?,'{}','d2d4',0,12,1.0)", (epd,))


@pytest.fixture
def drill_conn(conn):
    """Three graded positions of varying pattern/severity for 'alice'.

    EPD1 open-centre quiet middlegame drop 100 (also blundered again in g4);
    EPD2 closed-centre capture opening drop 300 (later drilled and failed);
    EPD3 open-centre quiet endgame drop 200 (later drilled and passed).
    """
    _add_position(conn, game_id=1, epd="EPD1", structure="open center",
                  move_type="quiet", phase="middlegame", drop_cp=100)
    _add_position(conn, game_id=2, epd="EPD2", structure="closed center",
                  move_type="capture", phase="opening", drop_cp=300)
    _add_position(conn, game_id=3, epd="EPD3", structure="open center",
                  move_type="quiet", phase="endgame", drop_cp=200)
    # EPD1 blundered a second time (different game) — makes it "repeated".
    _add_position(conn, game_id=4, epd="EPD1", structure="open center",
                  move_type="quiet", phase="middlegame", drop_cp=100)
    # Prior drill attempts: EPD2 failed (grade 0), EPD3 passed (grade 2).
    for epd, grade in (("EPD2", 0), ("EPD3", 2)):
        conn.execute("INSERT INTO attempts(epd, grade, created_ts) VALUES(?,?,2.0)",
                     (epd, grade))
    conn.commit()
    return conn


def _epds(positions):
    """The EPDs of a selection, in order."""
    return [p["epd"] for p in positions]


class TestSelectionModes:
    """Ordering modes: worst-first and repeat-my-failures (TRN-MODE)."""

    @pytest.mark.spec("TRN-MODE")
    def test_worst_mode_orders_by_biggest_eval_drop(self, drill_conn):
        """'worst' returns positions biggest-blunder first."""
        # Act.
        got = trainer.select_positions(drill_conn, mode="worst", username="alice")
        # Assert: 300, then 200, then 100.
        assert _epds(got) == ["EPD2", "EPD3", "EPD1"]

    @pytest.mark.spec("TRN-MODE")
    def test_repeat_failures_mode_keeps_only_positions_failed_before(self, drill_conn):
        """'repeat_failures' drills only positions attempted and failed (grade < 1)."""
        # Act.
        got = trainer.select_positions(
            drill_conn, mode="repeat_failures", username="alice")
        # Assert: EPD2 failed; EPD3 passed and is excluded.
        assert _epds(got) == ["EPD2"]

    @pytest.mark.spec("TRN-MODE")
    def test_default_mode_returns_the_whole_pool(self, drill_conn):
        """The default (random) mode samples the full deduped pool."""
        # Act.
        got = trainer.select_positions(drill_conn, username="alice")
        # Assert: one puzzle per position, all three present (order unspecified).
        assert set(_epds(got)) == {"EPD1", "EPD2", "EPD3"}


class TestPatternFilters:
    """Structure / move-type / phase filters, and that they compose (TRN-PATRN)."""

    @pytest.mark.spec("TRN-PATRN")
    def test_each_pattern_dimension_narrows_the_pool(self, drill_conn):
        """A single pattern filter keeps only matching positions."""
        # Act + Assert.
        assert set(_epds(trainer.select_positions(
            drill_conn, username="alice", structure="open center"))) == {"EPD1", "EPD3"}
        assert _epds(trainer.select_positions(
            drill_conn, username="alice", move_type="capture")) == ["EPD2"]
        assert _epds(trainer.select_positions(
            drill_conn, username="alice", phase="opening")) == ["EPD2"]

    @pytest.mark.spec("TRN-PATRN")
    def test_pattern_filters_compose(self, drill_conn):
        """Independent pattern filters AND together."""
        # Act: open-centre AND endgame is only EPD3 (EPD1 is a middlegame).
        got = trainer.select_positions(
            drill_conn, username="alice", structure="open center", phase="endgame")
        # Assert.
        assert _epds(got) == ["EPD3"]

    @pytest.mark.spec("TRN-PATRN")
    def test_opening_filter_scopes_the_drill_to_one_line(self, conn):
        """Exact and word-contains opening filters both scope the drill."""
        # Arrange: two French Advance variants and an unrelated opening.
        _add_position(conn, game_id=1, epd="A1", structure="s", move_type="quiet",
                      phase="opening", drop_cp=200,
                      opening="French Defense Advance Nimzowitsch System")
        _add_position(conn, game_id=2, epd="A2", structure="s", move_type="quiet",
                      phase="opening", drop_cp=200,
                      opening="French Defense Advance Paulsen Attack")
        _add_position(conn, game_id=3, epd="IT", structure="s", move_type="quiet",
                      phase="opening", drop_cp=200, opening="Italian Game")
        conn.commit()
        # Exact name matches just that one variant.
        assert _epds(trainer.select_positions(
            conn, username="alice",
            opening="French Defense Advance Paulsen Attack")) == ["A2"]
        # Word-contains catches every French Advance variant in one query...
        assert set(_epds(trainer.select_positions(
            conn, username="alice", opening_like="french advance"))) == {"A1", "A2"}
        # ...and the words may be non-adjacent in the name; 'french' alone is broader.
        assert len(trainer.select_positions(
            conn, username="alice", opening_like="french")) == 2

    @pytest.mark.spec("TRN-PATRN")
    def test_max_fullmove_caps_the_drill_to_the_early_opening(self, conn):
        """max_fullmove keeps only positions at or before that move number."""
        # Arrange: an early opening position and a deep one.
        _add_position(conn, game_id=1, epd="EARLY", structure="s", move_type="quiet",
                      phase="opening", drop_cp=200, fullmove=5)
        _add_position(conn, game_id=2, epd="DEEP", structure="s", move_type="quiet",
                      phase="opening", drop_cp=200, fullmove=14)
        conn.commit()
        # Act + Assert: capping at move 6 drops the move-14 position.
        assert _epds(trainer.select_positions(
            conn, username="alice", max_fullmove=6)) == ["EARLY"]


class TestRepeatedAndLength:
    """The 'repeated only' toggle and the drill-length cap (TRN-REPEAT, TRN-LEN)."""

    @pytest.mark.spec("TRN-REPEAT")
    def test_repeated_only_keeps_positions_blundered_more_than_once(self, drill_conn):
        """'repeated_only' keeps only positions blundered 2+ times across games."""
        # Act: only EPD1 was blundered in two games.
        got = trainer.select_positions(
            drill_conn, username="alice", repeated_only=True)
        # Assert.
        assert _epds(got) == ["EPD1"]

    @pytest.mark.spec("TRN-LEN")
    def test_drill_length_caps_the_number_of_positions(self, drill_conn):
        """`n` caps the drill length (using 'worst' for a deterministic pick)."""
        # Act.
        got = trainer.select_positions(
            drill_conn, n=2, mode="worst", username="alice")
        # Assert: the two biggest blunders only.
        assert _epds(got) == ["EPD2", "EPD3"]


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


class TestBearings:
    """The fixed 'get your bearings' pause before the clock starts."""

    @pytest.mark.spec("TRN-INTRO")
    def test_bearings_pause_highlights_the_opponent_last_move(self):
        """The pause carries the fixed delay and the opponent's last move."""
        # Act.
        b = tp._bearings_for({"opp_move": "e2e4"})
        # Assert.
        assert b["delayMs"] == tp._BEARINGS_MS
        assert b["lastMove"] == "e2e4"

    @pytest.mark.spec("TRN-INTRO")
    def test_bearings_pause_when_there_is_no_prior_move(self):
        """A first-move mistake still gets the pause, just nothing to highlight."""
        # Act.
        b = tp._bearings_for({})
        # Assert.
        assert b["delayMs"] == tp._BEARINGS_MS
        assert b["lastMove"] is None
