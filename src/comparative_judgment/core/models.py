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

    `QUESTION` entries are open questions about behaviour that may be correct by
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

    Only the fields shown during a comparison are hashed. If they change, the
    finding becomes a new item and prior comparisons stay bound to the version
    that was on screen when they were made — otherwise old judgments silently
    re-attach to text nobody compared.

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

    @property
    def key(self) -> tuple[str, str]:
        """Identity for comparison purposes: an id *at a particular text*."""
        return (self.id, self.content_hash)


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
