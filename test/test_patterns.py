"""Dashboard analytics: summary counts and the termination breakdown."""

from collections import Counter

import pytest

from boardviz import patterns


class TestClockTrajectory:
    """Clock-remaining-by-move, split by how the game ended (REV-THINK)."""

    @pytest.mark.spec("REV-THINK")
    def test_splits_by_outcome_and_normalizes_to_base_time(self, conn):
        """% remaining is seconds/base, grouped won / lost-on-board / lost-on-time."""
        # Arrange: a 10-minute (600s) win, a 600s flag loss, and a draw (dropped).
        for gid, outcome, flagged in ((1, "win", 0), (2, "loss", 1), (3, "draw", 0)):
            conn.execute(
                "INSERT INTO games(id, game_uuid, username, outcome, flagged, "
                "time_control) VALUES(?,?,?,?,?,'600')",
                (gid, f"g{gid}", "a", outcome, flagged))
        # is_me moves with a recorded clock at moves 10 and 20.
        for gid, fm, rem in ((1, 10, 300), (1, 20, 150), (2, 10, 60), (2, 20, 30),
                             (3, 10, 300)):
            conn.execute(
                "INSERT INTO moves(game_id, ply, fullmove, is_me, seconds_remaining)"
                " VALUES(?,?,?,1,?)", (gid, fm * 2, fm, rem))
        conn.commit()
        # Act.
        ct = patterns.clock_trajectory(conn, {"username": "a"}, is_me=1)
        got = {(r.outcome_group, r.move): r.pct_remaining for r in ct.itertuples()}
        # Assert: 300/600=50%, 150/600=25%; the flag loss 10%/5%; draw excluded.
        assert got[("won", 10)] == 50.0
        assert got[("won", 20)] == 25.0
        assert got[("lost on time", 10)] == 10.0
        assert got[("lost on time", 20)] == 5.0
        assert "lost on board" not in set(ct["outcome_group"])  # none in fixture
        assert not any(g == "draw" for g in ct["outcome_group"])  # draws dropped

    @pytest.mark.spec("REV-THINK")
    def test_empty_when_no_clock_data(self, conn):
        """No timed games / no recorded clock yields an empty tidy frame."""
        got = patterns.clock_trajectory(conn, {"username": "nobody"}, is_me=1)
        assert list(got.columns) == ["move", "outcome_group", "pct_remaining", "n"]
        assert got.empty

    @pytest.mark.spec("REV-SIDE")
    def test_clock_by_move_averages_all_games_without_an_outcome_split(self, conn):
        """The opponent reference line averages every game per move, no split."""
        # Arrange: two 600s games (any outcomes) with a clock at moves 10 and 20.
        for gid, outcome, flagged in ((1, "win", 0), (2, "loss", 1)):
            conn.execute(
                "INSERT INTO games(id, game_uuid, username, outcome, flagged, "
                "time_control) VALUES(?,?,'a',?,?,'600')", (gid, f"g{gid}", outcome,
                                                            flagged))
        for gid, fm, rem in ((1, 10, 300), (1, 20, 150), (2, 10, 60), (2, 20, 30)):
            conn.execute(
                "INSERT INTO moves(game_id, ply, fullmove, is_me, seconds_remaining)"
                " VALUES(?,?,?,1,?)", (gid, fm * 2, fm, rem))
        conn.commit()
        # Act.
        got = patterns.clock_by_move(conn, {"username": "a"}, is_me=1)
        by_move = dict(zip(got["move"], got["pct_remaining"]))
        # Assert: move 10 = mean(300,60)/600 = 30%; move 20 = mean(150,30)/600 = 15%.
        assert list(got.columns) == ["move", "pct_remaining", "n"]
        assert by_move[10] == 30.0
        assert by_move[20] == 15.0


class TestMoveTime:
    """Move-time distribution and median-pace-by-outcome (REV-THINK)."""

    @staticmethod
    def _seed(conn, rows):
        """rows: (game_id, outcome, flagged, [seconds_spent...])."""
        for gid, outcome, flagged, times in rows:
            conn.execute(
                "INSERT INTO games(id, game_uuid, username, outcome, flagged) "
                "VALUES(?,?,'a',?,?)", (gid, f"g{gid}", outcome, flagged))
            for ply, sec in enumerate(times, start=1):
                conn.execute(
                    "INSERT INTO moves(game_id, ply, is_me, seconds_spent) "
                    "VALUES(?,?,1,?)", (gid, ply, sec))
        conn.commit()

    @pytest.mark.spec("REV-THINK")
    def test_hist_bins_and_caps_the_tail(self, conn):
        """Times bin by 1s; anything at/above the cap lands in the top bin."""
        self._seed(conn, [(1, "win", 0, [0.5, 1.5, 2.5, 25.0])])
        h = patterns.move_time_hist(conn, {"username": "a"}, is_me=1,
                                    bin_s=1.0, cap_s=20.0)
        got = dict(zip(h["bin_start"], h["moves"]))
        assert got == {0.0: 1, 1.0: 1, 2.0: 1, 20.0: 1}  # 25s capped into 20+

    @pytest.mark.spec("REV-THINK")
    def test_pace_reports_median_and_p95_by_outcome(self, conn):
        """Median and p95 seconds/move split by won / lost-board / lost-time."""
        self._seed(conn, [(1, "win", 0, [2.0, 4.0]),        # won: median 3.0
                          (2, "loss", 1, [3.0, 5.0])])       # lost on time: 4.0
        pace = patterns.move_time_pace(conn, {"username": "a"}, is_me=1)
        med = dict(zip(pace["outcome_group"], pace["median_s"]))
        assert med["won"] == 3.0
        assert med["lost on time"] == 4.0
        assert "draw" not in med


