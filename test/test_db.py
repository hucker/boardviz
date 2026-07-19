"""DB schema round-trips: game filters, import persistence, and the grade cache."""

import json

import pytest

from chesstrain import db


class TestGameFilters:
    """query_games / where_in filter behaviour (backs the FLT requirements)."""

    @pytest.mark.spec("FLT-EMPTY")
    def test_where_in_builds_scalar_list_or_no_clause(self):
        """A scalar becomes '= ?', a list 'IN (...)', None/[] no filter at all."""
        assert db.where_in("c", None) == ("", [])
        assert db.where_in("c", []) == ("", [])  # empty list = no filter (all)
        assert db.where_in("c", "a") == ("c = ?", ["a"])
        assert db.where_in("c", ["a", "b"]) == ("c IN (?,?)", ["a", "b"])

    @pytest.mark.spec("FLT-EMPTY")
    def test_query_games_accepts_a_list_of_values(self, conn):
        """A list filter matches any of its values; an empty list matches all."""
        # Arrange: two wins, a loss, a draw.
        for i, outcome in enumerate(["win", "loss", "draw", "win"]):
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, outcome, end_time) "
                "VALUES(?,?,1,?,?)", (f"g{i}", "alice", outcome, 1000 + i))
        conn.commit()
        # Act + Assert.
        assert len(db.query_games(conn, outcome=["win", "draw"])) == 3  # 2 + 1
        assert len(db.query_games(conn, outcome="win")) == 2   # scalar still works
        assert len(db.query_games(conn, outcome=[])) == 4      # empty = all

    @pytest.mark.spec("FLT-DIMS")
    def test_query_games_filters_by_colour_result_and_time_control(self, conn, records):
        """Each exact-match dimension narrows the listing."""
        # Arrange: one imported white win at blitz.
        db.upsert_games(conn, records, "alice", is_me=True)
        # Act + Assert.
        assert len(db.query_games(conn, color="white")) == 1
        assert len(db.query_games(conn, color="black")) == 0
        assert len(db.query_games(conn, outcome="win")) == 1
        assert len(db.query_games(conn, tc_class="rapid")) == 0

    @pytest.mark.spec("FLT-DIMS")
    def test_query_games_opening_is_case_insensitive_substring(self, conn):
        """Opening filter matches a case-insensitive substring of the name."""
        # Arrange.
        for i, opening in enumerate(
                ["French Defense: Advance", "Sicilian Najdorf", "French Exchange"]):
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, outcome, opening, "
                "end_time, analyzed) VALUES(?,?,1,'win',?,?,0)",
                (f"g{i}", "alice", opening, 1000 + i))
        conn.commit()
        # Act + Assert.
        assert len(db.query_games(conn, opening="French")) == 2   # substring
        assert len(db.query_games(conn, opening="french")) == 2   # case-insensitive
        assert len(db.query_games(conn, opening="Sicilian")) == 1
        assert len(db.query_games(conn, opening="Caro-Kann")) == 0

    @pytest.mark.spec("FLT-DIMS")
    def test_query_games_filters_by_flagged_and_analysis_state(self, conn):
        """Flagged and analysis filters (and their combination) narrow correctly."""
        # Arrange: (flagged, analyzed) across four games.
        for i, (flagged, analyzed) in enumerate([(1, 1), (0, 1), (0, 0), (1, 0)]):
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, outcome, flagged, "
                "analyzed, end_time) VALUES(?,?,1,'loss',?,?,?)",
                (f"g{i}", "alice", flagged, analyzed, 1000 + i))
        conn.commit()
        # Act + Assert.
        assert len(db.query_games(conn, flagged=1)) == 2
        assert len(db.query_games(conn, flagged=0)) == 2
        assert len(db.query_games(conn, analyzed=1)) == 2
        assert len(db.query_games(conn, flagged=1, analyzed=1)) == 1

    @pytest.mark.spec("FLT-COMPOS")
    def test_active_filters_apply_together(self, conn):
        """Several filters compose — all must hold (they AND, not OR)."""
        # Arrange: vary colour / result / time control across four games.
        rows = [("white", "win", "blitz"), ("white", "loss", "blitz"),
                ("black", "win", "blitz"), ("white", "win", "rapid")]
        for i, (color, outcome, tc) in enumerate(rows):
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, my_color, outcome, "
                "tc_class, end_time) VALUES(?,?,1,?,?,?,?)",
                (f"g{i}", "alice", color, outcome, tc, 1000 + i))
        conn.commit()
        # Act: only the white blitz win satisfies all three.
        got = db.query_games(conn, color="white", outcome="win", tc_class="blitz")
        # Assert.
        assert len(got) == 1
        assert got[0]["game_uuid"] == "g0"

    @pytest.mark.spec("FLT-RECENT")
    def test_recent_games_scope_cuts_off_at_nth_most_recent(self, conn):
        """nth_recent_end_time + min_end_time scope the listing to the last N."""
        # Arrange: five games, end_time 1000..1004 (newest last).
        for i in range(5):
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, outcome, end_time) "
                "VALUES(?,?,1,'win',?)", (f"g{i}", "alice", 1000 + i))
        conn.commit()
        # Act + Assert.
        assert db.nth_recent_end_time(conn, "alice", 1) == 1004   # most recent
        assert db.nth_recent_end_time(conn, "alice", 3) == 1002   # 3rd most recent
        assert db.nth_recent_end_time(conn, "alice", 99) is None  # fewer than N
        assert len(db.query_games(conn, min_end_time=1002)) == 3  # last 3


