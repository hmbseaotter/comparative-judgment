"""Fit tests.

The build prompt says to run these early rather than last, because a fit that is
subtly wrong returns plausible numbers rather than an error. Four properties
carry the risk:

* two fits over the same judgments agree byte for byte;
* the order judgments were recorded in does not change the answer;
* an item that wins or loses everything still gets a finite value;
* an intransitive rater is accepted rather than rejected.
"""

from __future__ import annotations

import itertools
import math
import random
import time

import pytest

from comparative_judgment.core.errors import FitDidNotConvergeError
from comparative_judgment.core.fit import LAMBDA, MAX_ITER, fit
from comparative_judgment.core.graph import components, is_connected
from comparative_judgment.core.models import Comparison, Outcome


def _cmp(seq: int, left: str, right: str, outcome: Outcome = Outcome.LEFT) -> Comparison:
    return Comparison(
        seq=seq,
        left_id=left,
        right_id=right,
        outcome=outcome,
        rater_id="r",
        session_id="s",
        timestamp="2026-08-28T12:00:00Z",
    )


def _ladder(names: list[str], repeats: int = 3) -> list[Comparison]:
    """Every pair judged consistently: earlier names are more severe."""
    out: list[Comparison] = []
    seq = 0
    for _ in range(repeats):
        for left, right in itertools.combinations(names, 2):
            seq += 1
            out.append(_cmp(seq, left, right, Outcome.LEFT))
    return out


class TestOrdering:
    def test_recovers_a_consistent_ordering(self) -> None:
        names = ["a", "b", "c", "d"]
        result = fit(names, _ladder(names))
        assert [e.finding_id for e in result.ranked()] == names

    def test_estimates_are_returned_sorted_by_id(self) -> None:
        names = ["d", "b", "a", "c"]
        result = fit(names, _ladder(sorted(names)))
        assert [e.finding_id for e in result.estimates] == ["a", "b", "c", "d"]


class TestDeterminism:
    def test_two_fits_agree_exactly(self) -> None:
        names = ["a", "b", "c", "d", "e"]
        judgments = _ladder(names)
        first, second = fit(names, judgments), fit(names, judgments)
        assert first.theta() == second.theta()
        assert first.iterations == second.iterations

    def test_insertion_order_does_not_change_the_answer(self) -> None:
        """The same judgments in a different order must give the same scale.

        This is the property a comparison sort could not offer: its result
        depends on the order pairs happened to be examined in.
        """
        names = ["a", "b", "c", "d", "e"]
        judgments = _ladder(names)
        shuffled = list(reversed(judgments))
        assert fit(names, judgments).theta() == fit(names, shuffled).theta()

    def test_item_order_does_not_change_the_answer(self) -> None:
        names = ["a", "b", "c", "d"]
        judgments = _ladder(names)
        assert fit(names, judgments).theta() == fit(list(reversed(names)), judgments).theta()


