"""The session API — the only surface a front end may touch.

Everything a terminal or a browser needs is here, and nothing here knows which
of them is calling. That separation is the point (D3): a web adapter is expected
later, and three mistakes would make the terminal work throwaway when it arrives.
This module avoids all three.

*Session state lives here, not in a widget.* The most tempting error and the most
expensive one. A front end holds no cursor, no undo stack and no pending pair.

*Persistence and pair selection are behind this interface*, never called from a
key handler.

*The return values are structured, never formatted.* A terminal lays a
:class:`PairForReview` out as panes and a browser would render it as HTML.
Returning display strings from here is the subtlest way to bake a terminal
assumption into the core.

One consequence worth naming: **there is no stored cursor.** Which pair comes
next is *derived* from the log, so a session that resumes offers exactly the
comparison it would have offered had it never stopped — and there is no
"position" file that can disagree with the judgments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from comparative_judgment.core import bands, pairing
from comparative_judgment.core.errors import CutError, UnknownItemError
from comparative_judgment.core.fit import FitResult, fit
from comparative_judgment.core.models import (
    BandAssignment,
    Comparison,
    Cut,
    Estimate,
    Finding,
    Outcome,
    PairForReview,
    Progress,
    Retraction,
)
from comparative_judgment.core.store import Store

DEFAULT_SESSION_ID: Final[str] = "session"


@dataclass(frozen=True, slots=True)
class Placement:
    """The outcome of banding, including what could not be banded."""

    assignments: tuple[BandAssignment, ...]
    unplaced: tuple[str, ...]
    thresholds: tuple[float, ...]


class Session:
    """One rater working through a batch in one store."""

    def __init__(
        self,
        store: Store,
        *,
        rater_id: str,
        session_id: str = DEFAULT_SESSION_ID,
        appearance_target: int = pairing.APPEARANCE_TARGET,
    ) -> None:
        self._store = store
        self._rater_id = rater_id
        self._session_id = session_id
        self._target = appearance_target
        self._fit_cache: FitResult | None = None

    # -- derived state -----------------------------------------------------

    def _invalidate(self) -> None:
        self._fit_cache = None

    def _findings(self) -> tuple[Finding, ...]:
        return self._store.findings()

    def _fit(self) -> FitResult:
        """Fit lazily and cache until a judgment changes the log.

        Refitting per judgment is affordable at the scale a human can reach: the
        sparse iteration settles a fifty-item batch in single-digit
        milliseconds. It is deliberately not incremental — an incremental
        estimate that drifted from the batch fit would break the guarantee that
        the same log always yields the same scale.
        """
        if self._fit_cache is None:
            ids = [f.id for f in self._findings()]
            self._fit_cache = fit(ids, self._store.active_comparisons())
        return self._fit_cache

    def estimates(self) -> tuple[Estimate, ...]:
        return self._fit().estimates

    # -- the comparison loop -----------------------------------------------

    def next_pair(self) -> PairForReview | None:
        """The next comparison to put in front of the rater, or ``None`` when done.

        Two modes, chosen by whether cuts exist yet. Before them, the goal is a
        scale good enough to draw three boundaries on, so every item works toward
        an appearance target. After them, the goal is only which of four bands an
        item falls in — exact rank is precision that gets discarded — so a new
        finding is compared against anchors near the boundary it sits closest to
        and is done in about three comparisons rather than about nine.

        Derived, not stored. Two calls with no judgment between them return the
        same pair, and so does a call after a restart.
        """
        estimates = self.estimates()
        cuts = self._store.cuts()
        chosen = (
            self._placement_pair(estimates, cuts)
            if cuts
            else pairing.next_bootstrap_pair(
                estimates,
                target=self._target,
                already_seen=pairing.seen_pairs(self._store.active_comparisons()),
            )
        )
        if chosen is None:
            return None

        by_id = {f.id: f for f in self._findings()}
        appearances = {e.finding_id: e.appearances for e in estimates}
        left_id, right_id = chosen
        return PairForReview(
            left=by_id[left_id],
            right=by_id[right_id],
            comparisons_spent=len(self._store.active_comparisons()),
            appearance_target=self._target,
            appearances_left=appearances.get(left_id, 0),
            appearances_right=appearances.get(right_id, 0),
        )

    def _placement_pair(
        self, estimates: tuple[Estimate, ...], cuts: tuple[Cut, ...]
    ) -> tuple[str, str] | None:
        """Place whichever admitted finding is still short of its placement quota.

        Anchors are skipped: they define the boundaries, so they are already
        placed by construction. Items are taken in id order so a resumed session
        picks up exactly where it left off.
        """
        anchors = pairing.cut_anchor_ids(cuts)
        theta = {e.finding_id: e.theta for e in estimates}
        try:
            cut_values = bands.thresholds(cuts, theta)
        except (CutError, UnknownItemError):
            # A boundary that no longer means anything cannot guide placement.
            # Surfacing it belongs to `place()`, which reports it by name.
            return None

        for estimate in sorted(estimates, key=lambda e: e.finding_id):
            if estimate.finding_id in anchors:
                continue
            if estimate.appearances >= pairing.PLACEMENT_COMPARISONS:
                continue
            chosen = pairing.next_placement_pair(
                estimate.finding_id,
                estimates=estimates,
                thresholds=cut_values,
                anchors=anchors,
                comparisons_made=estimate.appearances,
            )
            if chosen is not None:
                return chosen
        return None

    def record(self, outcome: Outcome) -> Comparison:
        """Record a judgment on the pair :meth:`next_pair` would return.

        The judgment is on disk before this returns, because the caller advances
        immediately afterwards and a buffered comparison is one a crash discards
        with the rater none the wiser.
        """
        pending = self.next_pair()
        if pending is None:
            msg = "nothing left to judge in this batch"
            raise ValueError(msg)
        recorded = self._store.append_comparison(
            left_id=pending.left.id,
            right_id=pending.right.id,
            outcome=outcome,
            rater_id=self._rater_id,
            session_id=self._session_id,
        )
        self._invalidate()
        return recorded

    def undo(self) -> Retraction | None:
        """Withdraw the most recent judgment, or ``None`` if there is none.

        Appends a retraction rather than deleting. In a session of hundreds of
        keypresses a misfire is certain, and without this it would become a
        silently wrong datum in the log everything downstream derives from.
        """
        active = self._store.active_comparisons()
        if not active:
            return None
        latest = max(active, key=lambda c: c.seq)
        retraction = self._store.append_retraction(
            retracts_seq=latest.seq,
            rater_id=self._rater_id,
            session_id=self._session_id,
        )
        self._invalidate()
        return retraction

    # -- reporting ---------------------------------------------------------

    def progress(self) -> Progress:
        """Where the batch has got to, including the measured cost figures.

        ``mean_appearances`` is reported because the whole cost model rests on an
        estimate of roughly ten appearances per item. Measuring it is what turns
        that from a claim into a figure the first real batch confirms or refutes
        (D8).
        """
        estimates = self.estimates()
        active = self._store.active_comparisons()
        appearances = [e.appearances for e in estimates]
        below = tuple(sorted(e.finding_id for e in estimates if e.appearances < self._target))
        return Progress(
            admitted=len(estimates),
            excluded_questions=self._store.excluded_question_count(),
            comparisons_spent=len(active),
            ties=sum(1 for c in active if c.outcome is Outcome.TIE),
            min_appearances=min(appearances) if appearances else 0,
            mean_appearances=(sum(appearances) / len(appearances)) if appearances else 0.0,
            appearance_target=self._target,
            complete=not below,
            items_below_target=below,
        )

    def mean_comparisons_per_item(self) -> float:
        """Judgments spent per finding placed — the other half of the cost model."""
        estimates = self.estimates()
        if not estimates:
            return 0.0
        return len(self._store.active_comparisons()) / len(estimates)

    # -- bands -------------------------------------------------------------

    def place(self) -> Placement:
        """Assign bands using the cuts currently stored.

        Raises :class:`CutError` when the cuts are missing or have inverted,
        rather than banding against a boundary that no longer means anything.
        """
        cuts = self._store.cuts()
        if not cuts:
            msg = "no cuts have been set; there are no boundaries to band against"
            raise CutError(msg)
        estimates = self.estimates()
        theta = {e.finding_id: e.theta for e in estimates}
        return Placement(
            assignments=bands.assign_bands(estimates, cuts),
            unplaced=bands.unplaced(estimates),
            thresholds=bands.thresholds(cuts, theta),
        )
