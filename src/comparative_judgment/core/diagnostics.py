"""Writing the diagnostics report (D44).

Its own file, never folded into the severity file: that file's fields are
enumerated in this specification and in the consuming harness's, and compared by
the harness's scanner, so a field added there for diagnostics would break an
interface to report something no consumer asked for. The report is written only
where a caller names a path; nothing in the store changes.

The report names the log it was computed from, so it can be recomputed. It is
byte-identical across two writes over an unchanged log on one machine, the
standard errors' own guarantee (D39).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from comparative_judgment.core.errors import OutputWriteError
from comparative_judgment.core.models import Diagnostics

DIAGNOSTICS_SCHEMA_VERSION: Final[str] = "1"


def diagnostics_payload(report: Diagnostics) -> dict[str, object]:
    return {
        "diagnostics_schema_version": DIAGNOSTICS_SCHEMA_VERSION,
        "comparison_log_hash": report.comparison_log_hash,
        "anchor_set_version": report.anchor_set_version,
        "regularization": report.regularization,
        "comparisons": report.comparisons,
        "ties": report.ties,
        "tie_rate": report.tie_rate,
        "blocked_reason": report.blocked_reason,
        "cuts": [
            {
                "name": cut.name.value,
                "above_id": cut.above_id,
                "below_id": cut.below_id,
                "threshold": cut.threshold,
            }
            for cut in report.cuts
        ],
        "items": [
            {
                "id": item.finding_id,
                "theta": item.theta,
                "se": item.se,
                "appearances": item.appearances,
                "informative": item.informative,
                "ties": item.ties,
                "tie_rate": item.tie_rate,
                "infit": item.infit,
                "outfit": item.outfit,
            }
            for item in report.items
        ],
        "regions": [
            {
                "rank": region.rank,
                "component": region.component,
                "low": region.low,
                "high": region.high,
                "comparisons": region.comparisons,
                "ties": region.ties,
                "tie_rate": region.tie_rate,
                "infit": region.infit,
                "outfit": region.outfit,
                "findings": list(region.findings),
                "cuts": [name.value for name in region.cuts],
            }
            for region in report.regions
        ],
        "components": [
            {"members": list(c.members), "imported": c.imported, "local": c.local}
            for c in report.components
        ],
        "anchor_sets": [
            {
                "anchor_set_version": bridge.anchor_set_version,
                "new": bridge.new,
                "shared": bridge.shared,
                "bridging": bridge.bridging,
            }
            for bridge in report.anchor_sets
        ],
    }


def write_diagnostics(path: Path, report: Diagnostics) -> None:
    """Write the report with sorted keys and LF endings, whatever the platform (D35)."""
    body = json.dumps(diagnostics_payload(report), indent=2, sort_keys=True, ensure_ascii=False)
    try:
        path.write_text(body + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        msg = f"cannot write the diagnostics report to {path}: {exc}"
        raise OutputWriteError(msg) from exc
