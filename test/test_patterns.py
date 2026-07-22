"""Dashboard analytics: summary counts and the termination breakdown."""

from collections import Counter

import pytest

from boardviz import patterns


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
