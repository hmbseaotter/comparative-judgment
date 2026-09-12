"""The record types.

Every one is a frozen dataclass. That is not stylistic: the comparison log is
append-only and its records are the product, so a record that can be mutated
after construction is a record whose stored form and in-memory form can disagree.

Nothing here imports a UI library, and nothing here formats anything for display.
The session API returns these; a terminal renders them as panes and a browser
would render them as HTML. A `__str__` that assumed a terminal would be the
subtlest way to bake a front end into the core.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

SCHEMA_VERSION: Final[str] = "1"

#: Keys every findings entry must carry. Settled jointly with the consuming
#: harness (its D22, this project's D10); a cross-repository scanner asserts the
#: two specifications keep naming the same six.
REQUIRED_FINDING_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "observation", "evidence", "consequence", "detectable_by", "tier"}
)

#: Never present in a findings document. Severity is produced by this tool and
#: joined by id; a findings file that carries it has two sources of truth.
FORBIDDEN_FINDING_KEYS: Final[frozenset[str]] = frozenset({"severity"})


class Tier(StrEnum):
    """Whether an entry is a defect at all.

    `QUESTION` entries are open questions about behavior that may be correct by
    design. They have no consequence to compare against, so they are excluded
    from batches, from the fit and from the anchor set — a rated question row
    would become an anchor that silently distorts every later placement.
    """

    DEFECT = "defect"
    QUESTION = "question"


class DetectableBy(StrEnum):
    """Where a finding is checkable, assigned at review time before code exists."""

    ASSERT = "assert"
    JUDGE = "judge"
    HUMAN = "human"


class Outcome(StrEnum):
    """The result of one comparison.

    `TIE` is a first-class outcome, not a failure to answer. Forcing a winner on
    a genuinely equal pair puts a coin-flip into the log indistinguishable from a
    real judgment. Ties are recorded, excluded from the fit, and reported as a
    rate — itself a measure of where the scale is soft.
    """

    LEFT = "left"
    RIGHT = "right"
    TIE = "tie"


class Band(StrEnum):
    """The four defect severity levels, worst first."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CutName(StrEnum):
    """The three boundaries between four bands."""

    CRITICAL_HIGH = "critical_high"
    HIGH_MEDIUM = "high_medium"
    MEDIUM_LOW = "medium_low"


#: Cuts in descending severity order. The order is load-bearing — band placement
#: walks it — so it is fixed here rather than re-derived at each call site.
CUT_ORDER: Final[tuple[CutName, ...]] = (
    CutName.CRITICAL_HIGH,
    CutName.HIGH_MEDIUM,
    CutName.MEDIUM_LOW,
)

#: Bands in descending severity order, aligned with CUT_ORDER: an item above the
#: first cut is BANDS[0], between the first and second is BANDS[1], and so on.
BANDS: Final[tuple[Band, ...]] = (Band.CRITICAL, Band.HIGH, Band.MEDIUM, Band.LOW)


def content_hash(*, observation: str, evidence: tuple[str, ...], consequence: str) -> str:
    """Hash the text a rater actually judged.

    Only the fields shown during a comparison are hashed, so the hash answers one
    question: is this the text the rater was looking at? When it changes on a
    finding that has been judged, loading is *refused* until a human accepts the
    change (D18) — the tool cannot tell a corrected typo from a rewrite, and
    forking automatically would orphan every judgment about a finding whose
    spelling someone fixed.

    Evidence fragments are joined with a separator that cannot occur in the text
    itself, so ``["ab", "c"]`` and ``["a", "bc"]`` do not collide.
    """
    parts = [observation.strip(), consequence.strip(), *(e.strip() for e in evidence)]
    joined = "\x00".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Finding:
    """One entry from the findings document, as judged."""

    id: str
    content_hash: str
    observation: str
    evidence: tuple[str, ...]
    consequence: str
    detectable_by: DetectableBy
    tier: Tier
    call_ref: str = ""


@dataclass(frozen=True, slots=True)
class Comparison:
    """One recorded judgment.

    `seq` is assigned by the store on append and is the log's ordering. It is
    stored rather than derived from position so that a retraction can name what
    it retracts without depending on line numbers.
    """

    seq: int
    left_id: str
    right_id: str
    outcome: Outcome
    rater_id: str
    session_id: str
    timestamp: str

    def winner_loser(self) -> tuple[str, str] | None:
        """Return ``(winner, loser)``, or ``None`` for a tie.

        "Winner" means *more severe*. Ties return None rather than an arbitrary
        order, so a caller cannot silently treat one as a decided comparison.
        """
        if self.outcome is Outcome.TIE:
            return None
        if self.outcome is Outcome.LEFT:
            return (self.left_id, self.right_id)
        return (self.right_id, self.left_id)


@dataclass(frozen=True, slots=True)
class Retraction:
    """A withdrawn comparison.

    Appended, never a deletion: the log is the product and its history is part of
    what makes a result reproducible. A retracted comparison leaves the fit but
    stays in the record.
    """

    seq: int
    retracts_seq: int
    rater_id: str
    session_id: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class RevisionAccepted:
    """A record that a finding's text changed and a human accepted it anyway.

    Loading a revised findings document is refused by default: judgments made
    against the old wording would otherwise apply silently to new wording nobody
    compared. Accepting is deliberate, and this is the audit trail for it —
    who, when, which finding, and which text it moved between.

    It lives in the same append-only log as comparisons and retractions, which
    means an acceptance changes the log hash. A severity file naming that hash is
    therefore tied to a history that *includes* the acceptance rather than one
    that conceals it.
    """

    seq: int
    finding_id: str
    old_hash: str
    new_hash: str
    #: How many judgments were carried over. Part of the record rather than
    #: recoverable from it: the count is taken at the moment of acceptance, and
    #: later comparisons would make a recomputed figure disagree with what the
    #: human was actually shown when they decided.
    comparisons: int
    rater_id: str
    session_id: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class Revision:
    """A pending text change, detected but not yet accepted."""

    finding_id: str
    old_hash: str
    new_hash: str
    comparisons: int


