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

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from comparative_judgment.core import bands, graph, pairing, severity
from comparative_judgment.core.errors import (
    CutError,
    DisconnectedComparisonsError,
    NothingToJudgeError,
    UnknownItemError,
)
from comparative_judgment.core.findings import load_findings
from comparative_judgment.core.fit import FitResult, fit
from comparative_judgment.core.models import (
    CUT_ORDER,
    BandAssignment,
    Comparison,
    Cut,
    CutName,
    CutSeparation,
    Estimate,
    Finding,
    Outcome,
    PairForReview,
    Progress,
    Removal,
    RemovalAccepted,
    Retraction,
    Revision,
    RevisionAccepted,
)
from comparative_judgment.core.store import Store

DEFAULT_SESSION_ID: Final[str] = "session"

#: Re-exported so a front end can name the default without importing `pairing`.
#: The seam is the whole contract: a presentation layer that reaches into a core
#: module for one constant is a presentation layer that will reach in for a
#: function next.
DEFAULT_APPEARANCE_TARGET: Final[int] = pairing.APPEARANCE_TARGET


@dataclass(frozen=True, slots=True)
class Placement:
    """The outcome of banding, including what could not be banded."""

    assignments: tuple[BandAssignment, ...]
    unplaced: tuple[str, ...]
    thresholds: tuple[float, ...]
    #: Each cut's gap and the findings between its anchors on the fit that banded
    #: them (D34): how a reader tells a band drawn between neighbors from one
    #: drawn across a widened gap.
    separation: tuple[CutSeparation, ...]


