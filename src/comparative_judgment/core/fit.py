"""The regularised Bradley-Terry fit.

Bradley-Terry gives each item a strength ``pi`` and says the probability that
*i* is judged more severe than *j* is ``pi_i / (pi_i + pi_j)``. Fitting it to a
set of pairwise outcomes recovers a scale. Two properties are why this model was
chosen over sorting the items: it never assumes a total order, so an inconsistent
rater is data rather than an error condition, and it needs no comparison that a
sort would have skipped.

Two implementation facts carry most of the risk in this module.

**Regularisation is required, not a refinement (D13).** The unregularised maximum
likelihood estimate *diverges* for any item that wins or loses all of its
comparisons. On a severity scale that is guaranteed rather than exceptional: the
most severe finding beats everything it meets and the least severe loses
everything. The failure is silent — the iteration drifts toward its cap and
returns large arbitrary numbers that look like data. Each item therefore gets
``LAMBDA`` pseudo-wins and ``LAMBDA`` pseudo-losses against a virtual opponent
fixed at the scale origin, which makes every estimate finite and, as a
side-effect, pins the origin so no separate normalisation step is needed.

**Determinism is engineered, not hoped for.** Items are sorted by id before any
array is built, so the summation order is fixed and identical judgments produce
identical floats regardless of the order they were recorded in. The tolerance and
the iteration cap are constants, and exceeding the cap raises rather than
returning whatever value had been reached — which would make the result depend on
the cap instead of on the judgments.

Standard errors are deliberately absent: nothing in this phase consumes them, and
they belong with the diagnostics that do.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from comparative_judgment.core.errors import FitDidNotConvergeError
from comparative_judgment.core.models import Comparison, Estimate

#: Regularisation strength: pseudo-wins and pseudo-losses per item against a
#: virtual opponent at the origin. Stated here and in the README rather than
#: buried, because it visibly compresses the extremes of the scale.
LAMBDA: Final[float] = 0.5

#: Convergence is on the largest absolute change in log-strength.
#:
#: Not stricter than the work requires. Placement compares an item's theta
#: against a threshold midway between two anchors, and theta values span order
#: 1-10, so agreement to a billionth is far finer than any band decision can
#: use. Reproducibility does not depend on this number: converging to *a* fixed
#: tolerance is what makes two runs agree, whatever the tolerance is. Tightening
#: it further only buys iterations, and this iteration converges linearly.
TOL: Final[float] = 1e-9

#: The cap exists to catch NON-convergence, not slowness, and it is set from
#: measurement rather than intuition.
#:
#: This iteration converges linearly, and its rate depends on how far apart the
#: strengths want to sit. Realistic input -- roughly ten appearances per item,
#: pairs chosen near in rank, a rater who is not perfectly consistent -- settles
#: in 500-700 iterations at n=50 and n=200 alike. The slow case is the opposite
#: of pathological: a *complete* graph with *zero* inconsistency, where every
#: item strictly beats every item below it and the scale wants to spread as wide
#: as the prior allows. Measured there: 8,708 iterations at n=50, 15,582 at
#: n=75, 23,544 at n=100 -- roughly linear in n.
#:
#: An earlier cap of 10,000 would therefore have REFUSED a perfectly consistent
#: 60-item batch: not bad data, the best data possible. Raising the cap is the
#: right fix rather than accelerating, because each iteration is now cheap
#: (sparse over pairs) and the cap is a guard against a fit that will never
#: settle, not a time budget. Reaching 200,000 takes a shape no hand-rated
#: corpus can produce -- a complete graph at n>500 needs over 125,000 human
#: comparisons.
MAX_ITER: Final[int] = 200_000

#: The virtual opponent sits at pi = 1, i.e. theta = 0.
_VIRTUAL_STRENGTH: Final[float] = 1.0


@dataclass(frozen=True, slots=True)
class FitResult:
    """A fitted scale.

    ``estimates`` is ordered by finding id, not by strength: callers that want a
    ranking sort explicitly, and a caller that accidentally relies on order gets
    a stable one rather than a coincidental one.
    """

    estimates: tuple[Estimate, ...]
    iterations: int
    ties: int
    regularisation: float

    def theta(self) -> dict[str, float]:
        return {e.finding_id: e.theta for e in self.estimates}

    def ranked(self) -> tuple[Estimate, ...]:
        """Most severe first; ties in strength broken by id for stability."""
        return tuple(sorted(self.estimates, key=lambda e: (-e.theta, e.finding_id)))


def fit(item_ids: Sequence[str], comparisons: Iterable[Comparison]) -> FitResult:
    """Fit strengths to comparisons over ``item_ids``.

    Items with no comparisons are included and land at the origin — the prior is
    all the information there is about them. Refusing to *band* such an item is a
    separate concern, handled where bands are assigned.
    """
    items = sorted(set(item_ids))
    index = {item: i for i, item in enumerate(items)}
    n = len(items)

    if n == 0:
        return FitResult(estimates=(), iterations=0, ties=0, regularisation=LAMBDA)

    wins = np.zeros(n, dtype=np.float64)
    losses = np.zeros(n, dtype=np.float64)
    ties = np.zeros(n, dtype=np.float64)
    # Sparse by pair, not a dense n-by-n matrix. Comparisons are few relative to
    # n^2 -- a thousand items judged ten thousand times touch at most a few
    # thousand distinct pairs -- and a dense matrix rebuilt every iteration is
    # what made a large corpus take tens of seconds rather than under one.
    pair_counts: dict[tuple[int, int], float] = {}
    tie_total = 0

    for comparison in comparisons:
        left, right = comparison.left_id, comparison.right_id
        if left not in index or right not in index:
            continue
        i, j = index[left], index[right]
        decided = comparison.winner_loser()
        if decided is None:
            tie_total += 1
            ties[i] += 1
            ties[j] += 1
            continue
        winner, loser = decided
        wins[index[winner]] += 1
        losses[index[loser]] += 1
        key = (i, j) if i < j else (j, i)
        pair_counts[key] = pair_counts.get(key, 0.0) + 1.0

    # Regularisation: LAMBDA wins and LAMBDA losses each, against the virtual
    # opponent. `virtual_counts` is the 2*LAMBDA comparisons that implies.
    regularised_wins = wins + LAMBDA
    virtual_counts = 2.0 * LAMBDA

    # Sorted so the summation order is fixed: identical judgments must produce
    # identical floats regardless of the order they were recorded in.
    ordered_pairs = sorted(pair_counts)
    left_idx = np.array([p[0] for p in ordered_pairs], dtype=np.intp)
    right_idx = np.array([p[1] for p in ordered_pairs], dtype=np.intp)
    pair_n = np.array([pair_counts[p] for p in ordered_pairs], dtype=np.float64)

    strength = np.ones(n, dtype=np.float64)
    iterations = 0
    for iterations in range(1, MAX_ITER + 1):  # noqa: B007 - the count is reported
        # Denominator of the MM (Zermelo) update: for each i, the sum over real
        # opponents of n_ij / (pi_i + pi_j), plus the virtual opponent's term.
        # Start from an explicit float64 array rather than from bincount's
        # return value. `np.bincount` yields int64 when its weights are EMPTY
        # and float64 otherwise, so a batch with no decided comparisons -- all
        # ties, or none yet -- would otherwise build an integer denominator and
        # fail on the first float addition. The dtype must not depend on how
        # much data happens to be present.
        denominator = np.zeros(n, dtype=np.float64)
        if left_idx.size:
            contribution = pair_n / (strength[left_idx] + strength[right_idx])
            denominator += np.bincount(left_idx, weights=contribution, minlength=n)
            denominator += np.bincount(right_idx, weights=contribution, minlength=n)
        denominator += virtual_counts / (strength + _VIRTUAL_STRENGTH)

        updated = regularised_wins / denominator
        shift = np.abs(np.log(updated) - np.log(strength)).max()
        strength = updated
        if shift < TOL:
            break
    else:
        msg = (
            f"the fit did not converge within {MAX_ITER} iterations; emitting nothing "
            "rather than a value that would depend on the cap instead of the judgments"
        )
        raise FitDidNotConvergeError(msg)

    theta = np.log(strength)
    estimates = tuple(
        Estimate(
            finding_id=item,
            theta=float(theta[i]),
            appearances=int(wins[i] + losses[i] + ties[i]),
            wins=int(wins[i]),
            losses=int(losses[i]),
            ties=int(ties[i]),
        )
        for i, item in enumerate(items)
    )
    return FitResult(
        estimates=estimates,
        iterations=iterations,
        ties=tie_total,
        regularisation=LAMBDA,
    )
