"""Pairing and band-placement tests.

The properties that carry weight: pairing is deterministic (a resumed session
offers the same comparison it would have offered), placement costs about three
comparisons rather than about nine, and a cut behaves like the pair of findings
it is rather than like a number.
"""

from __future__ import annotations

import pytest

from comparative_judgment.core.bands import (
    assign_bands,
    band_for,
    thresholds,
    unplaced,
)
from comparative_judgment.core.errors import CutError, UnknownItemError
from comparative_judgment.core.models import Band, Comparison, Cut, CutName, Estimate, Outcome
from comparative_judgment.core.pairing import (
    APPEARANCE_TARGET,
    PLACEMENT_COMPARISONS,
    cut_anchor_ids,
    next_bootstrap_pair,
    next_placement_pair,
    seen_pairs,
)


def _est(fid: str, theta: float, appearances: int = 0) -> Estimate:
    return Estimate(
        finding_id=fid,
        theta=theta,
        appearances=appearances,
        wins=0,
        losses=0,
        ties=0,
    )


def _ladder_estimates(appearances: int = 0) -> list[Estimate]:
    """Five items spread evenly, most severe first."""
    return [_est(f"F-{i}", 2.0 - i, appearances) for i in range(5)]


THREE_CUTS = (
    Cut(CutName.CRITICAL_HIGH, "F-0", "F-1"),
    Cut(CutName.HIGH_MEDIUM, "F-1", "F-2"),
    Cut(CutName.MEDIUM_LOW, "F-2", "F-3"),
)


class TestBootstrapPairing:
    def test_returns_none_once_every_item_has_its_target(self) -> None:
        estimates = _ladder_estimates(appearances=APPEARANCE_TARGET)
        assert next_bootstrap_pair(estimates) is None

    def test_returns_none_below_two_items(self) -> None:
        assert next_bootstrap_pair([_est("only", 0.0)]) is None

    def test_picks_the_neediest_item_first(self) -> None:
        estimates = _ladder_estimates(appearances=5)
        estimates[3] = _est("F-3", -1.0, appearances=1)
        pair = next_bootstrap_pair(estimates)
        assert pair is not None
        assert "F-3" in pair

    def test_partners_with_the_nearest_item_on_the_scale(self) -> None:
        """A comparison between obviously-different findings is a wasted judgment."""
        estimates = _ladder_estimates(appearances=5)
        estimates[0] = _est("F-0", 2.0, appearances=0)
        pair = next_bootstrap_pair(estimates)
        assert pair == ("F-0", "F-1")

    def test_avoids_a_pair_already_judged(self) -> None:
        estimates = _ladder_estimates(appearances=5)
        estimates[0] = _est("F-0", 2.0, appearances=0)
        pair = next_bootstrap_pair(estimates, already_seen={frozenset({"F-0", "F-1"})})
        assert pair is not None
        assert set(pair) != {"F-0", "F-1"}

    def test_is_deterministic(self) -> None:
        """A resumed session must offer the comparison it would have offered."""
        estimates = _ladder_estimates(appearances=2)
        assert next_bootstrap_pair(estimates) == next_bootstrap_pair(estimates)

    def test_pair_order_is_stable(self) -> None:
        estimates = _ladder_estimates(appearances=2)
        pair = next_bootstrap_pair(estimates)
        assert pair is not None
        assert pair == tuple(sorted(pair))

    def test_seen_pairs_is_unordered(self) -> None:
        left = Comparison(1, "a", "b", Outcome.LEFT, "r", "s", "t")
        right = Comparison(2, "b", "a", Outcome.RIGHT, "r", "s", "t")
        assert seen_pairs([left, right]) == {frozenset({"a", "b"})}


