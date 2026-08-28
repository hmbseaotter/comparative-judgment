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

import json
from collections.abc import Sequence
from pathlib import Path

from comparative_judgment.core.models import BandAssignment
from comparative_judgment.core.store import Store

SEVERITY_SCHEMA_VERSION = "1"


def build_payload(
    *,
    assignments: Sequence[BandAssignment],
    store: Store,
    unplaced: Sequence[str] = (),
) -> dict[str, object]:
    """Assemble the severity document.

    Separated from writing so a caller can inspect or diff it without touching
    the filesystem, and so the test that asserts provenance fields does not have
    to read a file back.
    """
    return {
        "schema_version": SEVERITY_SCHEMA_VERSION,
        "anchor_set_version": store.anchor_set_version,
        "comparison_log_hash": store.log_hash(),
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
) -> None:
    payload = build_payload(assignments=assignments, store=store, unplaced=unplaced)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
