"""Reading the findings document.

The document is YAML and hand-authored. YAML because the `evidence` field holds
verbatim multi-line quotes and the schema requires fragments from different
points in a call to stay visibly separate — a Markdown table cell cannot hold a
newline or an unescaped pipe, and CSV quoting is fragile at exactly that job.

Two refusals here are load-bearing rather than defensive:

* A document carrying a ``severity`` field is rejected by name. Severity is
  produced by this tool and joined by id; a findings file that also carries it
  has two sources of truth, and the tool never mutates the document it reads.
* Entries with ``tier: question`` are excluded from the batch. They are
  non-defects — open questions about behaviour that may be correct by design —
  so they have no consequence to compare against. Rating one is meaningless on
  its own terms, and the damage compounds: a rated question row enters the
  anchor set, where it becomes a reference point every later placement is
  measured against. A scale anchored partly on items with no consequence is
  quietly wrong everywhere, and nothing in the output would reveal it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from comparative_judgment.core.errors import FindingSchemaError
from comparative_judgment.core.models import (
    FORBIDDEN_FINDING_KEYS,
    REQUIRED_FINDING_KEYS,
    DetectableBy,
    Finding,
    Tier,
    content_hash,
)
from comparative_judgment.core.shapes import as_dict, as_list, as_str, field

_ERR = FindingSchemaError


@dataclass(frozen=True, slots=True)
class LoadResult:
    """What a findings document yielded.

    ``excluded_questions`` is reported rather than silently dropped: a rater who
    expected seventy findings and sees sixty-three admitted needs to know why,
    and "the file had seven questions in it" is a different situation from "seven
    entries were malformed".
    """

    admitted: tuple[Finding, ...]
    excluded_questions: tuple[str, ...]

    @property
    def excluded_count(self) -> int:
        return len(self.excluded_questions)


def parse_findings(text: str, *, where: str = "findings") -> LoadResult:
    """Parse a findings document, admitting only defects."""
    document = yaml.safe_load(text)
    if document is None:
        return LoadResult(admitted=(), excluded_questions=())

    top = as_dict(document, where, error=_ERR)
    entries = as_list(field(top, "findings", where, error=_ERR), where, error=_ERR)

    admitted: list[Finding] = []
    excluded: list[str] = []
    seen: set[str] = set()

    for index, entry in enumerate(entries):
        item_where = f"{where}[{index}]"
        raw = as_dict(entry, item_where, error=_ERR)

        forbidden = FORBIDDEN_FINDING_KEYS & raw.keys()
        if forbidden:
            names = ", ".join(sorted(forbidden))
            msg = (
                f"{item_where}: findings documents must not carry {names}. Severity is "
                "produced by this tool and joined by id; carrying it here would give the "
                "value two sources of truth."
            )
            raise _ERR(msg)

        missing = REQUIRED_FINDING_KEYS - raw.keys()
        if missing:
            names = ", ".join(sorted(missing))
            msg = f"{item_where}: missing required key(s): {names}"
            raise _ERR(msg)

        finding_id = as_str(field(raw, "id", item_where, error=_ERR), item_where, error=_ERR)
        if finding_id in seen:
            msg = f"{item_where}: duplicate finding id {finding_id!r}"
            raise _ERR(msg)
        seen.add(finding_id)

        tier = Tier(as_str(field(raw, "tier", item_where, error=_ERR), item_where, error=_ERR))
        if tier is Tier.QUESTION:
            excluded.append(finding_id)
            continue

        observation = as_str(
            field(raw, "observation", item_where, error=_ERR), item_where, error=_ERR
        )
        consequence = as_str(
            field(raw, "consequence", item_where, error=_ERR), item_where, error=_ERR
        )
        for name, value in (("observation", observation), ("consequence", consequence)):
            if not value.strip():
                msg = (
                    f"{item_where}: {finding_id} has an empty {name}. A finding that cannot "
                    "be read without its transcript open cannot be compared against another, "
                    "and an anchor must be portable."
                )
                raise _ERR(msg)

        evidence_raw = as_list(
            field(raw, "evidence", item_where, error=_ERR), item_where, error=_ERR
        )
        evidence = tuple(as_str(e, item_where, error=_ERR) for e in evidence_raw)

        detectable = DetectableBy(
            as_str(field(raw, "detectable_by", item_where, error=_ERR), item_where, error=_ERR)
        )
        call_ref = as_str(raw.get("call_ref", ""), item_where, error=_ERR)

        admitted.append(
            Finding(
                id=finding_id,
                content_hash=content_hash(
                    observation=observation, evidence=evidence, consequence=consequence
                ),
                observation=observation,
                evidence=evidence,
                consequence=consequence,
                detectable_by=detectable,
                tier=tier,
                call_ref=call_ref,
            )
        )

    return LoadResult(admitted=tuple(admitted), excluded_questions=tuple(excluded))


def load_findings(path: Path) -> LoadResult:
    """Read and parse a findings document from disk."""
    return parse_findings(path.read_text(encoding="utf-8"), where=path.name)
