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