class TestRegularisation:
    """Without the prior these cases diverge. They are the normal case, not edges."""

    def test_an_item_that_wins_everything_is_finite(self) -> None:
        names = ["top", "mid", "low"]
        judgments = [
            _cmp(1, "top", "mid", Outcome.LEFT),
            _cmp(2, "top", "low", Outcome.LEFT),
            _cmp(3, "mid", "low", Outcome.LEFT),
        ]
        result = fit(names, judgments)
        top = result.theta()["top"]
        # isfinite, not an approx self-comparison: it says what is meant, and it
        # catches inf as well as nan. Unregularised, this value diverges.
        assert math.isfinite(top)
        assert abs(top) < 100.0

    def test_an_item_that_loses_everything_is_finite(self) -> None:
        names = ["top", "mid", "low"]
        judgments = [
            _cmp(1, "top", "low", Outcome.LEFT),
            _cmp(2, "mid", "low", Outcome.LEFT),
        ]
        result = fit(names, judgments)
        assert abs(result.theta()["low"]) < 100.0

    def test_converges_rather_than_hitting_the_cap(self) -> None:
        """Hitting the cap would mean drifting, which is the failure this prevents."""
        names = [f"f{i:02d}" for i in range(12)]
        result = fit(names, _ladder(names))
        assert result.iterations < MAX_ITER

    def test_a_perfectly_consistent_complete_batch_is_not_refused(self) -> None:
        """The slow case is the BEST data, not the worst.

        A complete graph with no inconsistency makes the scale spread as wide as
        the prior allows, which is what makes this iteration crawl. An earlier
        cap of 10,000 refused exactly this at around sixty items -- perfectly
        valid input, rejected for being too clean.
        """
        names = [f"f{i:03d}" for i in range(60)]
        result = fit(names, _ladder(names, repeats=1))
        assert result.iterations < MAX_ITER
        assert [e.finding_id for e in result.ranked()] == names

    def test_realistic_input_converges_quickly(self) -> None:
        """What the tool actually produces: sparse, near-in-rank, imperfect."""
        rng = random.Random(7)
        names = [f"f{i:03d}" for i in range(50)]
        judgments: list[Comparison] = []
        for seq in range(250):
            i = rng.randrange(len(names))
            j = min(len(names) - 1, max(0, i + rng.choice([-3, -2, -1, 1, 2, 3])))
            if i == j:
                continue
            noisy = rng.random() < 0.12
            outcome = Outcome.LEFT if (i < j) != noisy else Outcome.RIGHT
            judgments.append(_cmp(seq + 1, names[i], names[j], outcome))
        assert fit(names, judgments).iterations < 5000

    def test_an_item_with_no_comparisons_sits_at_the_origin(self) -> None:
        names = ["a", "b", "lonely"]
        result = fit(names, [_cmp(1, "a", "b", Outcome.LEFT)])
        assert result.theta()["lonely"] == pytest.approx(0.0, abs=1e-9)

    def test_the_strength_is_reported(self) -> None:
        assert fit(["a"], []).regularisation == LAMBDA


class TestNoDecidedComparisons:
    """The prior alone must still produce a usable fit.

    This case found a real bug: numpy's bincount returns int64 when its weights
    are empty and float64 otherwise, so the denominator's dtype depended on how
    much data happened to be present. With no decided pairs the fit built an
    integer array and failed on the first float addition.
    """

    def test_no_comparisons_at_all(self) -> None:
        result = fit(["a", "b", "c"], [])
        assert len(result.estimates) == 3
        assert all(e.theta == pytest.approx(0.0, abs=1e-9) for e in result.estimates)

    def test_only_ties(self) -> None:
        judgments = [_cmp(1, "a", "b", Outcome.TIE), _cmp(2, "b", "c", Outcome.TIE)]
        result = fit(["a", "b", "c"], judgments)
        assert result.ties == 2
        assert all(e.theta == pytest.approx(0.0, abs=1e-9) for e in result.estimates)

    def test_a_single_item(self) -> None:
        result = fit(["only"], [])
        assert result.estimates[0].theta == pytest.approx(0.0, abs=1e-9)


class TestIntransitivity:
    """A cycle is data, not an error. A sort could not even see most of them."""

    def test_a_cycle_is_accepted(self) -> None:
        judgments = [
            _cmp(1, "a", "b", Outcome.LEFT),
            _cmp(2, "b", "c", Outcome.LEFT),
            _cmp(3, "c", "a", Outcome.LEFT),
        ]
        result = fit(["a", "b", "c"], judgments)
        assert len(result.estimates) == 3

    def test_a_perfect_cycle_leaves_the_items_indistinguishable(self) -> None:
        # The phase-1 half of the intransitivity criterion (D17): the triad is
        # accepted rather than rejected, and the scale reports the items as
        # indistinguishable instead of inventing an order from arrival sequence.
        # The other half -- that misfit rises for these items -- is phase 2,
        # where the statistic itself lives.
        """Each wins once and loses once, so nothing separates them.

        The interesting output is that the scale says so, rather than inventing
        an order from the sequence the comparisons arrived in.
        """
        judgments = [
            _cmp(1, "a", "b", Outcome.LEFT),
            _cmp(2, "b", "c", Outcome.LEFT),
            _cmp(3, "c", "a", Outcome.LEFT),
        ]
        theta = fit(["a", "b", "c"], judgments).theta()
        assert max(theta.values()) - min(theta.values()) < 1e-6


