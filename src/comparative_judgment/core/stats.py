"""Standard errors, misfit and soft regions over a fitted scale.

This is where D2's reason for choosing Bradley-Terry over a sort is delivered:
inconsistency is reported as *graded misfit* rather than hidden inside a confident
total order. Three quantities, each of which can produce plausible numbers while
being quietly wrong, so each is stated precisely here.

**Standard errors come from the regularized information (D39).** The fit maximizes

    sum over decided comparisons of log sigma(theta_winner - theta_loser)
    + sum over findings of LAMBDA * log sigma(theta) + LAMBDA * log sigma(-theta)

-- the second line is D13's virtual opponent at the origin, which is what
``fit.py``'s MM update adds as ``wins + LAMBDA`` over ``2 * LAMBDA`` virtual
comparisons. Its negative Hessian is the information: ``p(1-p)`` per decided
comparison on the two findings' diagonal and minus that between them, plus
``2 * LAMBDA * s(1-s)`` on each diagonal, where ``s = sigma(theta)``. The prior
term makes the matrix positive definite whatever the comparison graph looks like,
so it is inverted directly: no pseudo-inverse, no reference finding to choose, and
every finding has a standard error. One nobody has compared sits at the origin
with information ``2 * LAMBDA * 0.25`` and a standard error of exactly 2.0, which
is the prior speaking and says so. The data-alone alternative is undefined for
such a finding, and at one that wins everything it describes an estimator D13
records as divergent.

**Misfit is infit and outfit (D37)**, over decided comparisons only, because a tie
is excluded from the fit and a residual needs the fit's expectation. For a decided
comparison whose observed outcome the fit gave probability ``p``, the squared
residual is ``(1-p)^2`` and its variance ``p(1-p)``, for both findings in it.
Infit is the ratio of their sums, so an informative comparison weighs more; outfit
is the mean of their ratio, ``(1-p)/p``, so one surprising result against a
distant finding moves it most. Both have expectation 1 under the model, but on
sparse data a consistent rater reads well below it -- three findings judged
consistently score about 0.2 -- so they are read by rank, not against 1.0.

**A triad on its own reads exactly 1.0**, because the model's best account of
A>B, B>C, C>A is three equal findings and three coin flips, which fits perfectly.
It is visible as misfit against its consistent counterpart, or wherever other
judgments place those findings apart, which is where the rater's scale is soft.

**Regions are equal-evidence windows (D38).** Each decided comparison is located at
the mean scale value of its two findings, and within each connected component the
comparisons are ordered by location and cut into consecutive groups of
``REGION_COMPARISONS``, so every region's misfit rests on the same amount of
evidence and a region judged twice cannot top the ranking on noise.

Every sum here runs in an order fixed by finding ids, never by the order the log
recorded judgments in, so a reordered log gives the same bits.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

import numpy as np
import numpy.typing as npt

from comparative_judgment.core.fit import LAMBDA, FitResult
from comparative_judgment.core.models import Comparison, CutName, SoftRegion

#: Decided comparisons per soft region (D38). A display granularity rather than a
#: threshold: it refuses nothing, and sets only how finely the scale is cut. Twenty
#: is enough for a region's infit to mean something and few enough that a fifty-
#: finding bootstrap of about 250 comparisons is cut into a dozen regions. Chosen,
#: not measured.
REGION_COMPARISONS: Final[int] = 20

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class ItemFit:
    """One finding's residual sums over its decided comparisons."""

    infit: float | None
    outfit: float | None


@dataclass(frozen=True, slots=True)
class _Decided:
    """A decided comparison, reduced to what misfit needs, in canonical order."""

    low_id: str
    high_id: str
    winner: str
    loser: str


