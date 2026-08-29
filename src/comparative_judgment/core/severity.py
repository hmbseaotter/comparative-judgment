"""Writing the severity file.

Severity leaves this tool in its **own** file, keyed by finding id — never
written back into the findings document. Two reasons, and the second is the one
that generalises: the tool must not mutate a document it does not own (it may not
even have write access to it), and keeping severity separate makes its provenance
explicit. A value in this file names the run, the anchor set and the comparison
log it came from; the same value pasted into a column names nothing.

The consuming project joins on ``id``. That is the whole contract.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from comparative_judgment.core.errors import SeverityWriteError
from comparative_judgment.core.models import BandAssignment, Cut
from comparative_judgment.core.store import Store

SEVERITY_SCHEMA_VERSION: Final[str] = "1"


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
        "severities": [
            {
                "id": a.finding_id,
                "severity": a.band.value,
                "theta": a.theta,
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
