"""Forced-mate review: motif classification, chance detection, and the queries."""

import pytest

from chesstrain import db, mate, patterns


class TestMotifClassifier:
    """Classifying a forced mate by its final checkmate position (MATE-MOTIF)."""

    @pytest.mark.spec("MATE-MOTIF")
    def test_back_rank_needs_a_rank_check_not_just_an_edge_king(self):
        """A rook checking along the king's back rank is 'back-rank'."""
        # Arrange: king g8 boxed by its pawns, Re1-e8# checks along rank 8.
        fen = "6k1/5ppp/8/8/8/8/8/4R1K1 w - - 0 1"
        # Act + Assert.
        assert mate.classify_mate_motif(fen, ["e1e8"]) == "back-rank"

    @pytest.mark.spec("MATE-MOTIF")
    def test_adjacent_queen_mate_on_the_home_rank_is_not_back_rank(self):
        """Scholar's Qxf7# checks from f7 (adjacent), so it's a piece mate, not back-rank."""
        # Arrange.
        fen = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
        # Act + Assert.
        assert mate.classify_mate_motif(fen, ["h5f7"]) == "queen (edge)"

    @pytest.mark.spec("MATE-MOTIF")
    def test_smothered_knight_mate(self):
        """A knight mate with the king walled in by its own pieces is 'smothered'."""
        # Arrange: king h8 boxed by rook g8 + pawns g7/h7, Nh6-f7#.
        fen = "6rk/6pp/7N/8/8/8/8/6K1 w - - 0 1"
        # Act + Assert.
        assert mate.classify_mate_motif(fen, ["h6f7"]) == "smothered"

    @pytest.mark.spec("MATE-MOTIF")
    def test_line_that_does_not_mate_is_unknown(self):
        """A line that doesn't resolve to checkmate is tagged 'unknown', not mislabelled."""
        # Arrange: a legal but non-mating move.
        fen = "6k1/5ppp/8/8/8/8/8/4R1K1 w - - 0 1"
        # Act + Assert.
        assert mate.classify_mate_motif(fen, ["e1e2"]) == "unknown"


class TestChanceDetection:
    """Grouping stored move evals into finished/blown mate chances (MATE-DETECT)."""

    def _row(self, ply, before, after, epd="E", best="a1a2"):
        return {"ply": ply, "eval_cp_before": before, "eval_cp_after": after,
                "epd_before": epd, "best_uci": best}

    @pytest.mark.spec("MATE-DETECT")
    def test_a_held_mate_is_one_converted_chance_at_the_starting_distance(self):
        """M3→M2→M1 kept throughout is a single converted chance of distance 3."""
        # Arrange: a mate held from M3 to delivery.
        rows = [self._row(40, 2997, 2998, "E1", "key"),
                self._row(42, 2998, 2999),
                self._row(44, 2999, 3000)]
        # Act.
        chances = mate.detect_chances(rows)
        # Assert.
        assert len(chances) == 1
        assert (chances[0].distance, chances[0].converted) == (3, True)
        assert chances[0].key_uci == "key"
        assert chances[0].drop_ply is None

    @pytest.mark.spec("MATE-DETECT")
    def test_dropping_out_of_mate_marks_the_chance_blown(self):
        """A move that leaves mate range blows the chance and records the ply."""
        # Arrange: M2 held, then M1 thrown away.
        rows = [self._row(10, 2998, 2998, "X1", "keep"),
                self._row(12, 2999, 50)]
        # Act.
        chances = mate.detect_chances(rows)
        # Assert.
        assert len(chances) == 1
        assert chances[0].converted is False
        assert (chances[0].distance, chances[0].drop_ply) == (2, 12)

    @pytest.mark.spec("MATE-DETECT")
    def test_non_mate_positions_start_no_chance(self):
        """Ordinary evals never open a chance."""
        # Arrange + Act + Assert.
        rows = [self._row(1, 120, 90), self._row(3, -50, -80)]
        assert mate.detect_chances(rows) == []


class TestMateQueries:
    """Conversion-by-distance and the chances grid honour whose-side + filters."""

    @pytest.fixture
    def mate_db(self, conn):
        """One game with alice's M1 (converted), M1 (blown), M2 (converted), and an
        opponent M1 that whose-side filtering must exclude."""
        conn.execute("INSERT INTO games(id, game_uuid, username, is_me, end_time) "
                     "VALUES(1,'g1','alice',1,1000)")
        seeds = [(1, 1, 1, None), (1, 1, 0, 20), (1, 2, 1, None), (0, 1, 1, None)]
        for is_me, distance, converted, drop in seeds:
            db.insert_mate_chance(
                conn, 1, is_me=is_me, ply=10, fen="8/8/8/8/8/8/8/8 w - - 0 1",
                distance=distance, key_uci="a1a2", mate_pv=["a1a2"],
                motif="back-rank", converted=converted, drop_ply=drop, url="u")
        conn.commit()
        return conn

    @pytest.mark.spec("MATE-CONV")
    def test_conversion_by_distance_counts_finished_vs_blown(self, mate_db):
        """Per distance: my M1 is 1/2 finished (50%), my M2 is 1/1 (100%)."""
        # Act.
        conv = {c["distance"]: c
                for c in patterns.mate_conversion_by_distance(
                    mate_db, {"username": "alice"})}
        # Assert.
        assert (conv[1]["chances"], conv[1]["converted"], conv[1]["pct"]) == (2, 1, 50)
        assert (conv[2]["chances"], conv[2]["converted"], conv[2]["pct"]) == (1, 1, 100)

    @pytest.mark.spec("MATE-MOTIF")
    def test_conversion_by_motif_groups_finished_vs_blown(self, mate_db):
        """My three chances share the 'back-rank' motif: 2/3 finished (67%)."""
        # Act.
        by_motif = patterns.mate_conversion_by_motif(mate_db, {"username": "alice"})
        # Assert: one motif row, aggregated across distances.
        assert len(by_motif) == 1
        row = by_motif[0]
        assert (row["label"], row["chances"], row["converted"], row["pct"]) == (
            "back-rank", 3, 2, 67)

    @pytest.mark.spec("MATE-GRID", "MATE-FILT")
    def test_chances_grid_is_scoped_to_the_chosen_side(self, mate_db):
        """The grid lists my three chances, not the opponent's (is_me filter)."""
        # Act.
        mine = patterns.mate_chances_df(mate_db, {"username": "alice"}, is_me=1)
        theirs = patterns.mate_chances_df(mate_db, {"username": "alice"}, is_me=0)
        # Assert.
        assert len(mine) == 3
        assert len(theirs) == 1
