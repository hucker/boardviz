"""CCT heat-map scoring: 'nothing here' clears and per-cell completeness (TRN-CCT)."""

import chess
import pytest

from boardviz.ui import trainer_page as tp


def _counts(**sides):
    """Build a {me:{cat:n}, opp:{cat:n}} count dict; missing cats default to 0."""
    zero = {"checks": 0, "captures": 0, "threats": 0}
    return {s: {**zero, **sides.get(s, {})} for s in ("me", "opp")}


class TestNormalizeCleared:
    """A partial 'nothing here' payload fills to a full {side: {cat: bool}} map."""

    @pytest.mark.spec("TRN-CCT")
    def test_missing_entries_default_to_false(self):
        """Absent sides/cats become False; present truthy values become True."""
        actual = tp._normalize_cleared({"me": {"checks": True}})
        assert actual["me"]["checks"] is True
        assert actual["me"]["captures"] is False
        assert actual["opp"]["threats"] is False

    @pytest.mark.spec("TRN-CCT")
    def test_none_is_all_false(self):
        """No payload at all means nothing was cleared."""
        actual = tp._normalize_cleared(None)
        assert all(v is False for row in actual.values() for v in row.values())


class TestCctCells:
    """Per-(side, category) cell state under the clear rules."""

    @pytest.mark.spec("TRN-CCT")
    def test_found_all_items_is_complete(self):
        """A bucket with items, all found, is a green 'found' cell."""
        cells = tp._cct_cells(_counts(me={"checks": 2}), _counts(me={"checks": 2}),
                              tp._normalize_cleared(None), "me")
        cell = cells["me"]["checks"]
        assert cell["state"] == "found"
        assert cell["complete"] is True
        assert cell["frac"] == 1.0

    @pytest.mark.spec("TRN-CCT")
    def test_partial_find_is_missed(self):
        """Found some but not all → red 'missed', with the partial fraction."""
        cells = tp._cct_cells(_counts(me={"captures": 1}),
                              _counts(me={"captures": 2}),
                              tp._normalize_cleared(None), "me")
        cell = cells["me"]["captures"]
        assert cell["state"] == "missed"
        assert cell["complete"] is False
        assert cell["frac"] == 0.5

    @pytest.mark.spec("TRN-CCT")
    def test_empty_and_cleared_is_complete(self):
        """An empty bucket you declared 'nothing here' is a green 'cleared' cell."""
        cells = tp._cct_cells(_counts(), _counts(),
                              tp._normalize_cleared({"me": {"checks": True}}), "me")
        cell = cells["me"]["checks"]
        assert cell["state"] == "cleared"
        assert cell["complete"] is True
        assert cell["frac"] == 1.0

    @pytest.mark.spec("TRN-CCT")
    def test_empty_but_not_cleared_is_open(self):
        """An empty bucket left un-cleared earns nothing — the whole point."""
        cells = tp._cct_cells(_counts(), _counts(),
                              tp._normalize_cleared(None), "me")
        cell = cells["me"]["checks"]
        assert cell["state"] == "open"
        assert cell["complete"] is False
        assert cell["frac"] == 0.0

    @pytest.mark.spec("TRN-CCT")
    def test_false_clear_of_a_nonempty_bucket_is_missed(self):
        """Declaring 'nothing here' where there were items is graded wrong (red)."""
        cells = tp._cct_cells(_counts(me={"checks": 0}), _counts(me={"checks": 2}),
                              tp._normalize_cleared({"me": {"checks": True}}), "me")
        cell = cells["me"]["checks"]
        assert cell["state"] == "missed"
        assert cell["complete"] is False

    @pytest.mark.spec("TRN-CCT")
    def test_scope_limits_cells_to_the_drilled_side(self):
        """A single-side scope produces cells for that side only."""
        cells = tp._cct_cells(_counts(), _counts(),
                              tp._normalize_cleared(None), "opp")
        assert set(cells) == {"opp"}


class TestPositionScore:
    """The out-of-4 position score respects clears (empty needs clearing)."""

    @pytest.mark.spec("TRN-CCT")
    def test_all_found_plus_best_move_is_four(self):
        """Everything found and a best move → the full 4 points."""
        avail = _counts(me={"checks": 1, "captures": 1, "threats": 1})
        cells = tp._cct_cells(avail, avail, tp._normalize_cleared(None), "me")
        assert tp._cct_position_score(cells, 1.0) == 4.0

    @pytest.mark.spec("TRN-CCT")
    def test_uncleared_empty_buckets_cost_points(self):
        """Empty buckets left un-cleared score 0 for their category (not free)."""
        cells = tp._cct_cells(_counts(), _counts(),
                              tp._normalize_cleared(None), "me")
        # 3 categories all 'open' (0) + move 1.0 -> only the move scores.
        assert tp._cct_position_score(cells, 1.0) == 1.0

    @pytest.mark.spec("TRN-CCT")
    def test_clearing_all_empty_buckets_earns_full_category_credit(self):
        """Correctly clearing every empty bucket restores the 3 category points."""
        cleared = tp._normalize_cleared(
            {"me": {"checks": True, "captures": True, "threats": True}})
        cells = tp._cct_cells(_counts(), _counts(), cleared, "me")
        assert tp._cct_position_score(cells, 0.5) == 3.5


class TestAccumulateThreadsClears:
    """_accumulate_cct routes the clear payload into completeness."""

    @pytest.mark.spec("TRN-CCT")
    def test_empty_position_is_complete_only_when_cleared(self):
        """A bare K+K position (no forcing moves) is a clean scan only if cleared."""
        # Arrange: two kings — no checks, captures, or loose pieces either side.
        board = chess.Board("8/8/8/3k4/8/8/3K4/8 w - - 0 1")
        state_open = {"cct_scope": "both"}
        state_cleared = {"cct_scope": "both"}
        marked = {"checks": [], "captures": [], "threats": []}
        cats = ("checks", "captures", "threats")
        full_clear = {s: {c: True for c in cats} for s in ("me", "opp")}
        # Act
        open_complete, *_ = tp._accumulate_cct(
            state_open, board, marked, tp._normalize_cleared(None))
        cleared_complete, *_ = tp._accumulate_cct(
            state_cleared, board, marked, tp._normalize_cleared(full_clear))
        # Assert
        assert open_complete is False   # empty buckets left un-cleared
        assert cleared_complete is True  # every empty bucket declared clear