class TestSummaries:
    """Top-line counts and ECO-name resolution feeding the dashboard/filters."""

    @pytest.mark.spec("DASH-COUNT", "FLT-EMPTY")
    def test_summary_counts_accept_a_list_filter(self, conn):
        """A list outcome filter flows through _where as an IN clause."""
        # Arrange.
        for i, outcome in enumerate(["win", "loss", "draw", "win"]):
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, outcome, end_time) "
                "VALUES(?,?,1,?,?)", (f"g{i}", "alice", outcome, 1000 + i))
        conn.commit()
        # Act + Assert.
        assert patterns.summary_counts(conn, {"outcome": ["win", "draw"]})["games"] == 3
        assert patterns.summary_counts(conn, {"outcome": "win"})["games"] == 2

    @pytest.mark.spec("DASH-FILT")
    def test_summary_counts_obey_the_active_filter(self, conn):
        """The dashboard's headline counts narrow to the filtered games."""
        # Arrange: two white games (a win, a loss) and one black win.
        rows = [("white", "win"), ("white", "loss"), ("black", "win")]
        for i, (color, outcome) in enumerate(rows):
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, my_color, outcome, "
                "end_time) VALUES(?,?,1,?,?,?)",
                (f"g{i}", "alice", color, outcome, 1000 + i))
        conn.commit()
        # Act.
        unfiltered = patterns.summary_counts(conn, {"username": "alice"})
        white = patterns.summary_counts(
            conn, {"username": "alice", "my_color": "white"})
        # Assert: the colour filter scopes the counts down.
        assert unfiltered["games"] == 3
        assert white["games"] == 2
        assert white["wins"] == 1
        assert white["losses"] == 1

    @pytest.mark.spec("FLT-DIMS")
    def test_eco_opening_names_picks_the_most_common_name(self, conn):
        """Each ECO code resolves to its most-frequent opening name."""
        # Arrange: C20 seen twice as "King's Pawn", once as "Bongcloud".
        rows = [("C20", "King's Pawn"), ("C20", "King's Pawn"),
                ("C20", "Bongcloud"), ("B01", "Scandinavian")]
        for i, (eco, opening) in enumerate(rows):
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, outcome, eco, "
                "opening, end_time) VALUES(?,?,1,'win',?,?,?)",
                (f"g{i}", "alice", eco, opening, 1000 + i))
        conn.commit()
        # Act.
        names = patterns.eco_opening_names(conn)
        # Assert.
        assert names["C20"] == "King's Pawn"  # most common wins the tie-break
        assert names["B01"] == "Scandinavian"


class TestTerminationChart:
    """How-games-end classification, incl. the resign winning/losing split."""

    @pytest.mark.spec("DASH-TERM")
    def test_classify_termination_maps_outcome_and_method(self):
        """The raw termination header maps to (outcome, method)."""
        assert patterns.classify_termination("draw", "Game drawn") == ("draw", "draw")
        assert patterns.classify_termination(
            "win", "alice won by checkmate") == ("win", "checkmate")
        assert patterns.classify_termination(
            "loss", "bob won on time") == ("loss", "on time")
        assert patterns.classify_termination(
            "win", "bob won by resignation") == ("win", "resignation")

    @pytest.mark.spec("DASH-TERM")
    def test_resign_bucket_flips_eval_to_the_resigner_pov(self):
        """A resignation splits winning/losing by the resigner's final eval."""
        bucket = patterns._resign_bucket
        # I resigned (loss); last ply was my opponent (is_me=0). Their -300 means
        # I stood +300 — I threw a win.
        assert bucket("loss", -300, 0) == "resign while winning"
        assert bucket("loss", 500, 0) == "resign while losing"
        # Opponent resigned (win); last ply was mine (is_me=1). My +400 => they
        # were lost => a normal win.
        assert bucket("win", 400, 1) == "resign while losing"
        assert bucket("win", -300, 1) == "resign while winning"
        # No analysis -> no eval.
        assert bucket("loss", None, None) == "resign (unclear)"

    @pytest.mark.spec("DASH-TERM")
    def test_termination_breakdown_splits_resignations(self, conn):
        """Resignations refine into winning/losing across win and loss rows."""
        # Arrange: three resignations with a final-ply eval each.
        def add(uuid, outcome, term, last_eval, last_is_me):
            conn.execute(
                "INSERT INTO games(game_uuid, username, is_me, outcome, "
                "termination, end_time, analyzed) VALUES(?,?,1,?,?,?,1)",
                (uuid, "alice", outcome, term, 1000))
            gid = conn.execute(
                "SELECT id FROM games WHERE game_uuid=?", (uuid,)).fetchone()["id"]
            conn.execute(
                "INSERT INTO moves(game_id, ply, is_me, eval_cp_after) "
                "VALUES(?,?,?,?)", (gid, 30, last_is_me, last_eval))

        add("r1", "loss", "opp won by resignation", -300, 0)  # resigned a won game
        add("r2", "loss", "opp won by resignation", 400, 0)   # resigned a lost game
        add("r3", "win", "opp won by resignation", 250, 1)    # a normal win
        conn.commit()
        # Act: sum counts per method across win/loss rows.
        methods: Counter = Counter()
        for row in patterns.termination_breakdown(conn, {}):
            methods[row["method"]] += row["count"]
        # Assert.
        assert methods["resign while winning"] == 1
        assert methods["resign while losing"] == 2
        assert "resignation" not in methods  # coarse bucket fully refined
