"""Turning a fitted scale into severity bands.

A cut is stored as the **two findings either side of it**, never as a number
(D12). The reason is that a pairwise scale has no absolute origin: a threshold
captured as a value means something only relative to the fit that produced it, so
after a refit the same number sits somewhere subtly different and items cross it
for reasons the rater never judged. Storing the pair preserves what was actually
decided — "the boundary sits between this finding and that one" — and its
threshold is recomputed as their midpoint at each fit.

The consequence worth stating: a refit can *invert* an anchor pair, meaning the
two findings a boundary was drawn between have swapped places on the scale. That
is reported rather than silently re-sorted. It means the scale changed materially
in that region, which is a thing a human should look at, not a thing to paper
over.

A refit can also move a pair **apart** without inverting it, and the findings
that come to lie between the anchors are banded by the midpoint rather than by
any judgment about the boundary. That is reported too, by `separation`, and
never refused (D34): an inverted boundary means nothing, a wide one still means
something, and how wide is too wide is the rater's call.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence

from comparative_judgment.core.errors import CutError, UnknownItemError
from comparative_judgment.core.models import (
    BANDS,
    CUT_ORDER,
    Band,
    BandAssignment,
    Cut,
    CutSeparation,
    Estimate,
)


def _cuts_by_name(cuts: Sequence[Cut]) -> dict[str, Cut]:
    by_name: dict[str, Cut] = {}
    for cut in cuts:
        if cut.name.value in by_name:
            msg = f"cut {cut.name.value!r} is defined more than once"
            raise CutError(msg)
        by_name[cut.name.value] = cut
    return by_name


def thresholds(cuts: Sequence[Cut], theta: Mapping[str, float]) -> tuple[float, ...]:
    """Recompute each cut's threshold as the midpoint of its anchor pair.

    Raises when a cut is missing, names an unknown finding, or has inverted —
    each of which makes the boundary meaningless rather than merely imprecise.
    """
    by_name = _cuts_by_name(cuts)
    missing = [name.value for name in CUT_ORDER if name.value not in by_name]
    if missing:
        msg = f"missing cut(s): {', '.join(missing)}"
        raise CutError(msg)

    values: list[float] = []
    for name in CUT_ORDER:
        cut = by_name[name.value]
        for anchor in (cut.above_id, cut.below_id):
            if anchor not in theta:
                msg = f"cut {name.value!r} names unknown finding {anchor!r}"
                raise UnknownItemError(msg)
        above, below = theta[cut.above_id], theta[cut.below_id]
        if above <= below:
            msg = (
                f"cut {name.value!r} has inverted: {cut.above_id!r} was placed above "
                f"{cut.below_id!r}, but the current fit puts it at or below. The scale "
                "changed materially in that region — reporting rather than re-sorting, "
                "because a boundary drawn between two findings means nothing once they "
                "have swapped."
            )
            raise CutError(msg)
        values.append((above + below) / 2.0)

    # Descending severity. A non-monotone set means two boundaries have crossed,
    # which no per-cut check would catch.
    for higher, lower in itertools.pairwise(values):
        if higher <= lower:
            msg = (
                "cut thresholds are not in descending severity order; two boundaries "
                "have crossed on the current fit"
            )
            raise CutError(msg)
    return tuple(values)


def separation(
    cuts: Sequence[Cut], assignments: Sequence[BandAssignment]
) -> tuple[CutSeparation, ...]:
    """Each cut's gap, and the banded findings strictly between its anchors.

    Refuses exactly what `thresholds` refuses -- a missing, unknown or inverted
    cut, or two boundaries that have crossed -- by calling it, so the two cannot
    disagree about which cuts are valid. It refuses nothing else: a finding
    between a cut's anchors is reported by name however many there are (D34).

    Takes assignments rather than estimates so the population is the banded one.
    An item nobody compared sits where the prior put it, and naming it between two
    anchors would report the prior as a position. Strictly between, so a finding
    level with an anchor is not counted; most severe first, with ties broken by id,
    so the order does not depend on how the assignments arrived.
    """
    theta = {assignment.finding_id: assignment.theta for assignment in assignments}
    thresholds(cuts, theta)
    by_name = _cuts_by_name(cuts)
    ordered = sorted(theta.items(), key=lambda item: (-item[1], item[0]))
    reports: list[CutSeparation] = []
    for name in CUT_ORDER:
        cut = by_name[name.value]
        above, below = theta[cut.above_id], theta[cut.below_id]
        reports.append(
            CutSeparation(
                name=name,
                above_id=cut.above_id,
                below_id=cut.below_id,
                gap=above - below,
                between=tuple(item for item, value in ordered if below < value < above),
            )
        )
    return tuple(reports)


def band_for(theta_value: float, cut_thresholds: Sequence[float]) -> Band:
    """Which band a scale value falls in.

    Walks the cuts from most severe down, so an item above the first threshold is
    Critical and one below the last is Low. A value exactly on a threshold falls
    to the *less* severe side: bands should not be inflated by a tie with a
    boundary the rater drew between two other findings.
    """
    for index, threshold in enumerate(cut_thresholds):
        if theta_value > threshold:
            return BANDS[index]
    return BANDS[len(cut_thresholds)]


def assign_bands(
    estimates: Sequence[Estimate],
    cut_values: Sequence[float],
) -> tuple[BandAssignment, ...]:
    """Band every item that has been compared at least once.

    An item with no comparisons sits at the origin purely because the prior put
    it there, and banding it would report the prior as a judgment. Those are
    skipped rather than defaulted, and the caller can see which by comparing
    counts.

    Takes thresholds rather than cuts because the caller already has to derive
    them — deriving them again here computed the same values twice per placement
    and gave a `CutError` two places to come from.
    """
    return tuple(
        BandAssignment(
            finding_id=e.finding_id,
            band=band_for(e.theta, cut_values),
            theta=e.theta,
        )
        for e in estimates
        if e.appearances > 0
    )


def unplaced(estimates: Sequence[Estimate]) -> tuple[str, ...]:
    """Items with no comparison behind them, which therefore get no band."""
    return tuple(sorted(e.finding_id for e in estimates if e.appearances == 0))
