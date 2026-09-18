"""The store: an append-only comparison log plus the findings it refers to.

The log *is* the product. Orderings, bands and severities are all derived from
it, which is why nothing here ever rewrites or deletes a comparison — a
retraction is an appended record naming what it withdraws. That is what lets a
result be recomputed from the inputs it names, and what lets a later phase fit a
different model over the same judgments without re-asking a single question.

Layout::

    <store>/
      meta.json          schema version, anchor-set version, created_at
      findings.jsonl     admitted findings, one per line
      comparisons.jsonl  append-only; comparisons, retractions, accepted revisions
                         and removals, and band assignments, interleaved
      cuts.json          the three band cuts, as anchor pairs

JSONL for the log because append-only is the whole point. JSON for machine state.
YAML appears nowhere in here: it is the format of the *human-authored* findings
document this store reads from, not of the store itself.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from comparative_judgment.core.errors import (
    NoStorePathError,
    RetractionError,
    StoreExistsError,
    StoreSchemaError,
    UnknownItemError,
)
from comparative_judgment.core.models import (
    SCHEMA_VERSION,
    AssignedBand,
    Band,
    BandsAssigned,
    Comparison,
    Cut,
    CutName,
    DetectableBy,
    Finding,
    Outcome,
    Removal,
    RemovalAccepted,
    Retraction,
    Revision,
    RevisionAccepted,
    Tier,
)
from comparative_judgment.core.shapes import (
    as_dict,
    as_int,
    as_list,
    as_str,
    field,
)

META_FILE: Final[str] = "meta.json"
FINDINGS_FILE: Final[str] = "findings.jsonl"
LOG_FILE: Final[str] = "comparisons.jsonl"
CUTS_FILE: Final[str] = "cuts.json"

# Bytes read back from the end of the log to recover the last sequence number.
# Grown on demand, so a record longer than this costs a second read rather than
# a wrong answer.
_TAIL_WINDOW: Final[int] = 4096

#: Discriminator on log lines. Comparisons and retractions share one file so that
#: `seq` is a single ordering over everything that happened, in the order it
#: happened — two files would need their orders reconciled on every read.
KIND_COMPARISON: Final[str] = "comparison"
KIND_RETRACTION: Final[str] = "retraction"
KIND_REVISION: Final[str] = "revision"
KIND_REMOVAL: Final[str] = "removal"
KIND_ASSIGNMENT: Final[str] = "assignment"

Clock = Callable[[], str]
LogEntry = Comparison | Retraction | RevisionAccepted | RemovalAccepted | BandsAssigned


def utc_now() -> str:
    """UTC ISO-8601 with a `Z` suffix, second precision.

    Second precision on purpose: these timestamps order a human's keypresses, and
    sub-second detail invites reading a precision into the log that the activity
    being recorded does not have.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """Write whole-file JSON with a stable key order.

    `sort_keys` is not cosmetic: two stores built from identical inputs must
    produce identical bytes, and dict insertion order would otherwise leak
    construction order into the file. Nor is writing LF: left to the platform,
    every line ends in its separator, and a store's bytes depend on the machine
    that wrote them (D35).
    """
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _loads(text: str, where: str) -> object:
    """Parse one JSON document, naming the file when it is not JSON at all.

    The narrowing helpers in `shapes` already turn a wrong *shape* into a named
    refusal; without this, a wrong *syntax* escapes as a `JSONDecodeError` with a
    stack trace pointing into the standard library. That case is not exotic: the
    log is flushed per record precisely so a crash cannot lose a judgment, and a
    crash during that write is exactly what leaves a truncated final line.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"{where} is not valid JSON: {exc}"
        raise StoreSchemaError(msg) from exc


def _read_json(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read {path.name}: {exc}"
        raise StoreSchemaError(msg) from exc
    return as_dict(_loads(text, path.name), path.name, error=StoreSchemaError)


class Store:
    """An open store directory.

    Construct via :meth:`create` or :meth:`open`; the initializer deliberately
    does no filesystem work, so an instance never half-exists.
    """

    def __init__(self, path: Path, *, clock: Clock = utc_now) -> None:
        self.path = path
        self._clock = clock

    # -- lifecycle ---------------------------------------------------------

    @staticmethod
    def _require_path(path: Path | None) -> Path:
        if path is None:
            msg = (
                "no store path supplied. There is no implicit default: a tool that "
                "writes findings text somewhere by default will eventually do so on a "
                "machine where those findings are real."
            )
            raise NoStorePathError(msg)
        return path

    @classmethod
    def create(
        cls,
        path: Path | None,
        *,
        anchor_set_version: str = "1",
        clock: Clock = utc_now,
        force: bool = False,
    ) -> Store:
        """Create a store directory, refusing to overwrite one that exists.

        `force` re-initializes an existing store, and still refuses once any
        judgment has been recorded. Creating rewrites `cuts.json` empty, so
        without this guard a re-run destroys the three band cuts — a deletion, in
        a store whose stated property is that nothing is ever deleted.
        """
        target = cls._require_path(path)
        meta_path = target / META_FILE
        if meta_path.is_file():
            if not force:
                msg = (
                    f"a store already exists at {target}. Creating would rewrite "
                    "cuts.json empty and discard the three band cuts; pass force to "
                    "re-initialize deliberately"
                )
                raise StoreExistsError(msg)
            if cls(target, clock=clock)._read_lines(target / LOG_FILE):
                msg = (
                    f"the store at {target} holds recorded judgments; re-initializing "
                    "would leave them referring to findings and cuts that no longer "
                    "exist. Point at a new path instead"
                )
                raise StoreExistsError(msg)
        target.mkdir(parents=True, exist_ok=True)
        store = cls(target, clock=clock)
        _write_json(
            target / META_FILE,
            {
                "schema_version": SCHEMA_VERSION,
                "anchor_set_version": anchor_set_version,
                "created_at": clock(),
            },
        )
        (target / FINDINGS_FILE).touch()
        (target / LOG_FILE).touch()
        _write_json(target / CUTS_FILE, {"cuts": []})
        return store

    @classmethod
    def open(cls, path: Path | None, *, clock: Clock = utc_now) -> Store:
        target = cls._require_path(path)
        meta_path = target / META_FILE
        if not meta_path.is_file():
            msg = f"no store at {target} (missing {META_FILE})"
            raise StoreSchemaError(msg)
        meta = _read_json(meta_path)
        found = meta.get("schema_version")
        if found != SCHEMA_VERSION:
            msg = (
                f"store schema version {found!r} is not {SCHEMA_VERSION!r}; refusing to "
                "write rather than guessing at an unfamiliar layout"
            )
            raise StoreSchemaError(msg)
        return cls(target, clock=clock)

    @property
    def anchor_set_version(self) -> str:
        value = _read_json(self.path / META_FILE).get("anchor_set_version")
        return str(value) if value is not None else "1"

    def put_load_summary(self, excluded_questions: Sequence[str]) -> None:
        """Record which entries were excluded as questions when findings were loaded.

        Persisted rather than recomputed because the exclusion happens against
        the *source document*, which the store does not keep: by the time these
        findings are here, the question rows are already gone. A rater who
        expected seventy findings and sees sixty-three admitted needs to be told
        why, and "seven were questions" is a different situation from "seven were
        malformed".
        """
        meta = _read_json(self.path / META_FILE)
        meta["excluded_questions"] = list(excluded_questions)
        _write_json(self.path / META_FILE, meta)

    def excluded_question_ids(self) -> tuple[str, ...]:
        raw = _read_json(self.path / META_FILE).get("excluded_questions", [])
        entries = as_list(raw, META_FILE, error=StoreSchemaError)
        return tuple(as_str(e, META_FILE, error=StoreSchemaError) for e in entries)

    def excluded_question_count(self) -> int:
        return len(self.excluded_question_ids())

    # -- findings ----------------------------------------------------------

    def put_findings(self, findings: Iterable[Finding]) -> None:
        """Replace the findings index.

        Whole-file rather than append: the index mirrors a document that is
        re-read in full each time, and an append-only index would accumulate
        superseded rows with no way to tell which is current.
        """
        lines = [
            json.dumps(
                {
                    "id": f.id,
                    "content_hash": f.content_hash,
                    "observation": f.observation,
                    "evidence": list(f.evidence),
                    "consequence": f.consequence,
                    "detectable_by": f.detectable_by.value,
                    "tier": f.tier.value,
                    "call_ref": f.call_ref,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            for f in findings
        ]
        body = "\n".join(lines)
        (self.path / FINDINGS_FILE).write_text(
            body + "\n" if body else "", encoding="utf-8", newline="\n"
        )

    def findings(self) -> tuple[Finding, ...]:
        out: list[Finding] = []
        where = FINDINGS_FILE
        for line in self._read_lines(self.path / FINDINGS_FILE):
            raw = as_dict(_loads(line, where), where, error=StoreSchemaError)
            evidence = as_list(
                field(raw, "evidence", where, error=StoreSchemaError), where, error=StoreSchemaError
            )
            out.append(
                Finding(
                    id=as_str(
                        field(raw, "id", where, error=StoreSchemaError),
                        where,
                        error=StoreSchemaError,
                    ),
                    content_hash=as_str(
                        field(raw, "content_hash", where, error=StoreSchemaError),
                        where,
                        error=StoreSchemaError,
                    ),
                    observation=as_str(
                        field(raw, "observation", where, error=StoreSchemaError),
                        where,
                        error=StoreSchemaError,
                    ),
                    evidence=tuple(as_str(e, where, error=StoreSchemaError) for e in evidence),
                    consequence=as_str(
                        field(raw, "consequence", where, error=StoreSchemaError),
                        where,
                        error=StoreSchemaError,
                    ),
                    detectable_by=DetectableBy(
                        as_str(
                            field(raw, "detectable_by", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        )
                    ),
                    tier=Tier(
                        as_str(
                            field(raw, "tier", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        )
                    ),
                    call_ref=as_str(raw.get("call_ref", ""), where, error=StoreSchemaError),
                )
            )
        return tuple(out)

    # -- the log -----------------------------------------------------------

    def _next_seq(self) -> int:
        """One past the last sequence number on disk, re-read at every append.

        Deliberately *not* cached. A cached counter desynchronizes the moment a
        second handle appends — two records then share a number, and since a
        retraction addresses a comparison by seq, retracting one withdraws every
        record carrying that number. That is silent corruption of the artefact
        this whole tool exists to produce.

        The read is a tail seek rather than a full parse, so the append stays
        O(1) in the log's length: the cost the cache was introduced to remove is
        removed here too, without the correctness price.
        """
        return self._last_seq() + 1

    def _last_seq(self) -> int:
        """The highest sequence number written, read from the log's final line.

        Records are appended in sequence order, so the last line carries the
        highest number; the window grows until a complete final line is in hand
        rather than assuming one fits.
        """
        path = self.path / LOG_FILE
        if not path.is_file():
            return 0
        with path.open("rb") as handle:
            handle.seek(0, io.SEEK_END)
            size = handle.tell()
            window = 0
            tail = b""
            while window < size:
                window = min(size, max(window * 4, _TAIL_WINDOW))
                handle.seek(size - window)
                tail = handle.read(window)
                # A newline before the final record proves it was not truncated
                # by the window itself.
                if b"\n" in tail.rstrip(b"\n"):
                    break
        lines = [line for line in tail.splitlines() if line.strip()]
        if not lines:
            return 0
        raw = as_dict(_loads(lines[-1].decode("utf-8"), LOG_FILE), LOG_FILE, error=StoreSchemaError)
        return as_int(
            field(raw, "seq", LOG_FILE, error=StoreSchemaError), LOG_FILE, error=StoreSchemaError
        )

    def _append_log(self, payload: dict[str, object]) -> None:
        """Append one line and flush it to disk before returning.

        The caller advances to the next pair on return, so a comparison that is
        merely buffered is a comparison a crash silently discards — and the rater
        would have no way to know which judgment vanished.
        """
        with (self.path / LOG_FILE).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()

    def append_comparison(
        self,
        *,
        left_id: str,
        right_id: str,
        outcome: Outcome,
        rater_id: str,
        session_id: str,
    ) -> Comparison:
        if left_id == right_id:
            msg = (
                f"cannot compare {left_id!r} with itself: a self-comparison carries no "
                "judgment but would still enter the win matrix and move the scale"
            )
            raise UnknownItemError(msg)
        known = {f.id for f in self.findings()}
        for item in (left_id, right_id):
            if item not in known:
                msg = f"no finding {item!r} in this store"
                raise UnknownItemError(msg)
        record = Comparison(
            seq=self._next_seq(),
            left_id=left_id,
            right_id=right_id,
            outcome=outcome,
            rater_id=rater_id,
            session_id=session_id,
            timestamp=self._clock(),
        )
        self._append_log(
            {
                "kind": KIND_COMPARISON,
                "seq": record.seq,
                "left_id": record.left_id,
                "right_id": record.right_id,
                "outcome": record.outcome.value,
                "rater_id": record.rater_id,
                "session_id": record.session_id,
                "timestamp": record.timestamp,
            }
        )
        return record

    def append_retraction(self, *, retracts_seq: int, rater_id: str, session_id: str) -> Retraction:
        """Withdraw a live comparison, refusing one that names nothing.

        A retraction of an already-retracted or nonexistent record is inert to
        the fit, which filters by a set — but the log's hash is published as
        provenance in every severity file, so it still changes the fingerprint of
        a history without changing what that history says.
        """
        live = {c.seq for c in self.active_comparisons()}
        if retracts_seq not in live:
            msg = (
                f"no live comparison at seq {retracts_seq} to retract. A retraction "
                "that names nothing still changes the log's hash, which is published "
                "as provenance"
            )
            raise RetractionError(msg)
        record = Retraction(
            seq=self._next_seq(),
            retracts_seq=retracts_seq,
            rater_id=rater_id,
            session_id=session_id,
            timestamp=self._clock(),
        )
        self._append_log(
            {
                "kind": KIND_RETRACTION,
                "seq": record.seq,
                "retracts_seq": record.retracts_seq,
                "rater_id": record.rater_id,
                "session_id": record.session_id,
                "timestamp": record.timestamp,
            }
        )
        return record

    def pending_revisions(self, incoming: Iterable[Finding]) -> tuple[Revision, ...]:
        """Findings whose text has changed since they were last stored.

        Only findings that have actually been *judged* are reported: a change to
        something nobody compared costs nothing, and reporting it would train a
        rater to wave the acceptance flag through by reflex.
        """
        stored = {f.id: f.content_hash for f in self.findings()}
        counts = self._judged_counts()

        found: list[Revision] = []
        for finding in incoming:
            previous = stored.get(finding.id)
            if previous is None or previous == finding.content_hash:
                continue
            judged = counts.get(finding.id, 0)
            if judged == 0:
                continue
            found.append(
                Revision(
                    finding_id=finding.id,
                    old_hash=previous,
                    new_hash=finding.content_hash,
                    comparisons=judged,
                )
            )
        return tuple(sorted(found, key=lambda r: r.finding_id))

    def append_revision(
        self, revision: Revision, *, rater_id: str, session_id: str
    ) -> RevisionAccepted:
        """Record that a human accepted a text change on a judged finding."""
        record = RevisionAccepted(
            seq=self._next_seq(),
            finding_id=revision.finding_id,
            old_hash=revision.old_hash,
            new_hash=revision.new_hash,
            comparisons=revision.comparisons,
            rater_id=rater_id,
            session_id=session_id,
            timestamp=self._clock(),
        )
        self._append_log(
            {
                "kind": KIND_REVISION,
                "seq": record.seq,
                "finding_id": record.finding_id,
                "old_hash": record.old_hash,
                "new_hash": record.new_hash,
                "comparisons": record.comparisons,
                "rater_id": record.rater_id,
                "session_id": record.session_id,
                "timestamp": record.timestamp,
            }
        )
        return record

    def _judged_counts(self) -> dict[str, int]:
        """How many live comparisons each finding appears in."""
        counts: dict[str, int] = {}
        for comparison in self.active_comparisons():
            for item in (comparison.left_id, comparison.right_id):
                counts[item] = counts.get(item, 0) + 1
        return counts

    def pending_removals(self, incoming: Iterable[Finding]) -> tuple[Removal, ...]:
        """Judged findings the incoming document no longer contains.

        The sibling of :meth:`pending_revisions`. That one guards findings whose
        text changed; nothing guarded findings that *vanished*, and the harm is
        the same shape through the adjacent door — the index is replaced whole,
        the comparisons stay in the log referring to an item the fit no longer
        knows, and the surviving partner's appearances silently fall.

        Only judged findings are reported, for the reason D18 gives: flagging a
        removal nobody compared would train a rater to wave the flag through.
        """
        arriving = {f.id for f in incoming}
        counts = self._judged_counts()
        found = [
            Removal(finding_id=f.id, old_hash=f.content_hash, comparisons=counts[f.id])
            for f in self.findings()
            if f.id not in arriving and counts.get(f.id, 0) > 0
        ]
        return tuple(sorted(found, key=lambda r: r.finding_id))

    def append_removal(
        self, removal: Removal, *, rater_id: str, session_id: str
    ) -> RemovalAccepted:
        """Record that a human accepted a judged finding leaving the document."""
        record = RemovalAccepted(
            seq=self._next_seq(),
            finding_id=removal.finding_id,
            old_hash=removal.old_hash,
            comparisons=removal.comparisons,
            rater_id=rater_id,
            session_id=session_id,
            timestamp=self._clock(),
        )
        self._append_log(
            {
                "kind": KIND_REMOVAL,
                "seq": record.seq,
                "finding_id": record.finding_id,
                "old_hash": record.old_hash,
                "comparisons": record.comparisons,
                "rater_id": record.rater_id,
                "session_id": record.session_id,
                "timestamp": record.timestamp,
            }
        )
        return record

    def append_assignment(
        self, assigned: Sequence[AssignedBand], *, rater_id: str, session_id: str
    ) -> BandsAssigned:
        """Record that a rater fixed every banded finding's band (D36).

        The whole set, in id order, so the record does not depend on how the
        bands arrived and the latest one alone is the frozen state.
        """
        record = BandsAssigned(
            seq=self._next_seq(),
            bands=tuple(sorted(assigned, key=lambda b: b.finding_id)),
            rater_id=rater_id,
            session_id=session_id,
            timestamp=self._clock(),
        )
        self._append_log(
            {
                "kind": KIND_ASSIGNMENT,
                "seq": record.seq,
                "bands": [
                    {
                        "finding_id": b.finding_id,
                        "band": b.band.value,
                        "content_hash": b.content_hash,
                    }
                    for b in record.bands
                ],
                "rater_id": record.rater_id,
                "session_id": record.session_id,
                "timestamp": record.timestamp,
            }
        )
        return record

    def assigned_bands(self) -> dict[str, Band]:
        """Each finding's band as the most recent assignment fixed it (D36).

        A finding whose removal was accepted after that assignment is left out:
        the removal is itself an audited acceptance, so its assignment lapses
        without a second one. Only an *accepted* removal does that. A finding that
        left the document unjudged, because every comparison involving it had
        been retracted, keeps its assignment, and the band it lost is proposed
        like any other change.
        """
        entries = self.log()
        latest = next((e for e in reversed(entries) if isinstance(e, BandsAssigned)), None)
        if latest is None:
            return {}
        lapsed = {
            e.finding_id for e in entries if isinstance(e, RemovalAccepted) and e.seq > latest.seq
        }
        return {b.finding_id: b.band for b in latest.bands if b.finding_id not in lapsed}

    def accepted_removals(self) -> tuple[RemovalAccepted, ...]:
        return tuple(e for e in self.log() if isinstance(e, RemovalAccepted))

    def accepted_revisions(self) -> tuple[RevisionAccepted, ...]:
        return tuple(e for e in self.log() if isinstance(e, RevisionAccepted))

    def log(self) -> tuple[LogEntry, ...]:
        """Everything recorded, in the order it happened."""
        out: list[LogEntry] = []
        where = LOG_FILE
        for line in self._read_lines(self.path / LOG_FILE):
            raw = as_dict(_loads(line, where), where, error=StoreSchemaError)
            kind = as_str(
                field(raw, "kind", where, error=StoreSchemaError), where, error=StoreSchemaError
            )
            seq = as_int(
                field(raw, "seq", where, error=StoreSchemaError), where, error=StoreSchemaError
            )
            rater = as_str(
                field(raw, "rater_id", where, error=StoreSchemaError), where, error=StoreSchemaError
            )
            session = as_str(
                field(raw, "session_id", where, error=StoreSchemaError),
                where,
                error=StoreSchemaError,
            )
            stamp = as_str(
                field(raw, "timestamp", where, error=StoreSchemaError),
                where,
                error=StoreSchemaError,
            )
            if kind == KIND_COMPARISON:
                out.append(
                    Comparison(
                        seq=seq,
                        left_id=as_str(
                            field(raw, "left_id", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        ),
                        right_id=as_str(
                            field(raw, "right_id", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        ),
                        outcome=Outcome(
                            as_str(
                                field(raw, "outcome", where, error=StoreSchemaError),
                                where,
                                error=StoreSchemaError,
                            )
                        ),
                        rater_id=rater,
                        session_id=session,
                        timestamp=stamp,
                    )
                )
            elif kind == KIND_REVISION:
                out.append(
                    RevisionAccepted(
                        seq=seq,
                        finding_id=as_str(
                            field(raw, "finding_id", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        ),
                        old_hash=as_str(
                            field(raw, "old_hash", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        ),
                        new_hash=as_str(
                            field(raw, "new_hash", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        ),
                        comparisons=as_int(
                            field(raw, "comparisons", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        ),
                        rater_id=rater,
                        session_id=session,
                        timestamp=stamp,
                    )
                )
            elif kind == KIND_RETRACTION:
                out.append(
                    Retraction(
                        seq=seq,
                        retracts_seq=as_int(
                            field(raw, "retracts_seq", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        ),
                        rater_id=rater,
                        session_id=session,
                        timestamp=stamp,
                    )
                )
            elif kind == KIND_REMOVAL:
                out.append(
                    RemovalAccepted(
                        seq=seq,
                        finding_id=as_str(
                            field(raw, "finding_id", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        ),
                        old_hash=as_str(
                            field(raw, "old_hash", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        ),
                        comparisons=as_int(
                            field(raw, "comparisons", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        ),
                        rater_id=rater,
                        session_id=session,
                        timestamp=stamp,
                    )
                )
            elif kind == KIND_ASSIGNMENT:
                out.append(
                    BandsAssigned(
                        seq=seq,
                        bands=tuple(
                            self._assigned_band(entry, where)
                            for entry in as_list(
                                field(raw, "bands", where, error=StoreSchemaError),
                                where,
                                error=StoreSchemaError,
                            )
                        ),
                        rater_id=rater,
                        session_id=session,
                        timestamp=stamp,
                    )
                )
            else:
                msg = f"{where}: unknown log entry kind {kind!r}"
                raise StoreSchemaError(msg)
        return tuple(out)

    def log_hash(self) -> str:
        """A content hash of the comparison log.

        Named in every severity file so a result can be tied to the exact set of
        judgments that produced it. Hashing the raw file rather than the parsed
        records is deliberate: it covers retractions and ordering too, so any
        change to the history changes the hash even when the active comparisons
        are unchanged.

        **Line endings are normalized first** (D35). A platform's separator is not
        part of the history, and hashing it named different hashes for the same
        judgments on Windows and on Linux. CRLF pairs become LF before hashing, so
        a log written partly by each hashes as the same log written clean, and
        ordering and retractions still count.
        """
        raw = (self.path / LOG_FILE).read_bytes() if (self.path / LOG_FILE).is_file() else b""
        return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()

    def active_comparisons(self) -> tuple[Comparison, ...]:
        """Comparisons with retractions applied.

        This is what the fit consumes. The retracted records remain in
        :meth:`log`, which is the point: the withdrawal is part of the history,
        not an erasure of it.
        """
        entries = self.log()
        retracted = {e.retracts_seq for e in entries if isinstance(e, Retraction)}
        return tuple(e for e in entries if isinstance(e, Comparison) and e.seq not in retracted)

    # -- cuts --------------------------------------------------------------

    def put_cuts(self, cuts: Sequence[Cut]) -> None:
        _write_json(
            self.path / CUTS_FILE,
            {
                "cuts": [
                    {
                        "name": c.name.value,
                        "above_id": c.above_id,
                        "below_id": c.below_id,
                        "calibration_note": c.calibration_note,
                    }
                    for c in cuts
                ]
            },
        )

    def cuts(self) -> tuple[Cut, ...]:
        where = CUTS_FILE
        raw = _read_json(self.path / CUTS_FILE)
        entries = as_list(raw.get("cuts", []), where, error=StoreSchemaError)
        out: list[Cut] = []
        for entry in entries:
            item = as_dict(entry, where, error=StoreSchemaError)
            out.append(
                Cut(
                    name=CutName(
                        as_str(
                            field(item, "name", where, error=StoreSchemaError),
                            where,
                            error=StoreSchemaError,
                        )
                    ),
                    above_id=as_str(
                        field(item, "above_id", where, error=StoreSchemaError),
                        where,
                        error=StoreSchemaError,
                    ),
                    below_id=as_str(
                        field(item, "below_id", where, error=StoreSchemaError),
                        where,
                        error=StoreSchemaError,
                    ),
                    calibration_note=as_str(
                        item.get("calibration_note", ""), where, error=StoreSchemaError
                    ),
                )
            )
        return tuple(out)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _assigned_band(entry: object, where: str) -> AssignedBand:
        item = as_dict(entry, where, error=StoreSchemaError)
        band = as_str(
            field(item, "band", where, error=StoreSchemaError), where, error=StoreSchemaError
        )
        if band not in {b.value for b in Band}:
            msg = f"{where}: an assignment names {band!r}, which is not a band"
            raise StoreSchemaError(msg)
        return AssignedBand(
            finding_id=as_str(
                field(item, "finding_id", where, error=StoreSchemaError),
                where,
                error=StoreSchemaError,
            ),
            band=Band(band),
            content_hash=as_str(
                field(item, "content_hash", where, error=StoreSchemaError),
                where,
                error=StoreSchemaError,
            ),
        )

    @staticmethod
    def _read_lines(path: Path) -> list[str]:
        if not path.is_file():
            return []
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