class TestEndState:
    """The precomputed end-of-game snapshot (state, clocks, pieces) — IMP-ENDST."""

    def _two_move_game(self, conn, opp_eval_after):
        """A 1.e4 e5 game by 'alice' (White); the last ply is the opponent's.

        ``opp_eval_after`` is that final ply's eval from the *opponent's* POV, so
        store_end_state must flip it to mine. Returns the game id.
        """
        conn.execute(
            "INSERT INTO games(id, game_uuid, username, is_me, my_color, "
            "outcome, analyzed, end_time) "
            "VALUES(1,'g1','alice',1,'white','loss',1,1000)")
        start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"
        after_e4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3"
        db.insert_moves(conn, [
            {"game_id": 1, "ply": 1, "is_me": 1, "uci": "e2e4",
             "epd_before": start, "eval_cp_after": 30, "seconds_remaining": 170},
            {"game_id": 1, "ply": 2, "is_me": 0, "uci": "e7e5",
             "epd_before": after_e4, "eval_cp_after": opp_eval_after,
             "seconds_remaining": 160},
        ])
        conn.commit()
        return 1

    @pytest.mark.spec("IMP-ENDST")
    def test_store_flips_the_final_ply_to_my_pov_and_captures_context(self, conn):
        """A last ply that is the opponent's is negated to my POV; clocks/pieces land."""
        # Arrange: opponent is losing by 300 at the end, so I'm winning by 300.
        gid = self._two_move_game(conn, opp_eval_after=-300)
        # Act.
        db.store_end_state(conn, gid)
        # Assert.
        row = db.query_games(conn)[0]
        assert row["end_state"] == "winning"
        assert row["end_eval_cp"] == 300
        assert row["end_clock_me"] == 170     # my last move's remaining clock
        assert row["end_clock_opp"] == 160    # opponent's
        assert row["end_pieces"] == 30        # 32 on the board minus two kings

    @pytest.mark.spec("IMP-ENDST")
    def test_state_buckets_by_the_win_threshold(self, conn):
        """|eval| under the win threshold is 'even'; at or past it is winning/losing."""
        # Arrange: opponent barely ahead → I'm barely behind (within threshold).
        gid = self._two_move_game(conn, opp_eval_after=50)  # my POV: -50
        # Act.
        db.store_end_state(conn, gid)
        # Assert.
        assert db.query_games(conn)[0]["end_state"] == "even"

    @pytest.mark.spec("IMP-ENDST")
    def test_backfill_fills_analysed_games_missing_the_snapshot(self, conn):
        """Backfill populates analysed games with a null end_state, counting them."""
        # Arrange: an analysed game whose snapshot hasn't been computed yet.
        self._two_move_game(conn, opp_eval_after=-300)
        assert db.query_games(conn)[0]["end_state"] is None
        # Act.
        filled = db.backfill_end_state(conn)
        # Assert.
        assert filled == 1
        assert db.query_games(conn)[0]["end_state"] == "winning"
        assert db.backfill_end_state(conn) == 0  # nothing left to fill

    @pytest.mark.spec("FLT-DIMS")
    def test_query_games_filters_by_end_state(self, conn):
        """The end_state filter narrows the listing (and a list matches any)."""
        # Arrange: one game per end state.
        for i, state in enumerate(["winning", "even", "losing"]):
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, outcome, "
                "end_state, end_time) VALUES(?,?,1,'loss',?,?)",
                (f"g{i}", "alice", state, 1000 + i))
        conn.commit()
        # Act + Assert.
        assert len(db.query_games(conn, end_state="winning")) == 1
        assert len(db.query_games(conn, end_state=["winning", "losing"])) == 2
        assert len(db.query_games(conn, end_state=[])) == 3  # empty = all

    @pytest.mark.spec("FLT-DIMS")
    def test_classify_end_method_normalizes_the_termination_header(self):
        """The raw header maps to one method label; draws collapse to 'draw'."""
        assert db.classify_end_method("win", "alice won by checkmate") == "checkmate"
        assert db.classify_end_method("loss", "bob won on time") == "on time"
        assert db.classify_end_method("loss", "bob won by resignation") == "resignation"
        assert db.classify_end_method("draw", "Game drawn by agreement") == "draw"
        assert db.classify_end_method("win", "something odd") == "other"

    @pytest.mark.spec("FLT-DIMS")
    def test_query_games_filters_by_end_method(self, conn):
        """The end_method filter narrows the listing (and a list matches any)."""
        # Arrange: one game per termination method.
        for i, method in enumerate(["resignation", "checkmate", "on time"]):
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, outcome, "
                "end_method, end_time) VALUES(?,?,1,'loss',?,?)",
                (f"g{i}", "alice", method, 1000 + i))
        conn.commit()
        # Act + Assert.
        assert len(db.query_games(conn, end_method="resignation")) == 1
        assert len(db.query_games(conn, end_method=["resignation", "on time"])) == 2
        assert len(db.query_games(conn, end_method=[])) == 3  # empty = all


