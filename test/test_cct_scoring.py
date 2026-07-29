"""CCT heat-map scoring: per-cell completeness with empty-is-default (TRN-CCT)."""

import chess
import pytest

from boardviz import cct
from boardviz.ui import trainer_page as tp


def _counts(**sides):
    """Build a {me:{cat:n}, opp:{cat:n}} count dict; missing cats default to 0."""
    zero = {"checks": 0, "captures": 0, "threats": 0}
    return {s: {**zero, **sides.get(s, {})} for s in ("me", "opp")}


class TestCctCells:
    """Per-(side, category) cell state — found / missed / none."""

    @pytest.mark.spec("TRN-CCT")
    def test_found_all_items_is_complete(self):
        """A bucket with items, all found, is a green 'found' cell."""
        cells = tp._cct_cells(_counts(me={"checks": 2}), _counts(me={"checks": 2}),
                              "me")
        cell = cells["me"]["checks"]
        assert cell["state"] == "found"
        assert cell["complete"] is True
        assert cell["frac"] == 1.0

    @pytest.mark.spec("TRN-CCT")
    def test_partial_find_is_missed(self):
        """Found some but not all → red 'missed', with the partial fraction."""
        cells = tp._cct_cells(_counts(me={"captures": 1}),
                              _counts(me={"captures": 2}), "me")
        cell = cells["me"]["captures"]
        assert cell["state"] == "missed"
        assert cell["complete"] is False
        assert cell["frac"] == 0.5

    @pytest.mark.spec("TRN-CCT")
    def test_empty_bucket_is_trivially_complete(self):
        """Nothing to find is a neutral 'none' cell, complete with no action."""
        cells = tp._cct_cells(_counts(), _counts(), "me")
        cell = cells["me"]["checks"]
        assert cell["state"] == "none"
        assert cell["complete"] is True
        assert cell["frac"] == 1.0

    @pytest.mark.spec("TRN-CCT")
    def test_scope_limits_cells_to_the_drilled_side(self):
        """A single-side scope produces cells for that side only."""
        cells = tp._cct_cells(_counts(), _counts(), "opp")
        assert set(cells) == {"opp"}


class TestPositionScore:
    """The out-of-4 position score; empty categories are free (trivially found)."""

    @pytest.mark.spec("TRN-CCT")
    def test_all_found_plus_best_move_is_four(self):
        """Everything found and a best move → the full 4 points."""
        avail = _counts(me={"checks": 1, "captures": 1, "threats": 1})
        cells = tp._cct_cells(avail, avail, "me")
        assert tp._cct_position_score(cells, 1.0) == 4.0

    @pytest.mark.spec("TRN-CCT")
    def test_empty_categories_score_full_and_add_the_move(self):
        """A position with nothing to find scores its 3 categories plus the move."""
        cells = tp._cct_cells(_counts(), _counts(), "me")
        assert tp._cct_position_score(cells, 1.0) == 4.0
        assert tp._cct_position_score(cells, 0.0) == 3.0

    @pytest.mark.spec("TRN-CCT")
    def test_a_missed_category_costs_its_share(self):
        """Missing a side's captures drops that category's fraction (out of 4)."""
        found = _counts(me={"checks": 1, "captures": 0, "threats": 1})
        avail = _counts(me={"checks": 1, "captures": 2, "threats": 1})
        cells = tp._cct_cells(found, avail, "me")
        # checks 1.0 + captures 0.0 + loose 1.0 = 2.0 scan, + best move = 3.0.
        assert tp._cct_position_score(cells, 1.0) == 3.0


class TestScanLoad:
    """Engine-free scan-complexity metric for the difficulty filter (TRN-CCT)."""

    @pytest.mark.spec("TRN-CCT")
    def test_quiet_endgame_has_a_low_load(self):
        """A bare king-and-king endgame has no forcing items."""
        board = chess.Board("8/8/8/3k4/8/8/3K4/8 w - - 0 1")
        assert cct.scan_load(board) == 0

    @pytest.mark.spec("TRN-CCT")
    def test_load_counts_forcing_items_both_sides(self):
        """The load equals total checks + captures + loose across both sides."""
        board = chess.Board(
            "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 0 1")
        scan = cct.scan_both(board)
        expected = sum(len(scan[s][c]) for s in ("me", "opp")
                       for c in ("checks", "captures", "threats"))
        assert cct.scan_load(board) == expected
        assert cct.scan_load(board) > 0
