"""Trainer: position selection (modes, filters, dedup) and the replay (intro)."""

import chess
import pytest

from chesstrain import cct, trainer
from chesstrain.ui import trainer_page as tp


def _add_position(conn, *, game_id, epd, structure, move_type, phase, drop_cp,
                  username="alice", ply=10, opening=None, fullmove=None,
                  solve_depth=None):
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
        "depth, created_ts, solve_depth) VALUES(?,'{}','d2d4',0,12,1.0,?)",
        (epd, solve_depth))


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

    @pytest.mark.spec("TRN-DIFF")
    def test_difficulty_filter_keeps_only_the_harder_finds(self, conn):
        """min_solve_depth drops positions whose best move surfaces too shallow."""
        # Arrange: an obvious find (depth 3) and a hard one (depth 9).
        _add_position(conn, game_id=1, epd="EASY", structure="s", move_type="quiet",
                      phase="middlegame", drop_cp=200, solve_depth=3)
        _add_position(conn, game_id=2, epd="HARD", structure="s", move_type="quiet",
                      phase="middlegame", drop_cp=200, solve_depth=9)
        conn.commit()
        # Act + Assert: requiring depth >= 6 keeps only the hard one.
        assert _epds(trainer.select_positions(
            conn, username="alice", min_solve_depth=6)) == ["HARD"]

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


class TestCommitMove:
    """Scoring/recording an answered move, shared by the puzzle and CCT beats."""

    @pytest.mark.spec("TRN-SCORE")
    def test_commit_scores_and_records_the_attempt(self, conn, monkeypatch):
        """A best move scores +1, updates the running total, and writes an attempt."""
        # Arrange: st.rerun would abort outside a live script — stub it out.
        monkeypatch.setattr(tp.st, "rerun", lambda: None)
        board = chess.Board()
        state = {"total": 0.0, "answered": 0}
        pos = {"grades": {"e2e4": 2}, "epd": "EPD1", "tc_class": "blitz"}
        # Act.
        tp._commit_move(conn, pos, board, state, {"uci": "e2e4", "ms": 1500})
        # Assert: scored, tallied, and persisted.
        assert state["result"]["final_score"] == 1.0
        assert state["result"]["san"] == "e4"
        assert (state["total"], state["answered"]) == (1.0, 1)
        row = conn.execute(
            "SELECT grade, final_score FROM attempts WHERE epd='EPD1'").fetchone()
        assert tuple(row) == (2, 1.0)

    @pytest.mark.spec("TRN-CCT")
    def test_commit_carries_the_cct_marks(self, conn, monkeypatch):
        """A CCT beat's per-layer marks ride along on the result."""
        # Arrange.
        monkeypatch.setattr(tp.st, "rerun", lambda: None)
        marked = {"checks": ["e2e4"], "captures": [], "threats": ["d5"]}
        pos = {"grades": {"e2e4": 2}, "epd": "EPD2", "tc_class": "blitz"}
        state = {}
        # Act.
        tp._commit_move(conn, pos, chess.Board(), state,
                        {"uci": "e2e4", "ms": 100, "marked": marked})
        # Assert.
        assert state["result"]["marked"] == marked


class TestCctCounts:
    """Scoring the per-layer marks against the true both-ways sets (TRN-CCT)."""

    @pytest.mark.spec("TRN-CCT")
    def test_counts_correct_marks_and_ignores_wrong_ones(self):
        """A correct check and threat count; a wrong check mark does not."""
        # Arrange: White Ra1 checks via a1a8 (open 8th); White Bg1 wins Nd4 (loose).
        board = chess.Board("4k3/8/8/8/3n4/8/8/R3K1B1 w - - 0 1")
        scan = cct.scan_both(board)
        # a1a8 is a real check; d4 a real threat; a1a5 is not a check (wrong mark).
        marked = {"checks": ["a1a8", "a1a5"], "captures": [], "threats": ["d4"]}
        # Act.
        found = tp._cct_counts(board, marked, scan)
        # Assert.
        assert found["me"]["checks"] == 1
        assert found["me"]["threats"] == 1
        assert found["me"]["captures"] == 0


class TestScanSummary:
    """The 'found / available' review line for the both-ways scan (TRN-CCT)."""

    @pytest.mark.spec("TRN-CCT")
    def test_summary_reports_finds_over_totals_for_both_sides(self):
        """Each side shows correct-marks over the true set sizes."""
        # Arrange: a scan with known set sizes and a partial found tally.
        scan = {"me": {"checks": {"a1a2", "b1b2"}, "captures": {"c1c2"},
                       "threats": {"d4"}},
                "opp": {"checks": set(), "captures": {"e7e5"}, "threats": set()}}
        found = {"me": {"checks": 1, "captures": 1, "threats": 0},
                 "opp": {"checks": 0, "captures": 1, "threats": 0}}
        # Act.
        line = tp._scan_summary(found, scan)
        # Assert: a prominent missed headline, and misses flagged per category.
        assert "You missed 2 of 5 marks" in line
        assert "1/2 checks :red[(1 missed)]" in line
        assert "0/1 threats :red[(1 missed)]" in line
        assert "1/1 captures" in line and "captures :red" not in line

    @pytest.mark.spec("TRN-CCT")
    def test_summary_celebrates_a_clean_scan(self):
        """Marking everything gives a positive, miss-free headline."""
        # Arrange: found == available on every side/category.
        scan = {"me": {"checks": {"a1a2"}, "captures": set(), "threats": {"d4"}},
                "opp": {"checks": set(), "captures": set(), "threats": set()}}
        found = {"me": {"checks": 1, "captures": 0, "threats": 1},
                 "opp": {"checks": 0, "captures": 0, "threats": 0}}
        # Act.
        line = tp._scan_summary(found, scan)
        # Assert.
        assert "Clean scan — you marked all 2." in line
        assert "missed" not in line


class TestSideIndicator:
    """The prominent 'which colour am I playing' banner (TRN-INTRO)."""

    @pytest.mark.spec("TRN-INTRO")
    def test_side_line_names_white_when_white_is_to_move(self):
        """The opening position (White to move) is labelled White."""
        # Act.
        line = tp._side_line(chess.Board())
        # Assert.
        assert "White" in line and "Black" not in line

    @pytest.mark.spec("TRN-INTRO")
    def test_side_line_names_black_when_black_is_to_move(self):
        """A black-to-move position is labelled Black."""
        # Arrange: same start position but Black to move.
        board = chess.Board(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1")
        # Act.
        line = tp._side_line(board)
        # Assert.
        assert "Black" in line and "White" not in line


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