class TestClockFilter:
    """Low-clock-at-end filter — finds time scrambles (FLT-CLOCK)."""

    @pytest.fixture
    def clock_games(self, conn):
        """Three games: (uuid, base time control, my clock, opp clock) at the end."""
        for uuid, tc, me, opp in [
                ("g0", "180", 3, 90), ("g1", "180", 90, 2), ("g2", "600", 55, 400)]:
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, outcome, "
                "time_control, end_clock_me, end_clock_opp, end_time) "
                "VALUES(?,?,1,'loss',?,?,?,0)", (uuid, "alice", tc, me, opp))
        conn.commit()
        return conn

    @pytest.mark.spec("FLT-CLOCK")
    def test_absolute_cutoff_filters_by_whose_clock(self, clock_games):
        """An absolute cutoff keeps games whose chosen clock was under it."""
        conn = clock_games
        # Act + Assert: me low, opponent low, then either (empty = either).
        assert len(db.query_games(conn, clock={"who": ["me"], "seconds": 5})) == 1
        assert len(db.query_games(
            conn, clock={"who": ["opponent"], "seconds": 5})) == 1
        assert len(db.query_games(conn, clock={"who": [], "seconds": 5})) == 2

    @pytest.mark.spec("FLT-CLOCK")
    def test_fractional_cutoff_scales_to_the_time_control(self, clock_games):
        """A fractional cutoff is per-game: 10% of base is 18s at 180, 60s at 600."""
        # Arrange + Act: my clock under 10% of the base time control.
        got = db.query_games(clock_games, clock={"who": ["me"], "frac": 0.10})
        # Assert: g0 (3 < 18) and g2 (55 < 60) qualify; g1 (90) does not.
        assert {r["game_uuid"] for r in got} == {"g0", "g2"}