class TestTies:
    def test_ties_are_counted_and_excluded_from_the_scale(self) -> None:
        decided = [_cmp(1, "a", "b", Outcome.LEFT)]
        with_tie = [*decided, _cmp(2, "a", "b", Outcome.TIE)]
        assert fit(["a", "b"], with_tie).ties == 1
        # The tie adds an appearance without moving either item.
        assert fit(["a", "b"], with_tie).theta() == fit(["a", "b"], decided).theta()

    def test_a_tie_still_counts_as_an_appearance(self) -> None:
        result = fit(["a", "b"], [_cmp(1, "a", "b", Outcome.TIE)])
        assert all(e.appearances == 1 for e in result.estimates)
        assert all(e.ties == 1 for e in result.estimates)


class TestConnectivity:
    def test_two_unjudged_groups_are_separate_components(self) -> None:
        judgments = [_cmp(1, "a", "b", Outcome.LEFT), _cmp(2, "c", "d", Outcome.LEFT)]
        assert components(["a", "b", "c", "d"], judgments) == (("a", "b"), ("c", "d"))
        assert not is_connected(["a", "b", "c", "d"], judgments)

    def test_a_bridging_comparison_joins_them(self) -> None:
        judgments = [
            _cmp(1, "a", "b", Outcome.LEFT),
            _cmp(2, "c", "d", Outcome.LEFT),
            _cmp(3, "b", "c", Outcome.LEFT),
        ]
        assert is_connected(["a", "b", "c", "d"], judgments)

    def test_a_tie_does_not_bridge(self) -> None:
        """A tie is real information the fit cannot use.

        Two groups joined only by ties are joined by nothing the estimate can
        see, so reporting them as comparable would present the prior as evidence.
        """
        judgments = [
            _cmp(1, "a", "b", Outcome.LEFT),
            _cmp(2, "c", "d", Outcome.LEFT),
            _cmp(3, "b", "c", Outcome.TIE),
        ]
        assert not is_connected(["a", "b", "c", "d"], judgments)

    def test_components_are_stable_across_input_order(self) -> None:
        judgments = [_cmp(1, "a", "b", Outcome.LEFT), _cmp(2, "c", "d", Outcome.LEFT)]
        assert components(["d", "c", "b", "a"], judgments) == components(
            ["a", "b", "c", "d"], list(reversed(judgments))
        )


class TestFailureModes:
    def test_empty_input_is_not_an_error(self) -> None:
        result = fit([], [])
        assert result.estimates == ()

    def test_comparisons_about_unknown_items_are_ignored(self) -> None:
        result = fit(["a", "b"], [_cmp(1, "a", "zzz", Outcome.LEFT)])
        assert {e.finding_id for e in result.estimates} == {"a", "b"}
        assert all(e.appearances == 0 for e in result.estimates)

    def test_non_convergence_raises_rather_than_returning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cap that is hit must raise, never return a drifting value."""
        monkeypatch.setattr("comparative_judgment.core.fit.MAX_ITER", 1)
        names = ["a", "b", "c", "d"]
        with pytest.raises(FitDidNotConvergeError, match="did not converge"):
            fit(names, _ladder(names))


class TestPerformance:
    def test_one_thousand_items_ten_thousand_comparisons_under_five_seconds(self) -> None:
        names = [f"f{i:04d}" for i in range(1000)]
        judgments: list[Comparison] = []
        for seq in range(10_000):
            i = seq % 999
            judgments.append(_cmp(seq + 1, names[i], names[i + 1], Outcome.LEFT))
        started = time.perf_counter()
        fit(names, judgments)
        assert time.perf_counter() - started < 5.0
