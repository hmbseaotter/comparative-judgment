"""Interactive latency, written as a requirement (D45).

A keypress in the terminal UI records a judgment, derives the next pair and
redraws progress. The 0.5.0 audit measured that at 28 ms for 25 findings and 64 ms
for 100, roughly linear in n; by phase 2 it was 91 ms at 50 and about 280 ms at
200, because the log was parsed about eight times per keypress. The consuming
harness's next held-out set may pass the n = 100 the fit's cap was measured to, so
the cost is held here rather than left in *Not checked*.

**What is measured is realistic input, in D16's sense**: pairs near in rank and a
rater who is not perfectly consistent, from a fixed seed. A perfectly consistent
rater is the fit's slow case, and one refit per keypress is by design (the fit is
never incremental, so the same log always gives the same scale). The iteration
count is asserted too, so the fixture cannot drift into the slow case -- or out of
the realistic one -- and still pass for the wrong reason.

The budget is a median over several keypresses, which a loaded machine moves less
than any single one.
"""

from __future__ import annotations

import json
import math
import random
import statistics
import time
from pathlib import Path
from typing import Final

from comparative_judgment.core.findings import parse_findings
from comparative_judgment.core.models import Comparison, Outcome
from comparative_judgment.core.session import Session
from comparative_judgment.core.store import LOG_FILE, Store

#: The requirement: a median keypress under this many seconds at FINDINGS findings.
BUDGET: Final[float] = 0.250
FINDINGS: Final[int] = 200
#: Each finding judged against the next REACH below it: about ten appearances each.
REACH: Final[int] = 5
KEYPRESSES: Final[int] = 11


def _document(count: int) -> str:
    return "findings:\n" + "".join(
        f"  - id: F-{i:03d}\n"
        f"    observation: Observation {i}.\n"
        f"    evidence: ['line {i}: fragment']\n"
        f"    consequence: Consequence {i}.\n"
        "    detectable_by: judge\n"
        "    tier: defect\n"
        for i in range(count)
    )


def _realistic_store(path: Path) -> Store:
    """Two hundred findings and about a thousand near-rank comparisons, rater not perfect.

    The log is written in one go rather than through `append_comparison`, whose
    per-record check against every finding is the right cost for one keypress and
    the wrong one for building a thousand records in a fixture.
    """
    store = Store.create(path, clock=lambda: "2026-09-18T12:00:00Z")
    store.put_findings(parse_findings(_document(FINDINGS)).admitted)
    rng = random.Random(16)
    lines: list[str] = []
    for i in range(FINDINGS):
        for step in range(1, REACH + 1):
            if i + step >= FINDINGS:
                continue
            # The lower index is more severe; the rater agrees more often the
            # further apart two findings are, and is far from infallible nearby.
            agrees = rng.random() < 1.0 / (1.0 + math.exp(-0.3 * step))
            record = Comparison(
                seq=len(lines) + 1,
                left_id=f"F-{i:03d}",
                right_id=f"F-{i + step:03d}",
                outcome=Outcome.LEFT if agrees else Outcome.RIGHT,
                rater_id="r",
                session_id="s",
                timestamp="2026-09-18T12:00:00Z",
            )
            lines.append(json.dumps(Store._comparison_payload(record), sort_keys=True))
    (store.path / LOG_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return store


def test_a_keypress_at_two_hundred_findings_stays_inside_the_budget(tmp_path: Path) -> None:
    store = _realistic_store(tmp_path / "store")
    session = Session(store, rater_id="r", appearance_target=12)
    fitted = session.fit()
    assert 950 <= len(store.active_comparisons()) <= 1_000
    assert 300 <= fitted.iterations <= 2_000, (
        f"{fitted.iterations} iterations: the fixture has left the realistic range D16 measured"
    )
    assert session.next_pair() is not None

    timings: list[float] = []
    for _ in range(KEYPRESSES):
        started = time.perf_counter()
        session.record(Outcome.LEFT)
        session.next_pair()
        session.progress()
        timings.append(time.perf_counter() - started)
    median = statistics.median(timings)
    assert median < BUDGET, f"median keypress {median * 1000:.0f} ms at n = {FINDINGS}"