@dataclass(frozen=True, slots=True)
class RemovalAccepted:
    """A record that a judged finding left the document and a human accepted it.

    The sibling of :class:`RevisionAccepted`, for the adjacent door. Where a
    revision changes what a judgment *refers to*, a removal takes the referent
    away entirely: the comparisons stay in the log, the fit no longer knows the
    item, and the surviving partner's appearance count silently falls. The same
    reasoning applies — nothing can distinguish a deliberate cut from a stray
    edit, so the tool refuses and a human decides — and the same audit trail
    follows, in the same log, changing the same hash.
    """

    seq: int
    finding_id: str
    old_hash: str
    comparisons: int
    rater_id: str
    session_id: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class Removal:
    """A judged finding present in the store and absent from the incoming document."""

    finding_id: str
    old_hash: str
    comparisons: int


@dataclass(frozen=True, slots=True)
class Cut:
    """A band boundary, stored as the two findings either side of it.

    Not a scale value. A pairwise scale has no absolute origin, so a stored
    threshold means something only relative to the fit that produced it; after a
    refit the same number sits somewhere subtly different and items cross it for
    reasons the rater never judged. Storing the pair preserves what was actually
    decided — "the boundary sits between this finding and that one" — and its
    threshold is recomputed as their midpoint at each fit.
    """

    name: CutName
    above_id: str
    below_id: str
    calibration_note: str = ""


@dataclass(frozen=True, slots=True)
class Estimate:
    """One item's fitted position on the scale.

    Standard errors are deliberately absent in this phase: nothing here consumes
    them — placement compares against cut thresholds and the stopping rule counts
    appearances — and they are the phase-2 diagnostics' input.
    """

    finding_id: str
    theta: float
    appearances: int
    wins: int
    losses: int
    ties: int


@dataclass(frozen=True, slots=True)
class BandAssignment:
    """Where an item landed, and against which fit."""

    finding_id: str
    band: Band
    theta: float


@dataclass(frozen=True, slots=True)
class CutSeparation:
    """How far apart a cut's anchors sit on the current fit, and what lies between them.

    A cut is drawn between two findings the rater judged to be neighbors, and its
    threshold is their midpoint. A refit can move the pair apart, and the findings
    that come to lie between them are then banded by that midpoint rather than by
    any judgment against the boundary -- which is how placing one finding silently
    re-banded three others in the consuming harness.

    **Reported, never refused** (D34). An inverted pair is refused because the
    boundary no longer means anything; a separated one still means something, and
    how far apart is too far is a judgment: one finding between anchors is ordinary
    drift and seventeen is not, and a line drawn between those would be a threshold
    nobody chose. So every cut states its gap and names what lies between, and the
    rater decides.
    """

    name: CutName
    above_id: str
    below_id: str
    #: The upper anchor's scale value minus the lower one's, on the fit reported.
    gap: float
    #: Banded findings strictly between the two anchors, most severe first.
    between: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PairForReview:
    """What a front end needs to show one comparison.

    Structured, not formatted. A terminal lays these out as panes; a browser
    would render them as HTML. Returning display strings from here is how a
    terminal assumption gets baked into the core.
    """

    left: Finding
    right: Finding
    comparisons_spent: int
    appearance_target: int
    appearances_left: int
    appearances_right: int


@dataclass(frozen=True, slots=True)
class Progress:
    """Where a batch has got to.

    `mean_comparisons_per_item` and `min_appearances` are reported because the
    cost model rests on two estimates — roughly ten appearances per item, roughly
    three comparisons to place one — and measuring them is what turns those from
    claims into figures the first real batch either confirms or refutes.
    """

    admitted: int
    excluded_questions: int
    comparisons_spent: int
    ties: int
    min_appearances: int
    mean_appearances: float
    appearance_target: int
    complete: bool
    items_below_target: tuple[str, ...] = field(default_factory=tuple)
    #: Named once cuts exist: non-anchor findings still short of the placement
    #: quota, which is what finishing means in that mode. `items_below_target`
    #: keeps its own meaning and stays populated -- "below the appearance
    #: target" is still a true statement about a freshly placed item, it is
    #: simply not the criterion any more. Two fields rather than one that
    #: changes meaning, because a field whose meaning depends on another field
    #: is read wrongly by whoever forgets to check the other one.
    items_unplaced: tuple[str, ...] = field(default_factory=tuple)
    #: True once cuts exist: the loop is placing items against boundaries rather
    #: than working every item toward the appearance target, so "every item at N
    #: appearances" is not what finishing means any more.
    placing: bool = False
    #: Non-empty when there is nothing to judge because something is *wrong* --
    #: an inverted cut, an anchor that no longer exists -- rather than because
    #: the batch is done. Without it a front end cannot tell the two apart, and
    #: the honest-looking answer is the wrong one.
    blocked_reason: str = ""
    #: Each cut's gap and the findings strictly between its anchors, once cuts
    #: exist and none is blocked (D34). Empty rather than partial while a cut is
    #: inverted or names a finding the fit does not hold: `blocked_reason` names
    #: that, and a gap across a broken boundary would describe nothing.
    separation: tuple[CutSeparation, ...] = field(default_factory=tuple)