class TestTimeTroubleLosses:
    """The derived 'lost to the clock' reading of flags + low-clock resigns (FLT-TTL)."""

    @pytest.mark.spec("FLT-TTL")
    def test_lost_on_clock_needs_low_clock_and_a_lost_race(self):
        """Only a resignation with my clock low AND far behind counts (3 vs 60)."""
        assert db.lost_on_clock("resignation", 3, 60) is True    # lost the race
        assert db.lost_on_clock("resignation", 3, 4) is False    # mutual scramble
        assert db.lost_on_clock("resignation", 20, 300) is False  # not low enough
        assert db.lost_on_clock("checkmate", 3, 60) is False     # not a resignation
        assert db.lost_on_clock("resignation", None, 60) is False  # unknown clock

    @pytest.mark.spec("FLT-TTL")
    def test_time_trouble_filter_selects_flags_and_lost_race_resigns(self, conn):
        """The filter keeps flags and low-clock-race resigns, nothing else."""
        # Arrange: a flag, a lost-race resign, a mutual-scramble resign, a win.
        games = [
            ("flag", "loss", "on time", 0.0, 40.0),
            ("race", "loss", "resignation", 3.0, 60.0),
            ("scramble", "loss", "resignation", 3.0, 4.0),
            ("won_time", "win", "on time", 5.0, 0.0),
        ]
        for uuid, outcome, method, me, opp in games:
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, outcome, "
                "end_method, end_clock_me, end_clock_opp, end_time) "
                "VALUES(?,?,1,?,?,?,?,0)", (uuid, "alice", outcome, method, me, opp))
        conn.commit()
        # Act.
        got = {r["game_uuid"] for r in db.query_games(conn, time_trouble=True)}
        # Assert: the flag and the lost-race resign; not the scramble or the win.
        assert got == {"flag", "race"}


class TestImportPersistence:
    """Cheap re-import, incremental analysis flags, and run progress."""

    @pytest.mark.spec("IMP-DEDUP")
    def test_reimporting_the_same_game_inserts_nothing(self, conn, records):
        """A re-imported game isn't duplicated and keeps its analysed flag."""
        # Arrange + Act: import twice.
        first = db.upsert_games(conn, records, "alice", is_me=True)
        second = db.upsert_games(conn, records, "alice", is_me=True)
        # Assert.
        assert first == 1
        assert second == 0  # the cheap-reimport guarantee
        rows = db.query_games(conn, username="alice")
        assert len(rows) == 1
        assert rows[0]["eco"] == "C20"
        assert rows[0]["analyzed"] == 0

    @pytest.mark.spec("IMP-INCR")
    def test_marking_a_game_analysed_removes_it_from_pending(self, conn, records):
        """A game drops out of the unanalysed set once marked analysed."""
        # Arrange.
        db.upsert_games(conn, records, "alice", is_me=True)
        pending = db.unanalyzed_games(conn, "alice")
        assert len(pending) == 1
        # Act.
        db.mark_analyzed(conn, pending[0]["id"])
        # Assert.
        assert len(db.unanalyzed_games(conn, "alice")) == 0

    @pytest.mark.spec("IMP-BKGND")
    def test_import_run_row_tracks_progress(self, conn):
        """A run row records done/total/status for the UI to poll."""
        # Arrange + Act.
        rid = db.start_run(conn, "alice", "analyze", total=3, ts=1.0)
        db.update_run(conn, rid, done=2, ts=2.0)
        db.update_run(conn, rid, status="done", ts=3.0)
        # Assert.
        run = db.latest_run(conn, "alice", "analyze")
        assert run is not None
        assert run["done"] == 2
        assert run["status"] == "done"


class TestGradeCache:
    """The precomputed grade cache that lets the trainer score without an engine."""

    @pytest.mark.spec("NFR-FAST")
    def test_grade_cache_round_trips(self, conn):
        """Grades stored for a position read back intact (no engine at read time)."""
        # Arrange.
        grades = {"e2e4": 2, "d2d4": 1, "a2a3": -2}
        # Act.
        db.upsert_grade(conn, "EPDKEY", grades, "e2e4", 37, 12, ts=1.0)
        row = db.get_grade(conn, "EPDKEY")
        # Assert.
        assert row is not None
        assert json.loads(row["grades_json"]) == grades
        assert row["best_uci"] == "e2e4"
