"""Choosing which two findings to show next.

Two modes, because the question differs:

**Cold start** — no cuts exist yet, so the goal is a scale good enough to draw
three boundaries on. Pairs are chosen between items whose current estimates are
close, since a comparison between two obviously-different findings costs a
judgment and tells the fit almost nothing it did not already know. The phase ends
when every item reaches the appearance target (D11).

**Placement** — cuts exist, so the goal is not an item's exact rank but which of
four bands it falls in. Exact rank is precision that gets discarded: severity
output is "this is a High", never "this is the 287th-worst finding". Comparing
against the anchors of the *nearest* cut answers the band question in about three
comparisons where ranking would take about nine — the difference between roughly
3,000 and 9,000 judgments over a thousand findings.

Every choice here is deterministic. Ties in every ordering break on finding id,
so the same state always yields the same pair; a session that resumes offers the
same comparison it would have offered had it never stopped.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

from comparative_judgment.core.models import Comparison, Cut, Estimate

#: Appearances per item before the cold-start phase reports itself complete.
#: This IS the specification's own estimate rather than a separate guess, so the
#: first real batch either confirms it or shows it was wrong (D11, D8).
APPEARANCE_TARGET: Final[int] = 10

#: Comparisons against cut anchors before an item's band is read off. Three,
#: because three cuts bracket four bands and a binary walk needs two to locate
#: one, with the third buying confidence at the boundary.
PLACEMENT_COMPARISONS: Final[int] = 3


def seen_pairs(comparisons: Iterable[Comparison]) -> set[frozenset[str]]:
    """Unordered pairs already judged, so a rater is not asked twice needlessly."""
    return {frozenset((c.left_id, c.right_id)) for c in comparisons}


def _ordered(left: str, right: str) -> tuple[str, str]:
    """Present the pair in a stable order.

    Which side a finding appears on is arbitrary, and randomising it would make
    the same state produce different screens. Sorting by id keeps a resumed
    session identical to an uninterrupted one.
    """
    return (left, right) if left <= right else (right, left)


def next_bootstrap_pair(
    estimates: Sequence[Estimate],
    *,
    target: int = APPEARANCE_TARGET,
    already_seen: set[frozenset[str]] | None = None,
) -> tuple[str, str] | None:
    """The next cold-start comparison, or ``None`` when every item has its target.

    The neediest item goes first — fewest appearances, then lowest id — and its
    partner is whichever item sits closest to it on the current scale, preferring
    one that also still needs appearances and that it has not already met.
    """
    if len(estimates) < 2:
        return None

    seen = already_seen or set()
    below = [e for e in estimates if e.appearances < target]
    if not below:
        return None

    focus = min(below, key=lambda e: (e.appearances, e.finding_id))
    below_ids = {e.finding_id for e in below}

    def rank(candidate: Estimate) -> tuple[int, int, float, str]:
        # Sort key, most preferred first: not already compared with the focus;
        # still below target; nearest on the scale; then id, for stability.
        return (
            1 if frozenset((focus.finding_id, candidate.finding_id)) in seen else 0,
            0 if candidate.finding_id in below_ids else 1,
            abs(candidate.theta - focus.theta),
            candidate.finding_id,
        )

    candidates = [e for e in estimates if e.finding_id != focus.finding_id]
    if not candidates:
        return None
    partner = min(candidates, key=rank)
    return _ordered(focus.finding_id, partner.finding_id)


def cut_anchor_ids(cuts: Iterable[Cut]) -> tuple[str, ...]:
    """Every finding that defines a cut boundary, de-duplicated and ordered."""
    anchors: list[str] = []
    for cut in cuts:
        for anchor in (cut.above_id, cut.below_id):
            if anchor not in anchors:
                anchors.append(anchor)
    return tuple(sorted(anchors))


def next_placement_pair(
    item_id: str,
    *,
    estimates: Sequence[Estimate],
    thresholds: Sequence[float],
    anchors: Sequence[str],
    comparisons_made: int,
    required: int = PLACEMENT_COMPARISONS,
) -> tuple[str, str] | None:
    """The next comparison for placing one item, or ``None`` when it is placed.

    The partner is an anchor near the threshold the item currently sits closest
    to, which is where a further judgment can still change the answer. Comparing
    against an anchor three bands away would be easy for the rater and useless to
    the result.
    """
    if comparisons_made >= required or not thresholds or not anchors:
        return None

    by_id = {e.finding_id: e for e in estimates}
    if item_id not in by_id:
        return None
    theta = by_id[item_id].theta

    nearest_threshold = min(thresholds, key=lambda t: (abs(theta - t), t))
    available = [a for a in anchors if a != item_id and a in by_id]
    if not available:
        return None

    partner = min(
        available,
        key=lambda a: (abs(by_id[a].theta - nearest_threshold), a),
    )
    return _ordered(item_id, partner)