def _sigmoid(value: float) -> float:
    """The logistic function, stable for large magnitudes of either sign."""
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def _decided(comparisons: Iterable[Comparison], known: Mapping[str, float]) -> list[_Decided]:
    """Decided comparisons between findings the fit holds, in an order fixed by ids.

    A comparison naming a finding the fit does not hold -- one whose removal was
    accepted -- is skipped, exactly as the fit skips it.
    """
    out: list[_Decided] = []
    for comparison in comparisons:
        pair = comparison.winner_loser()
        if pair is None:
            continue
        winner, loser = pair
        if winner not in known or loser not in known:
            continue
        low, high = sorted((winner, loser))
        out.append(_Decided(low_id=low, high_id=high, winner=winner, loser=loser))
    out.sort(key=lambda d: (d.low_id, d.high_id, d.winner))
    return out


def _observed_probability(decided: _Decided, theta: Mapping[str, float]) -> float:
    """The probability the fit gave the outcome that was recorded."""
    return _sigmoid(theta[decided.winner] - theta[decided.loser])


def information(fit_result: FitResult, comparisons: Iterable[Comparison]) -> FloatArray:
    """The regularized observed information at the fitted scale values.

    Rows and columns follow `fit_result.estimates`, which is in finding-id order.
    Only decided comparisons enter, as only they enter the fit; which finding won
    does not change a pair's contribution, since ``p(1-p)`` is symmetric.
    """
    ids = [estimate.finding_id for estimate in fit_result.estimates]
    index = {item: i for i, item in enumerate(ids)}
    theta = fit_result.theta()
    size = len(ids)
    matrix = np.zeros((size, size), dtype=np.float64)

    pair_counts: dict[tuple[int, int], int] = {}
    for decided in _decided(comparisons, theta):
        key = (index[decided.low_id], index[decided.high_id])
        pair_counts[key] = pair_counts.get(key, 0) + 1

    # Sorted, so the diagonal accumulates in an order fixed by ids.
    for (i, j), count in sorted(pair_counts.items()):
        p = _sigmoid(theta[ids[i]] - theta[ids[j]])
        weight = count * p * (1.0 - p)
        matrix[i, i] += weight
        matrix[j, j] += weight
        matrix[i, j] -= weight
        matrix[j, i] -= weight

    for i, item in enumerate(ids):
        s = _sigmoid(theta[item])
        matrix[i, i] += 2.0 * LAMBDA * s * (1.0 - s)
    return matrix


def covariance(fit_result: FitResult, comparisons: Iterable[Comparison]) -> FloatArray:
    """The inverse of the regularized information.

    Kept whole rather than reduced to its diagonal, because phase 3's stopping
    rule needs the variance of a finding's distance from a cut threshold, and a
    threshold is the mean of two anchors: that needs the covariances too. Returned
    as an array and never stored on a record, since an array field would make a
    frozen record neither comparable nor hashable.
    """
    matrix = information(fit_result, comparisons)
    if matrix.size == 0:
        return matrix
    inverse: FloatArray = np.linalg.inv(matrix)
    return inverse


def standard_errors(fit_result: FitResult, comparisons: Iterable[Comparison]) -> dict[str, float]:
    """Each finding's standard error, keyed by finding id."""
    inverse = covariance(fit_result, comparisons)
    ids = [estimate.finding_id for estimate in fit_result.estimates]
    diagonal = np.diagonal(inverse) if inverse.size else np.zeros(0, dtype=np.float64)
    return {item: math.sqrt(float(diagonal[i])) for i, item in enumerate(ids)}


def item_misfit(fit_result: FitResult, comparisons: Iterable[Comparison]) -> dict[str, ItemFit]:
    """Infit and outfit for every finding the fit holds (D37)."""
    theta = fit_result.theta()
    squared: dict[str, float] = {}
    variance: dict[str, float] = {}
    ratio: dict[str, float] = {}
    count: dict[str, int] = {}
    for decided in _decided(comparisons, theta):
        p = _observed_probability(decided, theta)
        residual_squared = (1.0 - p) ** 2
        weight = p * (1.0 - p)
        standardized = (1.0 - p) / p
        for item in (decided.winner, decided.loser):
            squared[item] = squared.get(item, 0.0) + residual_squared
            variance[item] = variance.get(item, 0.0) + weight
            ratio[item] = ratio.get(item, 0.0) + standardized
            count[item] = count.get(item, 0) + 1

    out: dict[str, ItemFit] = {}
    for estimate in fit_result.estimates:
        item = estimate.finding_id
        if item not in count:
            out[item] = ItemFit(infit=None, outfit=None)
            continue
        out[item] = ItemFit(infit=squared[item] / variance[item], outfit=ratio[item] / count[item])
    return out