class TestPlacementPairing:
    def test_stops_after_the_required_comparisons(self) -> None:
        """About three, not about nine — the whole scaling argument."""
        assert (
            next_placement_pair(
                "new",
                estimates=[*_ladder_estimates(5), _est("new", 0.4)],
                thresholds=(1.5, 0.5, -0.5),
                anchors=cut_anchor_ids(THREE_CUTS),
                comparisons_made=PLACEMENT_COMPARISONS,
            )
            is None
        )

    def test_partners_with_an_anchor_near_the_closest_boundary(self) -> None:
        """Comparing against an anchor three bands away is easy and useless."""
        estimates = [*_ladder_estimates(5), _est("new", 0.45)]
        pair = next_placement_pair(
            "new",
            estimates=estimates,
            thresholds=(1.5, 0.5, -0.5),
            anchors=cut_anchor_ids(THREE_CUTS),
            comparisons_made=0,
        )
        assert pair is not None
        # 0.5 is the High/Medium boundary; F-1 (1.0) and F-2 (0.0) define it.
        assert set(pair) & {"F-1", "F-2"}

    def test_returns_none_for_an_unknown_item(self) -> None:
        assert (
            next_placement_pair(
                "ghost",
                estimates=_ladder_estimates(5),
                thresholds=(1.5,),
                anchors=("F-0",),
                comparisons_made=0,
            )
            is None
        )

    def test_cut_anchor_ids_deduplicates(self) -> None:
        assert cut_anchor_ids(THREE_CUTS) == ("F-0", "F-1", "F-2", "F-3")


class TestThresholds:
    def test_threshold_is_the_midpoint_of_the_anchor_pair(self) -> None:
        theta = {"F-0": 2.0, "F-1": 1.0, "F-2": 0.0, "F-3": -1.0}
        assert thresholds(THREE_CUTS, theta) == (1.5, 0.5, -0.5)

    def test_thresholds_move_with_the_scale(self) -> None:
        """A cut is not a number: refitting moves the boundary with its anchors.

        This is the whole point of storing the pair. A stored threshold would
        have stayed put while every item moved around it.
        """
        shifted = {"F-0": 4.0, "F-1": 2.0, "F-2": 0.0, "F-3": -2.0}
        assert thresholds(THREE_CUTS, shifted) == (3.0, 1.0, -1.0)

    def test_an_inverted_anchor_pair_is_reported_by_name(self) -> None:
        theta = {"F-0": 2.0, "F-1": 1.0, "F-2": -1.0, "F-3": 0.0}  # F-2 now below F-3
        with pytest.raises(CutError, match="medium_low"):
            thresholds(THREE_CUTS, theta)

    def test_a_missing_cut_is_reported(self) -> None:
        theta = {"F-0": 2.0, "F-1": 1.0, "F-2": 0.0, "F-3": -1.0}
        with pytest.raises(CutError, match="medium_low"):
            thresholds(THREE_CUTS[:2], theta)

    def test_a_duplicate_cut_is_reported(self) -> None:
        theta = {"F-0": 2.0, "F-1": 1.0, "F-2": 0.0, "F-3": -1.0}
        with pytest.raises(CutError, match="more than once"):
            thresholds((*THREE_CUTS, THREE_CUTS[0]), theta)

    def test_an_unknown_anchor_is_reported(self) -> None:
        with pytest.raises(UnknownItemError, match="F-3"):
            thresholds(THREE_CUTS, {"F-0": 2.0, "F-1": 1.0, "F-2": 0.0})


class TestBandAssignment:
    CUT_VALUES = (1.5, 0.5, -0.5)

    @pytest.mark.parametrize(
        ("theta", "expected"),
        [
            (3.0, Band.CRITICAL),
            (1.6, Band.CRITICAL),
            (1.0, Band.HIGH),
            (0.6, Band.HIGH),
            (0.0, Band.MEDIUM),
            (-0.4, Band.MEDIUM),
            (-1.0, Band.LOW),
        ],
    )
    def test_bands_follow_the_thresholds(self, theta: float, expected: Band) -> None:
        assert band_for(theta, self.CUT_VALUES) == expected

    def test_a_value_exactly_on_a_boundary_falls_to_the_milder_side(self) -> None:
        """Boundaries drawn between two other findings should not inflate a band."""
        assert band_for(1.5, self.CUT_VALUES) == Band.HIGH
        assert band_for(-0.5, self.CUT_VALUES) == Band.LOW

    def test_every_compared_item_gets_a_band(self) -> None:
        estimates = _ladder_estimates(appearances=4)
        assigned = assign_bands(estimates, THREE_CUTS)
        assert len(assigned) == len(estimates)

    def test_an_item_with_no_comparisons_gets_no_band(self) -> None:
        """The prior put it at the origin; banding it would report the prior."""
        estimates = [*_ladder_estimates(appearances=4), _est("never-judged", 0.0, 0)]
        assigned = assign_bands(estimates, THREE_CUTS)
        assert "never-judged" not in {a.finding_id for a in assigned}
        assert unplaced(estimates) == ("never-judged",)
