"""Writing the severity file.

Severity leaves this tool in its **own** file, keyed by finding id — never
written back into the findings document. Two reasons, and the second is the one
that generalizes: the tool must not mutate a document it does not own (it may not
even have write access to it), and keeping severity separate makes its provenance
explicit. A value in this file names the run, the anchor set and the comparison
log it came from; the same value pasted into a column names nothing.

The consuming project joins on ``id``. That is the whole contract.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from comparative_judgment.core import bands
from comparative_judgment.core.errors import SeverityWriteError, UnknownItemError
from comparative_judgment.core.models import BandAssignment, Cut
from comparative_judgment.core.store import Store

SEVERITY_SCHEMA_VERSION: Final[str] = "3"
"""Bumped from 1 when each row gained `content_hash`, `appearances` and
`informative`, and from 2 when the file gained `cuts`: each cut's anchors, its
gap and the findings between them (D34).

**A version that does not move when the schema does means nothing**, and the
consumer needs it here for a specific reason: its own contract says extra fields
are tolerated, so absence is legal. Without a version a reader cannot tell a file
that predates the fields from a tool that never emitted them, and a check for
them would have to tolerate absence and therefore assert nothing.
"""


def run_id(*, log_hash: str, anchor_set_version: str, cuts: Sequence[Cut]) -> str:
    """Name this run by what produced it, rather than by when it happened.

    Derived, not minted. A UUID would identify the export; this identifies the
    *result* — two exports from an unchanged log, anchor set and set of cuts
    carry the same id, and any change to those three changes it. That keeps the
    byte-identity guarantee intact without needing an exclusion carved out of
    it, and makes the id answer a question worth asking: is this the same
    severity assignment I saw before?

    The cuts are included because they are not in the log. Three different
    boundaries over one set of judgments are three different results.
    """
    parts = [log_hash, anchor_set_version]
    for cut in sorted(cuts, key=lambda c: c.name.value):
        parts += [cut.name.value, cut.above_id, cut.below_id, cut.calibration_note.strip()]
    # A separator that cannot occur in any of the parts, so two different
    # decompositions cannot hash the same -- the same reasoning as content_hash.
    separator = "\x00"
    return hashlib.sha256(separator.join(parts).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class RowEvidence:
    """What a severity row says about its own basis."""

    content_hash: str
    appearances: int
    informative: int


def row_evidence(store: Store) -> dict[str, RowEvidence]:
    """Per finding: the text hash it was judged on, and how decisively.

    Read from the store rather than passed in, so the numbers cannot disagree
    with the log they describe -- the failure this whole schema change is
    against is a file saying something the artifact behind it does not.

    `informative` counts decided comparisons. A tie is a judgment and is kept in
    the log, but the fit excludes it, so it tells a reader nothing about where
    the finding sits.
    """
    appearances: dict[str, int] = {}
    informative: dict[str, int] = {}
    for comparison in store.active_comparisons():
        decided = comparison.winner_loser() is not None
        for item in (comparison.left_id, comparison.right_id):
            appearances[item] = appearances.get(item, 0) + 1
            if decided:
                informative[item] = informative.get(item, 0) + 1
    return {
        finding.id: RowEvidence(
            content_hash=finding.content_hash,
            appearances=appearances.get(finding.id, 0),
            informative=informative.get(finding.id, 0),
        )
        for finding in store.findings()
    }


def build_payload(
    *,
    assignments: Sequence[BandAssignment],
    store: Store,
    unplaced: Sequence[str] = (),
    cuts: Sequence[Cut] = (),
) -> dict[str, object]:
    """Assemble the severity document.

    Separated from writing so a caller can inspect or diff it without touching
    the filesystem, and so the test that asserts provenance fields does not have
    to read a file back.
    """
    log_hash = store.log_hash()
    anchor_set_version = store.anchor_set_version
    evidence = row_evidence(store)
    missing = sorted(a.finding_id for a in assignments if a.finding_id not in evidence)
    if missing:
        raise UnknownItemError(
            "cannot write a severity row for finding(s) the store does not hold: "
            f"{', '.join(missing)}. A band whose text and comparison counts are unknown is "
            "the row this schema exists to make impossible."
        )
    return {
        "schema_version": SEVERITY_SCHEMA_VERSION,
        "anchor_set_version": anchor_set_version,
        "comparison_log_hash": log_hash,
        "run_id": run_id(log_hash=log_hash, anchor_set_version=anchor_set_version, cuts=cuts),
        # What the top cut was calibrated against. Without it the file records a
        # perfectly consistent ordering with no way to tell whether the whole set
        # sits a band too high.
        "calibration": {
            c.name.value: c.calibration_note for c in sorted(cuts, key=lambda c: c.name.value)
        },
        # **How each boundary sits on the fit this file came from**, computed from
        # the rows it exports rather than passed in, so a consumer re-deriving a
        # between-set from `theta` cannot find the file disagreeing with itself
        # (D34). A cut with findings between its anchors is reported here and
        # refused nowhere: how far apart is too far is the rater's judgment.
        "cuts": [
            {
                "name": report.name.value,
                "above_id": report.above_id,
                "below_id": report.below_id,
                "gap": report.gap,
                "between": list(report.between),
            }
            for report in (bands.separation(cuts, assignments) if cuts else ())
        ],
        "severities": [
            {
                "id": a.finding_id,
                "severity": a.band.value,
                "theta": a.theta,
                # **What this band was placed on**, so the file says what it
                # scored and not only what it concluded. Without it a finding's
                # text can be edited after export and the band goes on
                # describing wording nobody compared -- detectable only by
                # someone running `cj load`, and the edits that cause it are
                # cross-cutting sweeps where nobody is thinking about severity.
                "content_hash": evidence[a.finding_id].content_hash,
                # **How well determined the band is.** Two rows that look
                # identical can rest on very different evidence: `appearances`
                # counts the live comparisons a finding is in, `informative`
                # counts those that were decided rather than tied. Ties are
                # excluded from the fit, so a finding with ten appearances and
                # eight ties is placed on two results -- which is the shape that
                # moves furthest when one more comparison arrives.
                "appearances": evidence[a.finding_id].appearances,
                "informative": evidence[a.finding_id].informative,
            }
            for a in sorted(assignments, key=lambda a: a.finding_id)
        ],
        # Named rather than omitted. An id absent from `severities` with no
        # explanation reads as an oversight; listed here it reads as what it is
        # -- a finding nobody compared, which the tool declines to band because
        # doing so would report the prior as a judgment.
        "unplaced": list(unplaced),
    }


def write_severity_file(
    path: Path,
    *,
    assignments: Sequence[BandAssignment],
    store: Store,
    unplaced: Sequence[str] = (),
    cuts: Sequence[Cut] = (),
) -> None:
    payload = build_payload(assignments=assignments, store=store, unplaced=unplaced, cuts=cuts)
    body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        msg = f"cannot write the severity file to {path}: {exc}"
        raise SeverityWriteError(msg) from exc