def _group_bounds(total: int) -> list[tuple[int, int]]:
    """Half-open index ranges cutting `total` ordered comparisons into regions.

    Consecutive groups of `REGION_COMPARISONS`, with a trailing group smaller than
    that merged into the one before it, so every region holds at least that many
    unless the whole component holds fewer, in which case it is one region.
    """
    if total == 0:
        return []
    groups = max(1, total // REGION_COMPARISONS)
    bounds = [(k * REGION_COMPARISONS, (k + 1) * REGION_COMPARISONS) for k in range(groups - 1)]
    bounds.append(((groups - 1) * REGION_COMPARISONS, total))
    return bounds


def soft_regions(
    fit_result: FitResult,
    comparisons: Sequence[Comparison],
    components: Sequence[Sequence[str]],
    cut_thresholds: Sequence[tuple[CutName, str, str, float]] = (),
) -> tuple[SoftRegion, ...]:
    """The scale cut into equal-evidence regions, ranked by infit (D38).

    Regions are built within each component and never across two, because two
    groups no comparison joins have independent origins (D21). A tie is located
    like a decided comparison and counted in the region whose stretch of the scale
    holds it, when both its findings are in one component; a tie across two
    components enters only the overall tie rate. A cut is named in the region
    holding its threshold, within the component that holds its anchors.

    Each region's stretch runs from its first comparison's location up to the next
    region's first, the first region open below and the last open above, so every
    tie and every cut threshold in a component belongs to exactly one region.
    """
    theta = fit_result.theta()
    decided = _decided(comparisons, theta)
    ties = [
        (c.left_id, c.right_id)
        for c in comparisons
        if c.winner_loser() is None and c.left_id in theta and c.right_id in theta
    ]

    unranked: list[SoftRegion] = []
    for component_index, members in enumerate(components, start=1):
        inside = set(members)
        located = sorted(
            (
                ((theta[d.winner] + theta[d.loser]) / 2.0, d)
                for d in decided
                if d.winner in inside and d.loser in inside
            ),
            key=lambda entry: (entry[0], entry[1].low_id, entry[1].high_id, entry[1].winner),
        )
        bounds = _group_bounds(len(located))
        if not bounds:
            continue
        lows = [located[start][0] for start, _ in bounds]

        def region_of(location: float, lows: list[float] = lows) -> int:
            return max(0, bisect.bisect_right(lows, location) - 1)

        tie_counts = [0] * len(bounds)
        for left, right in ties:
            if left in inside and right in inside:
                tie_counts[region_of((theta[left] + theta[right]) / 2.0)] += 1

        cut_names: list[list[CutName]] = [[] for _ in bounds]
        for name, above, below, threshold in cut_thresholds:
            if above in inside and below in inside:
                cut_names[region_of(threshold)].append(name)

        for k, (start, stop) in enumerate(bounds):
            group = located[start:stop]
            squared = variance = ratio = 0.0
            involved: set[str] = set()
            for _, d in group:
                p = _observed_probability(d, theta)
                squared += (1.0 - p) ** 2
                variance += p * (1.0 - p)
                ratio += (1.0 - p) / p
                involved.update((d.winner, d.loser))
            size = len(group)
            unranked.append(
                SoftRegion(
                    rank=0,
                    component=component_index,
                    low=group[0][0],
                    high=group[-1][0],
                    comparisons=size,
                    ties=tie_counts[k],
                    tie_rate=tie_counts[k] / (size + tie_counts[k]),
                    infit=squared / variance,
                    outfit=ratio / size,
                    findings=tuple(sorted(involved, key=lambda item: (-theta[item], item))),
                    cuts=tuple(cut_names[k]),
                )
            )

    ordered = sorted(unranked, key=lambda r: (-r.infit, r.component, r.low))
    return tuple(replace(r, rank=rank) for rank, r in enumerate(ordered, start=1))
