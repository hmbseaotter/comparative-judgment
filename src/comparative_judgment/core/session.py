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

import math
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from comparative_judgment.core import anchors, bands, diagnostics, graph, pairing, severity, stats
from comparative_judgment.core.errors import (
    BandsNotAssignedError,
    CutError,
    DisconnectedComparisonsError,
    ImportConflictError,
    IncompleteImportError,
    NothingToJudgeError,
    UnknownItemError,
)
from comparative_judgment.core.findings import load_findings
from comparative_judgment.core.fit import LAMBDA, FitResult, fit
from comparative_judgment.core.models import (
    CUT_ORDER,
    AnchorComparison,
    AnchorExport,
    AnchorSet,
    AnchorSetBridge,
    AssignedBand,
    BandAssignment,
    BandsAssigned,
    Comparison,
    ComponentReport,
    Cut,
    CutName,
    CutSeparation,
    CutThreshold,
    Diagnostics,
    Estimate,
    Finding,
    ImportOutcome,
    ImportRecorded,
    ItemDiagnostics,
    Outcome,
    PairForReview,
    Progress,
    ProposedBand,
    Removal,
    RemovalAccepted,
    Retraction,
    Revision,
    RevisionAccepted,
)
from comparative_judgment.core.store import Store

DEFAULT_SESSION_ID: Final[str] = "session"

#: The session id an assignment is recorded under. An assignment is not part of
#: a comparison session any more than a load's acceptances are, which `load`
#: records as "load"; this is the same convention for the same reason.
ASSIGN_SESSION_ID: Final[str] = "assign"

#: Re-exported so a front end can name the default without importing `pairing`.
#: The seam is the whole contract: a presentation layer that reaches into a core
#: module for one constant is a presentation layer that will reach in for a
#: function next.
DEFAULT_APPEARANCE_TARGET: Final[int] = pairing.APPEARANCE_TARGET

#: Re-exported for the same reason: a front end describing the regions it shows
#: names their size without importing `stats`.
REGION_COMPARISONS: Final[int] = stats.REGION_COMPARISONS

Monotonic = Callable[[], float]


def _comparison_identity(
    comparison: Comparison | AnchorComparison,
) -> tuple[str, str, str, str, str, str]:
    """What makes two records the same judgment, across stores (D41).

    Every field the judgment was made with, and neither the sequence number,
    which each store assigns itself, nor the origin, which says only how it
    arrived: so a judgment that has been to another store and back is recognized.
    """
    return (
        comparison.left_id,
        comparison.right_id,
        comparison.outcome.value,
        comparison.rater_id,
        comparison.session_id,
        comparison.timestamp,
    )


def _not_yet_present(
    incoming: Iterable[AnchorComparison], present: Iterable[Comparison]
) -> tuple[list[AnchorComparison], int]:
    """The incoming comparisons the log does not already hold, and how many it did.

    Matched by count, not by membership. A rater can record the same pair with the
    same outcome twice within one second, and those are two judgments: dropping
    one would give the importing store a different scale from the one exported.
    """
    remaining = Counter(_comparison_identity(c) for c in present)
    fresh: list[AnchorComparison] = []
    skipped = 0
    for comparison in incoming:
        key = _comparison_identity(comparison)
        if remaining[key] > 0:
            remaining[key] -= 1
            skipped += 1
        else:
            fresh.append(comparison)
    return fresh, skipped


def _cut_signature(cuts: Iterable[Cut]) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(sorted((c.name.value, c.above_id, c.below_id, c.calibration_note) for c in cuts))


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
    #: Every banded finding this fit places other than as assigned, or that has
    #: no assignment yet (D36). Export refuses while any remains.
    proposals: tuple[ProposedBand, ...]