@dataclass(frozen=True, slots=True)
class LoadOutcome:
    """What a load did, or refused to do.

    Structured rather than printed, like everything else crossing this seam. The
    *policy* — refuse a changed or vanished judged finding unless a human accepts
    it — lives here; only the wording of the refusal belongs to a front end.
    """

    applied: bool
    admitted: int
    excluded_questions: tuple[str, ...]
    pending_revisions: tuple[Revision, ...]
    pending_removals: tuple[Removal, ...]
    accepted_revisions: tuple[RevisionAccepted, ...]
    accepted_removals: tuple[RemovalAccepted, ...]


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

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        rater_id: str,
        session_id: str = DEFAULT_SESSION_ID,
        appearance_target: int = pairing.APPEARANCE_TARGET,
    ) -> Session:
        """Open the store at `path` and start a session on it.

        Here rather than in a front end so that opening a store is not the one
        operation every presentation layer has to know how to do for itself. A
        web adapter gets the same one-line entry the terminal has.
        """
        return cls(
            Store.open(path),
            rater_id=rater_id,
            session_id=session_id,
            appearance_target=appearance_target,
        )

    @classmethod
    def create(
        cls,
        path: Path,
        *,
        rater_id: str,
        force: bool = False,
    ) -> Session:
        """Create a store at `path`, refusing to overwrite an existing one."""
        return cls(Store.create(path, force=force), rater_id=rater_id)

    @property
    def store_path(self) -> Path:
        return self._store.path

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
            raise NothingToJudgeError(msg)
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
        cuts = self._store.cuts()
        appearances = [e.appearances for e in estimates]
        below = tuple(sorted(e.finding_id for e in estimates if e.appearances < self._target))
        unplaced = self._unplaced(estimates, cuts)
        blocked = self._blocked_reason(estimates, cuts)
        placing = bool(cuts)
        separation = () if blocked else self._separation(estimates, cuts)
        return Progress(
            admitted=len(estimates),
            excluded_questions=self._store.excluded_question_count(),
            comparisons_spent=len(active),
            ties=sum(1 for c in active if c.outcome is Outcome.TIE),
            min_appearances=min(appearances) if appearances else 0,
            mean_appearances=(sum(appearances) / len(appearances)) if appearances else 0.0,
            appearance_target=self._target,
            complete=not blocked and not (unplaced if placing else below),
            items_below_target=below,
            items_unplaced=unplaced,
            placing=placing,
            blocked_reason=blocked,
            separation=separation,
        )

    def _unplaced(self, estimates: tuple[Estimate, ...], cuts: tuple[Cut, ...]) -> tuple[str, ...]:
        """Non-anchor findings still short of the placement quota.

        Empty before cuts exist, because placement is not what the loop is doing
        yet -- which is why `progress()` can call it unconditionally.

        Deliberately the same predicate `_placement_pair` selects on, so that an
        item named here is exactly an item that loop would still offer. The two
        disagreed: `complete` was measured against the *appearance* target long
        after that target stopped being the finishing condition, so a newcomer
        placed in its three comparisons -- with `next_pair()` returning `None`
        and the item banded -- was reported as an unfinished batch. The TUI
        compensated by reading `placing`; `cj status` did not, and printed
        `complete no` beside a name it had already placed.

        Anchors are excluded for the reason the placement loop excludes them:
        they define the boundaries, so they are placed by construction.
        """
        if not cuts:
            return ()
        anchors = pairing.cut_anchor_ids(cuts)
        return tuple(
            sorted(
                e.finding_id
                for e in estimates
                if e.finding_id not in anchors and e.appearances < pairing.PLACEMENT_COMPARISONS
            )
        )

    def _blocked_reason(self, estimates: tuple[Estimate, ...], cuts: tuple[Cut, ...]) -> str:
        """Why there is nothing to judge, when the reason is not "it is finished".

        `next_pair()` returns `None` for two unrelated reasons, and a front end
        reading only that renders the wrong one: an inverted cut leaves the
        placement path with no boundary to aim at, and the honest-looking display
        for `None` is "batch complete". Derived here rather than stored, like
        everything else on this seam.
        """
        if not cuts:
            return ""
        theta = {e.finding_id: e.theta for e in estimates}
        try:
            bands.thresholds(cuts, theta)
        except (CutError, UnknownItemError) as exc:
            return str(exc)
        return ""

    def _separation(
        self, estimates: tuple[Estimate, ...], cuts: tuple[Cut, ...]
    ) -> tuple[CutSeparation, ...]:
        """Each cut's gap and the findings between its anchors, for `progress`.

        Called once `_blocked_reason` has come back empty. Banded against the
        thresholds first, because `separation` reads the banded population -- the
        one `place` and the severity file report. Progress refuses nothing, so a
        cut whose anchor has no live comparison, which `place` would refuse, is
        reported here as no separation rather than raised.
        """
        if not cuts:
            return ()
        theta = {e.finding_id: e.theta for e in estimates}
        try:
            assignments = bands.assign_bands(estimates, bands.thresholds(cuts, theta))
            return bands.separation(cuts, assignments)
        except (CutError, UnknownItemError):
            return ()

    def mean_comparisons_per_item(self) -> float:
        """Judgments spent per finding *placed* — the other half of the cost model.

        The denominator counts items with at least one appearance, not every
        admitted finding. Dividing by the whole batch is a different figure: the
        number is read precisely when a batch is part-way through, which is
        exactly when untouched items are in the denominator deflating it, and a
        rater checking "am I near the three the spec predicts?" would be told yes
        by arithmetic rather than by measurement.
        """
        placed = sum(1 for e in self.estimates() if e.appearances > 0)
        if not placed:
            return 0.0
        return len(self._store.active_comparisons()) / placed

    # -- bands -------------------------------------------------------------

    def place(self) -> Placement:
        """Assign bands using the cuts currently stored.

        Raises :class:`CutError` when the cuts are missing or have inverted, and
        :class:`DisconnectedComparisonsError` when the judged items fall into
        groups never compared against each other — rather than banding against a
        boundary that no longer means anything, or across a gap no judgment
        spans.
        """
        cuts = self._store.cuts()
        if not cuts:
            msg = "no cuts have been set; there are no boundaries to band against"
            raise CutError(msg)
        estimates = self.estimates()
        self.require_connected()
        theta = {e.finding_id: e.theta for e in estimates}
        cut_values = bands.thresholds(cuts, theta)
        assignments = bands.assign_bands(estimates, cut_values)
        return Placement(
            assignments=assignments,
            unplaced=bands.unplaced(estimates),
            thresholds=cut_values,
            separation=bands.separation(cuts, assignments),
        )

    # -- the operations either front end needs -----------------------------

    def fit(self) -> FitResult:
        """The current fit, structured. Public because a front end reports it."""
        return self._fit()

    def load(
        self,
        findings_path: Path,
        *,
        accept_revisions: bool = False,
        accept_removals: bool = False,
    ) -> LoadOutcome:
        """Re-read the findings document into the store.

        Refuses, writing nothing, when a *judged* finding has changed text or has
        left the document, unless the corresponding acceptance is passed. Both
        acceptances are appended to the same log as comparisons, so an acceptance
        changes the log hash and a severity file naming that hash is tied to a
        history that includes it (D18).
        """
        result = load_findings(findings_path)
        revisions = self._store.pending_revisions(result.admitted)
        removals = self._store.pending_removals(result.admitted)
        blocked = (revisions and not accept_revisions) or (removals and not accept_removals)
        if blocked:
            return LoadOutcome(
                applied=False,
                admitted=len(result.admitted),
                excluded_questions=result.excluded_questions,
                pending_revisions=revisions,
                pending_removals=removals,
                accepted_revisions=(),
                accepted_removals=(),
            )

        accepted_r = tuple(
            self._store.append_revision(r, rater_id=self._rater_id, session_id="load")
            for r in revisions
        )
        accepted_x = tuple(
            self._store.append_removal(x, rater_id=self._rater_id, session_id="load")
            for x in removals
        )
        self._store.put_findings(result.admitted)
        self._store.put_load_summary(result.excluded_questions)
        self._invalidate()
        return LoadOutcome(
            applied=True,
            admitted=len(result.admitted),
            excluded_questions=result.excluded_questions,
            pending_revisions=(),
            pending_removals=(),
            accepted_revisions=accepted_r,
            accepted_removals=accepted_x,
        )

    def set_cuts(self, cuts: Sequence[Cut]) -> tuple[float, ...]:
        """Store the three band cuts, after proving they mean something.

        Everything is checked before anything is written. The previous order —
        write, then validate — left `cuts.json` holding a boundary the tool had
        just refused, in a store whose failure model says an unrecoverable error
        writes nothing.
        """
        if len(cuts) != len(CUT_ORDER):
            msg = f"expected {len(CUT_ORDER)} cuts, got {len(cuts)}"
            raise CutError(msg)

        top = next((c for c in cuts if c.name is CutName.CRITICAL_HIGH), None)
        if top is None:
            msg = f"no {CutName.CRITICAL_HIGH.value} cut supplied"
            raise CutError(msg)
        if not top.calibration_note.strip():
            msg = (
                "the top cut needs a calibration note naming the written consequence "
                "definition it was drawn against. A pairwise scale has no origin: the "
                "ordering can be internally perfect while the whole set sits a band too "
                "high, and nothing downstream would record that it does"
            )
            raise CutError(msg)

        estimates = self.estimates()
        self.require_connected()
        appearances = {e.finding_id: e.appearances for e in estimates}
        for cut in cuts:
            for anchor_id in (cut.above_id, cut.below_id):
                if appearances.get(anchor_id, 0) == 0:
                    msg = (
                        f"cut {cut.name.value!r} names {anchor_id!r}, which has no "
                        "comparisons. Its position is the prior's, not a judgment's, so a "
                        "boundary drawn there reports the prior"
                    )
                    raise UnknownItemError(msg)

        theta = {e.finding_id: e.theta for e in estimates}
        values = bands.thresholds(tuple(cuts), theta)
        self._store.put_cuts(cuts)
        self._invalidate()
        return values

    def cuts(self) -> tuple[Cut, ...]:
        return self._store.cuts()

    def export(self, path: Path) -> Placement:
        """Write the severity file, refusing whatever `place` refuses."""
        placement = self.place()
        severity.write_severity_file(
            path,
            assignments=placement.assignments,
            store=self._store,
            unplaced=placement.unplaced,
            cuts=self.cuts(),
        )
        return placement

    # -- connectivity ------------------------------------------------------

    def components(self) -> tuple[tuple[str, ...], ...]:
        """The judged items, grouped by whether a comparison path links them.

        Unjudged items are excluded. They are isolated by definition, and
        counting them here would report every part-way batch as disconnected.
        """
        judged = [e.finding_id for e in self.estimates() if e.appearances > 0]
        return graph.components(judged, self._store.active_comparisons())

    def require_connected(self) -> None:
        """Refuse to report values across groups with no comparison between them.

        Bradley-Terry estimates *differences*: two groups never compared against
        each other have independent, arbitrary origins, so a boundary drawn
        between them separates items on the strength of the prior rather than of
        a judgment. That is the same failure the `unplaced` mechanism exists to
        prevent, arriving by a different route.
        """
        found = self.components()
        if len(found) > 1:
            groups = "; ".join("{" + ", ".join(group) + "}" for group in found)
            msg = (
                f"the judged findings fall into {len(found)} groups with no comparison "
                f"between them: {groups}. Their scale values have independent origins "
                "and are not comparable; compare an item from each group against one "
                "from another to join them"
            )
            raise DisconnectedComparisonsError(msg)
