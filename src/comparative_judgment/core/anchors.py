"""Anchor sets: the findings, judgments and cuts another store needs to band against (D41).

An anchor set is how an established scale moves between stores. The consuming
harness's phase 7 is the case it is built for: a fresh store for new findings
imports the design set's anchors, then places each new finding against the
imported cuts in about three comparisons, instead of bootstrapping a second scale
from nothing.

**What travels.** Every judged finding with its full text and content hash, every
live comparison among them -- ties included, retractions already applied -- with
the rater, session and timestamp it was made under, and the three cuts with their
calibration notes. Not the sequence numbers, which the importing store assigns
itself; not the source's band assignments, since a band in the importing store is
assigned by a rater there (D42); and not its history of retractions, revisions and
removals, which the source's log hash names instead.

**The version is the content's hash**, like the run id (D23): two exports of an
unchanged store are the same file byte for byte, and a file edited after export no
longer matches the version it states, which an import refuses.

**The file is somebody else's output**, so reading it validates everything before
anything is written (D26): every refusal is an :class:`AnchorSetError` naming the
file, and every finding's text must hash to the content hash it claims.

An anchor-set file holds findings text, and is exactly as sensitive as the store it
came from.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from comparative_judgment.core.errors import AnchorSetError, OutputWriteError
from comparative_judgment.core.models import (
    CUT_ORDER,
    AnchorComparison,
    AnchorSet,
    Comparison,
    Cut,
    CutName,
    Finding,
    Outcome,
    Tier,
    content_hash,
)
from comparative_judgment.core.shapes import as_dict, as_enum, as_list, as_str, field
from comparative_judgment.core.store import cut_payload, finding_payload, parse_cut, parse_finding

ANCHOR_SET_SCHEMA_VERSION: Final[str] = "1"


def _comparison_payload(comparison: AnchorComparison) -> dict[str, object]:
    return {
        "left_id": comparison.left_id,
        "right_id": comparison.right_id,
        "outcome": comparison.outcome.value,
        "rater_id": comparison.rater_id,
        "session_id": comparison.session_id,
        "timestamp": comparison.timestamp,
    }


def _content(anchor_set: AnchorSet) -> dict[str, object]:
    """The part of the file its version is the hash of."""
    return {
        "findings": [finding_payload(f) for f in anchor_set.findings],
        "comparisons": [_comparison_payload(c) for c in anchor_set.comparisons],
        "cuts": [cut_payload(c) for c in anchor_set.cuts],
    }


def _version_of(content: dict[str, object]) -> str:
    """Sixteen hex characters of the content's hash, the run id's shape (D23)."""
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_anchor_set(
    *,
    findings: Sequence[Finding],
    comparisons: Sequence[Comparison],
    cuts: Sequence[Cut],
    judged: Sequence[str],
    source_log_hash: str,
) -> AnchorSet:
    """The anchor set a store's judged findings make.

    `judged` names the findings with at least one live comparison the fit uses;
    the caller has it from the fit already. Comparisons travel in the order the log
    recorded them, among exported findings only -- a comparison naming a finding
    whose removal was accepted stays behind, as the fit leaves it out.
    """
    wanted = set(judged)
    exported = sorted((f for f in findings if f.id in wanted), key=lambda f: f.id)
    ids = {f.id for f in exported}
    carried = tuple(
        AnchorComparison(
            left_id=c.left_id,
            right_id=c.right_id,
            outcome=c.outcome,
            rater_id=c.rater_id,
            session_id=c.session_id,
            timestamp=c.timestamp,
        )
        for c in comparisons
        if c.left_id in ids and c.right_id in ids
    )
    ordered_cuts = tuple(sorted(cuts, key=lambda c: CUT_ORDER.index(c.name)))
    draft = AnchorSet(
        version="",
        source_log_hash=source_log_hash,
        findings=tuple(exported),
        comparisons=carried,
        cuts=ordered_cuts,
    )
    return AnchorSet(
        version=_version_of(_content(draft)),
        source_log_hash=source_log_hash,
        findings=draft.findings,
        comparisons=draft.comparisons,
        cuts=draft.cuts,
    )


def anchor_set_payload(anchor_set: AnchorSet) -> dict[str, object]:
    return {
        "anchor_set_schema_version": ANCHOR_SET_SCHEMA_VERSION,
        "anchor_set_version": anchor_set.version,
        "source_comparison_log_hash": anchor_set.source_log_hash,
        **_content(anchor_set),
    }


def write_anchor_set(path: Path, anchor_set: AnchorSet) -> None:
    """Write the file with sorted keys and LF endings, whatever the platform (D35)."""
    body = json.dumps(anchor_set_payload(anchor_set), indent=2, sort_keys=True, ensure_ascii=False)
    try:
        path.write_text(body + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        msg = f"cannot write the anchor set to {path}: {exc}"
        raise OutputWriteError(msg) from exc


def read_anchor_set(path: Path) -> AnchorSet:
    """Read and validate an anchor-set file, refusing by name whatever it gets wrong."""
    where = path.name
    err = AnchorSetError
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"cannot read the anchor set {where}: {exc}"
        raise AnchorSetError(msg) from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"{where} is not valid JSON: {exc}"
        raise AnchorSetError(msg) from exc

    raw = as_dict(document, where, error=err)
    schema = as_str(field(raw, "anchor_set_schema_version", where, error=err), where, error=err)
    if schema != ANCHOR_SET_SCHEMA_VERSION:
        msg = (
            f"{where} is anchor-set schema {schema!r}, and this build reads "
            f"{ANCHOR_SET_SCHEMA_VERSION!r}; refusing rather than guessing at its layout"
        )
        raise AnchorSetError(msg)

    findings = tuple(
        parse_finding(entry, f"{where} findings", error=err)
        for entry in as_list(field(raw, "findings", where, error=err), where, error=err)
    )
    comparisons = tuple(
        _parse_comparison(entry, where)
        for entry in as_list(field(raw, "comparisons", where, error=err), where, error=err)
    )
    cuts = tuple(
        parse_cut(entry, f"{where} cuts", error=err)
        for entry in as_list(field(raw, "cuts", where, error=err), where, error=err)
    )
    stated = as_str(field(raw, "anchor_set_version", where, error=err), where, error=err)
    source = as_str(field(raw, "source_comparison_log_hash", where, error=err), where, error=err)
    anchor_set = AnchorSet(
        version=stated,
        source_log_hash=source,
        findings=findings,
        comparisons=comparisons,
        cuts=cuts,
    )
    _validate(anchor_set, where)
    return anchor_set


def _parse_comparison(entry: object, where: str) -> AnchorComparison:
    err = AnchorSetError
    item = as_dict(entry, f"{where} comparisons", error=err)

    def text(key: str) -> str:
        return as_str(field(item, key, where, error=err), where, error=err)

    return AnchorComparison(
        left_id=text("left_id"),
        right_id=text("right_id"),
        outcome=as_enum(field(item, "outcome", where, error=err), Outcome, where, error=err),
        rater_id=text("rater_id"),
        session_id=text("session_id"),
        timestamp=text("timestamp"),
    )


def _validate(anchor_set: AnchorSet, where: str) -> None:
    """Everything a file must be before any of it is believed."""
    computed = _version_of(_content(anchor_set))
    if computed != anchor_set.version:
        msg = (
            f"{where} states version {anchor_set.version!r}, but its content hashes to "
            f"{computed!r}. It was edited after it was exported, so what it carries is not "
            "what the source store judged"
        )
        raise AnchorSetError(msg)

    ids: set[str] = set()
    for finding in anchor_set.findings:
        if finding.id in ids:
            msg = f"{where} carries finding {finding.id!r} more than once"
            raise AnchorSetError(msg)
        ids.add(finding.id)
        if finding.tier is Tier.QUESTION:
            msg = (
                f"{where} carries {finding.id!r} at tier 'question'. Question rows have no "
                "consequence to compare against and never belong in an anchor set (D10)"
            )
            raise AnchorSetError(msg)
        recomputed = content_hash(
            observation=finding.observation,
            evidence=finding.evidence,
            consequence=finding.consequence,
        )
        if recomputed != finding.content_hash:
            msg = (
                f"{where}: the text of {finding.id!r} hashes to {recomputed[:12]}, not the "
                f"{finding.content_hash[:12]} it states. Its judgments were made against "
                "other text"
            )
            raise AnchorSetError(msg)

    compared: set[str] = set()
    for comparison in anchor_set.comparisons:
        for item in (comparison.left_id, comparison.right_id):
            if item not in ids:
                msg = f"{where} has a comparison naming {item!r}, which it does not carry"
                raise AnchorSetError(msg)
        if comparison.left_id == comparison.right_id:
            msg = f"{where} has a comparison of {comparison.left_id!r} with itself"
            raise AnchorSetError(msg)
        compared.update((comparison.left_id, comparison.right_id))

    names = [cut.name for cut in anchor_set.cuts]
    if sorted(names, key=CUT_ORDER.index) != list(CUT_ORDER):
        msg = f"{where} must carry exactly the three cuts {', '.join(c.value for c in CUT_ORDER)}"
        raise AnchorSetError(msg)
    for cut in anchor_set.cuts:
        for anchor in (cut.above_id, cut.below_id):
            if anchor not in compared:
                msg = (
                    f"{where}: cut {cut.name.value!r} names {anchor!r}, which has no "
                    "comparison in the set, so its position would be the prior's"
                )
                raise AnchorSetError(msg)
        if cut.name is CutName.CRITICAL_HIGH and not cut.calibration_note.strip():
            msg = f"{where}: the top cut carries no calibration note (D25)"
            raise AnchorSetError(msg)