@dataclass(frozen=True, slots=True)
class AssignOutcome:
    """What an assignment did, or refused to do (D36).

    Structured like :class:`LoadOutcome`: the policy -- a first assignment is
    free, changing an assigned band needs its own acceptance, and an assignment
    with nothing new or changed writes nothing -- lives here, and only the
    wording belongs to a front end.
    """

    applied: bool
    #: Findings banded for the first time, which need no further acceptance.
    first_time: tuple[ProposedBand, ...]
    #: Assigned findings the current fit bands differently, or not at all.
    rebanded: tuple[ProposedBand, ...]
    record: BandsAssigned | None

    @property
    def refused(self) -> bool:
        """Nothing was written because a change to an assigned band was not accepted."""
        return not self.applied and bool(self.rebanded)


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
        monotonic: Monotonic = time.monotonic,
    ) -> None:
        self._store = store
        self._rater_id = rater_id
        self._session_id = session_id
        self._target = appearance_target
        self._fit_cache: FitResult | None = None
        # The sitting's timer (D40). Held here, in memory, and never written: no
        # session keeps a start record, because a record that carries no judgment
        # would still change the log hash and the run id.
        self._monotonic = monotonic
        self._opened = monotonic()

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

    def elapsed_seconds(self) -> float:
        """Time since this session opened: the sitting, which is what fatigue is about (D40).

        Not part of :class:`Progress`, which stays a function of the store alone,
        and read without touching the store, so a front end can tick it every
        second at no cost to the comparison loop.
        """
        return self._monotonic() - self._opened

    def _incomplete_import_reason(self) -> str:
        pending = self._store.incomplete_imports()
        if not pending:
            return ""
        versions = ", ".join(record.anchor_set_version for record in pending)
        return (
            f"the import of anchor set {versions} was interrupted before it finished. Its "
            "record says more than the log holds, so the scale is missing judgments it "
            "claims; run import-anchors again with the same file to complete it"
        )

    def _require_complete_imports(self) -> None:
        """Refuse to write while an import is part-way done (D41).

        Anything built on the scale meanwhile -- a judgment, a cut, an assignment,
        an export -- would rest on an import the log says happened and that did not
        finish, so nothing writes until the same import is run again.
        """
        reason = self._incomplete_import_reason()
        if reason:
            raise IncompleteImportError(reason)

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

        Nothing is offered while an import is incomplete; `progress` names why.
        """
        if self._store.incomplete_imports():
            return None
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
        self._require_complete_imports()
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

        Only a judgment made in this store is withdrawn (D41). Straight after an
        import the latest live comparison is an imported one, possibly another
        rater's, and a misfired undo would retract it in this rater's name.
        """
        self._require_complete_imports()
        active = [c for c in self._store.active_comparisons() if not c.origin]
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
        remaining = None if blocked else self._remaining(estimates, unplaced, placing=placing)
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
            proposals=() if blocked else self._proposals(),
            comparisons_remaining=remaining,
            remaining_is_lower_bound=remaining is not None and not placing,
        )

    def _remaining(
        self, estimates: tuple[Estimate, ...], unplaced: tuple[str, ...], *, placing: bool
    ) -> int:
        """Comparisons still to make before the batch is complete (D40).

        Placing: each unplaced finding's shortfall against the placement quota,
        which is exact, because every placement comparison pairs one newcomer with
        one anchor. Bootstrapping: half the appearances still owed, rounded up,
        since each comparison supplies two -- a lower bound, because a needy
        finding's nearest partner may already be at its target and that comparison
        then serves only one. Measured once: fifty findings at a target of ten took
        251 comparisons against a bound of 250.
        """
        if placing:
            appearances = {e.finding_id: e.appearances for e in estimates}
            return sum(pairing.PLACEMENT_COMPARISONS - appearances[item] for item in unplaced)
        if len(estimates) < 2:
            return 0
        owed = sum(max(0, self._target - e.appearances) for e in estimates)
        return math.ceil(owed / 2)

    def _proposals(self) -> tuple[ProposedBand, ...]:
        """The proposals band placement reports, or none where placement refuses.

        Read from :meth:`place` rather than computed alongside it, so progress and
        placement cannot disagree about which bands are proposed. Progress refuses
        nothing, so a store that placement would refuse reports no proposals here;
        `blocked_reason` or the component report already names why.
        """
        try:
            return self.place().proposals
        except (CutError, UnknownItemError, DisconnectedComparisonsError):
            return ()

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

        An interrupted import is the other thing that is wrong rather than
        finished, and it comes first: until it completes, no cut can be trusted.
        """
        interrupted = self._incomplete_import_reason()
        if interrupted:
            return interrupted
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

        Raises :class:`IncompleteImportError` while an import is part-way done,
        which also refuses everything built on placement: assignment and both
        exports.
        """
        self._require_complete_imports()
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
            proposals=bands.proposals(assignments, self._store.assigned_bands()),
        )

    def assign(self, *, accept_rebanding: bool = False) -> AssignOutcome:
        """Fix every banded finding's band, under this session's rater (D36).

        Refuses whatever :meth:`place` refuses, since there is nothing sound to
        assign across an inverted cut or a disconnected graph. Otherwise:

        - a finding banded for the first time is assigned with no further flag,
          because nothing a consumer has cited changes;
        - a finding whose assigned band the current fit moves, or removes, is
          assigned only with `accept_rebanding` -- a flag of its own, for D22's
          reason, so that the more consequential acceptance is not reachable by
          habit;
        - with nothing new or changed, nothing is written, for the reason a
          retraction naming nothing is refused: an inert record still changes
          the log hash, and the run id with it.

        The record holds every banded finding, so the latest one alone is the
        frozen state.
        """
        placement = self.place()
        first = tuple(p for p in placement.proposals if p.assigned is None)
        changed = tuple(p for p in placement.proposals if p.assigned is not None)
        if not placement.proposals or (changed and not accept_rebanding):
            return AssignOutcome(applied=False, first_time=first, rebanded=changed, record=None)

        hashes = {f.id: f.content_hash for f in self._findings()}
        record = self._store.append_assignment(
            [
                AssignedBand(
                    finding_id=a.finding_id, band=a.band, content_hash=hashes[a.finding_id]
                )
                for a in placement.assignments
            ],
            rater_id=self._rater_id,
            session_id=ASSIGN_SESSION_ID,
        )
        return AssignOutcome(applied=True, first_time=first, rebanded=changed, record=record)

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

        And refuses by name, with no acceptance to pass, a finding reusing the
        identifier of one an import brought with other text (D41): accepting it as
        a revision would rewrite text another store's judgments were made against.
        """
        self._require_complete_imports()
        result = load_findings(findings_path)
        collisions = self._store.import_collisions(result.admitted)
        if collisions:
            listed = "; ".join(
                f"{item}: imported {imported[:12]}, document {incoming[:12]}"
                for item, imported, incoming in collisions
            )
            msg = (
                f"{len(collisions)} finding(s) in the document reuse the identifier of a finding "
                f"an anchor-set import brought, with other text: {listed}. One identifier cannot "
                "name two texts; rename the document's finding"
            )
            raise ImportConflictError(msg)
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
        self._require_complete_imports()
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
        """Write the severity file, refusing whatever `place` refuses.

        And refusing, writing nothing, while any banded finding is unassigned or
        the fit proposes a change to its assigned band (D36). So an exported band
        is always an assigned one, and never sits beside a `theta` that places it
        elsewhere, which the consuming harness refuses on load.
        """
        placement = self.place()
        if placement.proposals:
            listed = "; ".join(
                f"{p.finding_id}: {p.assigned.value if p.assigned else 'unassigned'} -> "
                f"{p.current.value if p.current else 'no band'}"
                for p in placement.proposals
            )
            msg = (
                f"{len(placement.proposals)} band(s) are unassigned or proposed for revision "
                f"on the current fit: {listed}. A consumer may already cite the assigned "
                "bands, so none is exported until a rater assigns them; changing an "
                "assigned band needs the re-banding acceptance"
            )
            raise BandsNotAssignedError(msg)
        severity.write_severity_file(
            path,
            assignments=placement.assignments,
            store=self._store,
            unplaced=placement.unplaced,
            cuts=self.cuts(),
        )
        return placement

    # -- diagnostics -------------------------------------------------------

    def diagnostics(self) -> Diagnostics:
        """Standard errors, misfit, the tie rate and the scale's soft regions (D37-D39, D43).

        Refuses nothing. Over a disconnected graph it reports the components and
        keeps each region inside one, since scale values are not comparable across
        a gap no comparison bridges (D21); over an inverted cut it names the cut in
        `blocked_reason` and labels no region with a threshold that means nothing.
        """
        result = self._fit()
        active = self._store.active_comparisons()
        cuts = self._store.cuts()
        errors = stats.standard_errors(result, active)
        fits = stats.item_misfit(result, active)
        blocked = self._blocked_reason(result.estimates, cuts)
        thresholds = self._cut_thresholds(result, cuts)
        items = tuple(
            ItemDiagnostics(
                finding_id=e.finding_id,
                theta=e.theta,
                se=errors[e.finding_id],
                appearances=e.appearances,
                informative=e.wins + e.losses,
                ties=e.ties,
                tie_rate=(e.ties / e.appearances) if e.appearances else None,
                infit=fits[e.finding_id].infit,
                outfit=fits[e.finding_id].outfit,
            )
            for e in result.estimates
        )
        decided = sum(e.wins for e in result.estimates)
        compared = decided + result.ties
        return Diagnostics(
            comparison_log_hash=self._store.log_hash(),
            anchor_set_version=self._store.anchor_set_version,
            regularization=LAMBDA,
            comparisons=compared,
            ties=result.ties,
            tie_rate=(result.ties / compared) if compared else 0.0,
            items=items,
            regions=stats.soft_regions(
                result,
                active,
                self.components(),
                tuple((c.name, c.above_id, c.below_id, c.threshold) for c in thresholds),
            ),
            components=self._component_reports(),
            anchor_sets=self._bridges(),
            cuts=thresholds,
            blocked_reason=blocked,
        )

    def write_diagnostics(self, path: Path) -> Diagnostics:
        """Compute the report and write it where the caller said (D44)."""
        report = self.diagnostics()
        diagnostics.write_diagnostics(path, report)
        return report

    @staticmethod
    def _cut_thresholds(result: FitResult, cuts: tuple[Cut, ...]) -> tuple[CutThreshold, ...]:
        """Each cut's threshold on this fit, or none where a cut means nothing on it."""
        if not cuts:
            return ()
        try:
            values = bands.thresholds(cuts, result.theta())
        except (CutError, UnknownItemError):
            return ()
        by_name = {cut.name: cut for cut in cuts}
        return tuple(
            CutThreshold(
                name=name,
                above_id=by_name[name].above_id,
                below_id=by_name[name].below_id,
                threshold=value,
            )
            for name, value in zip(CUT_ORDER, values, strict=True)
        )

    def _component_reports(self) -> tuple[ComponentReport, ...]:
        imported = {f.id for f in self._store.imported_findings()}
        return tuple(
            ComponentReport(
                members=group,
                imported=sum(1 for item in group if item in imported),
                local=sum(1 for item in group if item not in imported),
            )
            for group in self.components()
        )

    def _bridges(self) -> tuple[AnchorSetBridge, ...]:
        """For each import, how many decided comparisons tie its new findings to the rest (D43)."""
        held = {f.id for f in self._findings()}
        decided = [
            pair
            for c in self._store.active_comparisons()
            if (pair := c.winner_loser()) is not None and pair[0] in held and pair[1] in held
        ]
        reports: list[AnchorSetBridge] = []
        for record in self._store.imports():
            brought = {f.id for f in record.findings}
            in_set = brought | set(record.shared)
            reports.append(
                AnchorSetBridge(
                    anchor_set_version=record.anchor_set_version,
                    new=len(brought),
                    shared=len(record.shared),
                    bridging=sum(
                        1
                        for a, b in decided
                        if (a in brought and b not in in_set) or (b in brought and a not in in_set)
                    ),
                )
            )
        return tuple(reports)

    # -- anchor sets -------------------------------------------------------

    def export_anchor_set(self, path: Path) -> AnchorExport:
        """Write this store's anchor set: its judged findings, their comparisons and its cuts.

        Refuses whatever band placement refuses (D41) -- no cuts, an inverted cut,
        a disconnected graph -- since an anchor set whose boundaries mean nothing
        here would mean nothing anywhere else either.
        """
        self.place()
        judged = [e.finding_id for e in self.estimates() if e.appearances > 0]
        anchor_set = anchors.build_anchor_set(
            findings=self._findings(),
            comparisons=self._store.active_comparisons(),
            cuts=self._store.cuts(),
            judged=judged,
            source_log_hash=self._store.log_hash(),
        )
        anchors.write_anchor_set(path, anchor_set)
        return AnchorExport(
            version=anchor_set.version,
            findings=len(anchor_set.findings),
            comparisons=len(anchor_set.comparisons),
        )

    def import_anchor_set(self, path: Path) -> ImportOutcome:
        """Bring another store's anchor set into this one, under this session's rater (D41).

        Everything is checked before anything is written (D26), and every conflict
        is refused by name with no flag to pass, because each would merge two
        things that are not the same: an identifier this store holds with other
        text or has retired, cuts that differ from this store's own, a merge that
        would invert a cut, or judged findings the placement loop could not then
        join to the imported ones. No band is assigned (D42): imported findings
        arrive as first-time proposals, for `assign` to accept.

        The record goes first and the comparisons after it, then the cuts, so an
        interruption is detectable exactly and running the same import again
        finishes it.
        """
        anchor_set = anchors.read_anchor_set(path)
        version = anchor_set.version
        pending = {r.anchor_set_version: r for r in self._store.incomplete_imports()}
        if version in pending:
            return self._complete_import(anchor_set, pending[version])
        self._require_complete_imports()
        if any(r.anchor_set_version == version for r in self._store.imports()):
            msg = (
                f"anchor set {version} is already imported into this store; importing it again "
                "would count every judgment it carries twice"
            )
            raise ImportConflictError(msg)

        new, shared = self._classify_imported_findings(anchor_set)
        store_cuts = self._store.cuts()
        if store_cuts and _cut_signature(store_cuts) != _cut_signature(anchor_set.cuts):
            msg = (
                "this store's cuts differ from the anchor set's, and an import brings the set's "
                f"cuts: store {self._describe_cuts(store_cuts)}; file "
                f"{self._describe_cuts(anchor_set.cuts)}. Import into a store whose cuts match, "
                "or into one with none"
            )
            raise ImportConflictError(msg)

        present = [e for e in self._store.log() if isinstance(e, Comparison)]
        fresh, skipped = _not_yet_present(anchor_set.comparisons, present)
        adopting = not store_cuts
        self._check_merge(anchor_set, new, fresh)

        if not new and not fresh and not adopting:
            return ImportOutcome(
                anchor_set_version=version,
                applied=False,
                resumed=False,
                new_findings=(),
                shared_findings=tuple(sorted(shared)),
                appended=0,
                skipped=skipped,
                cuts_adopted=False,
                bridge=None,
                components=self._component_reports(),
            )

        record = self._store.append_import(
            anchor_set_version=version,
            source_log_hash=anchor_set.source_log_hash,
            findings=new,
            shared=shared,
            cuts=anchor_set.cuts,
            appended=len(fresh),
            skipped=skipped,
            rater_id=self._rater_id,
        )
        self._store.append_imported_comparisons(fresh, origin=version)
        if adopting:
            self._store.put_cuts(anchor_set.cuts)
        self._invalidate()
        return self._import_outcome(record, resumed=False, cuts_adopted=adopting)

    def _complete_import(self, anchor_set: AnchorSet, record: ImportRecorded) -> ImportOutcome:
        """Finish an import an earlier run was interrupted in.

        The comparisons still owed are recomputed exactly as the first run chose
        them -- the file's, less what the log held before the record -- and the
        ones already appended after the record are a prefix of that list, since
        they were written in order. So the finished log is the one an
        uninterrupted import would have written.
        """
        log = self._store.log()
        before = [e for e in log if isinstance(e, Comparison) and e.seq < record.seq]
        fresh, _ = _not_yet_present(anchor_set.comparisons, before)
        carried = sum(
            1 for e in log if isinstance(e, Comparison) and e.origin == record.anchor_set_version
        )
        if len(fresh) != record.appended:
            msg = (
                f"the interrupted import of {record.anchor_set_version} recorded {record.appended} "
                f"comparison(s) to append, and this file yields {len(fresh)} against the log as it "
                "stood; the store has changed under it, so completing it would guess"
            )
            raise ImportConflictError(msg)
        self._store.append_imported_comparisons(fresh[carried:], origin=record.anchor_set_version)
        adopting = not self._store.cuts()
        if adopting:
            self._store.put_cuts(record.cuts)
        self._invalidate()
        return self._import_outcome(record, resumed=True, cuts_adopted=adopting)

    def _import_outcome(
        self, record: ImportRecorded, *, resumed: bool, cuts_adopted: bool
    ) -> ImportOutcome:
        bridge = next(
            b for b in self._bridges() if b.anchor_set_version == record.anchor_set_version
        )
        return ImportOutcome(
            anchor_set_version=record.anchor_set_version,
            applied=True,
            resumed=resumed,
            new_findings=tuple(f.id for f in record.findings),
            shared_findings=record.shared,
            appended=record.appended,
            skipped=record.skipped,
            cuts_adopted=cuts_adopted,
            bridge=bridge,
            components=self._component_reports(),
        )

    def _classify_imported_findings(self, anchor_set: AnchorSet) -> tuple[list[Finding], list[str]]:
        """Split the file's findings into new and shared, refusing any collision (D41).

        An identifier collides when this store holds it with other text; when its
        removal was accepted here, since importing it would quietly undo an audited
        removal or attach old judgments to new text; when comparisons in the log
        still name it though no finding holds it; and when it was excluded here as
        a question. Every collision is named, and none can be accepted.
        """
        held = {f.id: f.content_hash for f in self._findings()}
        removed = {r.finding_id: r.old_hash for r in self._store.accepted_removals()}
        named = {item for c in self._store.active_comparisons() for item in (c.left_id, c.right_id)}
        questions = set(self._store.excluded_question_ids())
        new: list[Finding] = []
        shared: list[str] = []
        problems: list[str] = []
        for finding in anchor_set.findings:
            ident, text = finding.id, finding.content_hash[:12]
            if ident in held:
                if held[ident] == finding.content_hash:
                    shared.append(ident)
                else:
                    problems.append(f"{ident}: this store {held[ident][:12]}, file {text}")
            elif ident in removed:
                problems.append(
                    f"{ident}: its removal was accepted here (was {removed[ident][:12]}), "
                    f"file {text}"
                )
            elif ident in named:
                problems.append(f"{ident}: comparisons here still name it, though nothing holds it")
            elif ident in questions:
                problems.append(f"{ident}: excluded here as a question")
            else:
                new.append(finding)
        if problems:
            msg = (
                f"{len(problems)} identifier(s) in the anchor set collide with this store: "
                f"{'; '.join(problems)}. An identifier cannot name two texts, and the "
                "judgments behind each were made against its own"
            )
            raise ImportConflictError(msg)
        return new, shared

    def _check_merge(
        self, anchor_set: AnchorSet, new: Sequence[Finding], fresh: Sequence[AnchorComparison]
    ) -> None:
        """Fit the store as the import would leave it, and refuse what that would break.

        Two things: a cut the merged fit inverts, which no boundary survives; and a
        group of judged findings with no imported member, which the placement loop
        can never bridge, since it offers only findings short of their quota and
        these are not -- the store would sit reporting completion while bands
        refuse it as disconnected (D41).
        """
        ids = [f.id for f in self._findings()] + [f.id for f in new]
        merged = list(self._store.active_comparisons()) + [
            Comparison(
                seq=0,
                left_id=c.left_id,
                right_id=c.right_id,
                outcome=c.outcome,
                rater_id=c.rater_id,
                session_id=c.session_id,
                timestamp=c.timestamp,
            )
            for c in fresh
        ]
        result = fit(ids, merged)
        cuts = self._store.cuts() or anchor_set.cuts
        try:
            bands.thresholds(cuts, result.theta())
        except (CutError, UnknownItemError) as exc:
            msg = f"importing this anchor set would break a cut on the merged scale: {exc}"
            raise ImportConflictError(msg) from exc

        carried = {f.id for f in anchor_set.findings}
        judged = [e.finding_id for e in result.estimates if e.appearances > 0]
        stranded = [g for g in graph.components(judged, merged) if not carried & set(g)]
        if stranded:
            groups = "; ".join("{" + ", ".join(group) + "}" for group in stranded)
            msg = (
                f"after this import, {len(stranded)} group(s) of judged findings would have no "
                f"comparison path to the imported anchors: {groups}. The placement loop offers "
                "only findings short of their quota, so it could never join them and bands would "
                "refuse the store as disconnected. Import before judging, into a store whose "
                "findings are loaded and not yet compared"
            )
            raise ImportConflictError(msg)

    @staticmethod
    def _describe_cuts(cuts: Iterable[Cut]) -> str:
        return ", ".join(
            f"{c.name.value} {c.above_id}|{c.below_id}"
            for c in sorted(cuts, key=lambda c: CUT_ORDER.index(c.name))
        )

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
