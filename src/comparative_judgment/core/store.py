"""The store: an append-only comparison log plus the findings it refers to.

The log *is* the product. Orderings, bands and severities are all derived from
it, which is why nothing here ever rewrites or deletes a comparison — a
retraction is an appended record naming what it withdraws. That is what lets a
result be recomputed from the inputs it names, and what lets a later phase fit a
different model over the same judgments without re-asking a single question.

Layout::

    <store>/
      meta.json          schema version, anchor-set version, created_at
      findings.jsonl     findings admitted from the findings document, one per line
      comparisons.jsonl  append-only; comparisons, retractions, accepted revisions
                         and removals, band assignments and anchor-set imports,
                         interleaved
      cuts.json          the three band cuts, as anchor pairs

JSONL for the log because append-only is the whole point. JSON for machine state.
YAML appears nowhere in here: it is the format of the *human-authored* findings
document this store reads from, not of the store itself.

Findings arrive by two routes, and they are kept apart (D41). The findings index
mirrors the document and is rewritten whole by every load; findings an anchor-set
import brings live in its record in the log, so a load cannot drop them and the
log hash covers their text. `findings()` is both.

**Parsing is memoized, and the memo is never trusted without looking (D45).** A
keypress used to parse the log about eight times, which was most of its cost. Each
read now takes the file's size and modification time *first*, then parses, and
keeps the result under that key; the next call compares the key again before using
it. So another handle's append -- which always changes the size -- is seen, which
is D19's lesson about caching anything a second handle can change. A handle's own
append extends the memo only when the file grew by exactly the bytes it wrote,
which proves nobody else appended in between; otherwise the memo is dropped.
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
    AnchorComparison,
    AssignedBand,
    Band,
    BandsAssigned,
    Comparison,
    Cut,
    CutName,
    DetectableBy,
    Finding,
    ImportRecorded,
    Outcome,
    Removal,
    RemovalAccepted,
    Retraction,
    Revision,
    RevisionAccepted,
    Tier,
)
from comparative_judgment.core.shapes import (
    ErrorType,
    as_dict,
    as_enum,
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
KIND_IMPORT: Final[str] = "import"

#: The session id an import's own record is written under, beside `load` and
#: `assign`. The comparisons it brings keep the session ids they were made in.
IMPORT_SESSION_ID: Final[str] = "import"

Clock = Callable[[], str]
LogEntry = (
    Comparison | Retraction | RevisionAccepted | RemovalAccepted | BandsAssigned | ImportRecorded
)
_StatKey = tuple[int, int] | None


def finding_payload(finding: Finding) -> dict[str, object]:
    """A finding as the findings index, an import record and an anchor-set file write it."""
    return {
        "id": finding.id,
        "content_hash": finding.content_hash,
        "observation": finding.observation,
        "evidence": list(finding.evidence),
        "consequence": finding.consequence,
        "detectable_by": finding.detectable_by.value,
        "tier": finding.tier.value,
        "call_ref": finding.call_ref,
    }


def parse_finding(entry: object, where: str, *, error: ErrorType) -> Finding:
    """The inverse of :func:`finding_payload`, refusing by name whatever does not fit."""
    raw = as_dict(entry, where, error=error)

    def text(key: str) -> str:
        return as_str(field(raw, key, where, error=error), where, error=error)

    evidence = as_list(field(raw, "evidence", where, error=error), where, error=error)
    return Finding(
        id=text("id"),
        content_hash=text("content_hash"),
        observation=text("observation"),
        evidence=tuple(as_str(e, where, error=error) for e in evidence),
        consequence=text("consequence"),
        detectable_by=as_enum(
            field(raw, "detectable_by", where, error=error), DetectableBy, where, error=error
        ),
        tier=as_enum(field(raw, "tier", where, error=error), Tier, where, error=error),
        call_ref=as_str(raw.get("call_ref", ""), where, error=error),
    )


def cut_payload(cut: Cut) -> dict[str, object]:
    return {
        "name": cut.name.value,
        "above_id": cut.above_id,
        "below_id": cut.below_id,
        "calibration_note": cut.calibration_note,
    }


def parse_cut(entry: object, where: str, *, error: ErrorType) -> Cut:
    item = as_dict(entry, where, error=error)
    return Cut(
        name=as_enum(field(item, "name", where, error=error), CutName, where, error=error),
        above_id=as_str(field(item, "above_id", where, error=error), where, error=error),
        below_id=as_str(field(item, "below_id", where, error=error), where, error=error),
        calibration_note=as_str(item.get("calibration_note", ""), where, error=error),
    )


def split_lines(text: str) -> list[str]:
    """Non-blank lines, split on LF alone.

    Not `str.splitlines()`, which also splits on U+2028, U+2029, U+0085 and five
    other characters that `json.dumps(ensure_ascii=False)` writes raw inside a
    string. A finding whose text held one would have its record cut in two and
    refused as invalid JSON -- in the findings index, recoverable by reloading, and
    in the log, where findings now live inside import records, permanently. A
    trailing carriage return from a store written before D35 is JSON whitespace,
    so such a line still parses.
    """
    return [line for line in text.split("\n") if line.strip()]


def _stat_key(path: Path) -> _StatKey:
    """A file's size and modification time, or None when it does not exist."""
    try:
        status = path.stat()
    except FileNotFoundError:
        return None
    return (status.st_size, status.st_mtime_ns)


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
        # Parsed files, each under the stat key taken before it was read (D45).
        self._log_memo: tuple[_StatKey, tuple[LogEntry, ...]] | None = None
        self._findings_memo: tuple[_StatKey, tuple[Finding, ...]] | None = None

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
        """The most recent anchor set imported into this store, or meta.json's value.

        Derived from the log rather than written into meta.json (D41), so it cannot
        disagree with the import record that set it, and an interrupted import
        cannot leave it half-changed. A store that never imports keeps the "1" it
        was created with, so its run ids do not move.
        """
        imported = self.imports()
        if imported:
            return imported[-1].anchor_set_version
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
        superseded rows with no way to tell which is current. Findings an import
        brought are not in it and are not touched by it.
        """
        lines = [
            json.dumps(finding_payload(f), sort_keys=True, ensure_ascii=False) for f in findings
        ]
        body = "\n".join(lines)
        (self.path / FINDINGS_FILE).write_text(
            body + "\n" if body else "", encoding="utf-8", newline="\n"
        )
        self._findings_memo = None

    def document_findings(self) -> tuple[Finding, ...]:
        """The findings the findings document supplied, as the index holds them."""
        path = self.path / FINDINGS_FILE
        key = _stat_key(path)
        if self._findings_memo is not None and self._findings_memo[0] == key:
            return self._findings_memo[1]
        where = FINDINGS_FILE
        parsed = tuple(
            parse_finding(_loads(line, where), where, error=StoreSchemaError)
            for line in self._read_lines(path)
        )
        self._findings_memo = (key, parsed)
        return parsed

    def imported_findings(self) -> tuple[Finding, ...]:
        """Findings anchor-set imports added, in the order they were imported."""
        return tuple(f for record in self.imports() for f in record.findings)

    def findings(self) -> tuple[Finding, ...]:
        """Every finding in the store: the document's, then any imports added.

        An identifier held by both is held once, as the document states it. That
        can only happen with identical text, since both a load and an import refuse
        the same identifier with different text.
        """
        documented = self.document_findings()
        held = {f.id for f in documented}
        return documented + tuple(f for f in self.imported_findings() if f.id not in held)

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

    def _append_log(self, payload: dict[str, object], entry: LogEntry) -> None:
        """Append one line and flush it to disk before returning.

        The caller advances to the next pair on return, so a comparison that is
        merely buffered is a comparison a crash silently discards — and the rater
        would have no way to know which judgment vanished.

        **The parse memo is extended rather than dropped, but only on proof (D45).**
        Re-parsing the whole log after every keypress's own append was what the
        memo existed to stop. So when the memo matched the file just before this
        write, and the file afterwards is larger by exactly the bytes written, no
        other handle can have appended in between -- appends only grow a file --
        and the memo gains this entry. Any other outcome drops the memo, and the
        next read parses the file, which is D19's rule: nothing a second handle can
        change is trusted without looking.
        """
        path = self.path / LOG_FILE
        line = (json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        before = _stat_key(path)
        current = self._log_memo is not None and self._log_memo[0] == before
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line.decode("utf-8"))
            handle.flush()
        after = _stat_key(path)
        if (
            current
            and self._log_memo is not None
            and before is not None
            and after is not None
            and after[0] == before[0] + len(line)
        ):
            self._log_memo = (after, (*self._log_memo[1], entry))
        else:
            self._log_memo = None

    @staticmethod
    def _comparison_payload(record: Comparison) -> dict[str, object]:
        """A comparison as the log writes it; `origin` only when it has one.

        Absent rather than empty for a judgment made in this store, so every line
        written before imports existed keeps its bytes, and so do the log hash and
        the run ids derived from it.
        """
        payload: dict[str, object] = {
            "kind": KIND_COMPARISON,
            "seq": record.seq,
            "left_id": record.left_id,
            "right_id": record.right_id,
            "outcome": record.outcome.value,
            "rater_id": record.rater_id,
            "session_id": record.session_id,
            "timestamp": record.timestamp,
        }
        if record.origin:
            payload["origin"] = record.origin
        return payload

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
        self._append_log(self._comparison_payload(record), record)
        return record

    def append_import(
        self,
        *,
        anchor_set_version: str,
        source_log_hash: str,
        findings: Sequence[Finding],
        shared: Sequence[str],
        cuts: Sequence[Cut],
        appended: int,
        skipped: int,
        rater_id: str,
    ) -> ImportRecorded:
        """Record that an anchor set is being imported, before its comparisons (D41).

        The caller has validated everything; this writes. First, so the findings
        exist before anything refers to them, and so `appended` makes an
        interruption part-way through the comparisons exactly detectable.
        """
        record = ImportRecorded(
            seq=self._next_seq(),
            anchor_set_version=anchor_set_version,
            source_log_hash=source_log_hash,
            findings=tuple(sorted(findings, key=lambda f: f.id)),
            shared=tuple(sorted(shared)),
            cuts=tuple(cuts),
            appended=appended,
            skipped=skipped,
            rater_id=rater_id,
            session_id=IMPORT_SESSION_ID,
            timestamp=self._clock(),
        )
        self._append_log(
            {
                "kind": KIND_IMPORT,
                "seq": record.seq,
                "anchor_set_version": record.anchor_set_version,
                "source_log_hash": record.source_log_hash,
                "findings": [finding_payload(f) for f in record.findings],
                "shared": list(record.shared),
                "cuts": [cut_payload(c) for c in record.cuts],
                "appended": record.appended,
                "skipped": record.skipped,
                "rater_id": record.rater_id,
                "session_id": record.session_id,
                "timestamp": record.timestamp,
            },
            record,
        )
        return record

    def append_imported_comparisons(
        self, comparisons: Sequence[AnchorComparison], *, origin: str
    ) -> tuple[Comparison, ...]:
        """Append an anchor set's comparisons as they were made, tagged with its version.

        Rater, session and timestamp are the original judgment's, so attribution
        survives the move (D4) and phase 3 can separate raters again. Not checked
        against the findings one record at a time, as `append_comparison` is: the
        import validated them against the merged store once, and its record,
        written first, is what makes their findings known.
        """
        out: list[Comparison] = []
        for comparison in comparisons:
            record = Comparison(
                seq=self._next_seq(),
                left_id=comparison.left_id,
                right_id=comparison.right_id,
                outcome=comparison.outcome,
                rater_id=comparison.rater_id,
                session_id=comparison.session_id,
                timestamp=comparison.timestamp,
                origin=origin,
            )
            self._append_log(self._comparison_payload(record), record)
            out.append(record)
        return tuple(out)

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
            },
            record,
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
            },
            record,
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

        Only the document's findings can leave it. A finding an import added was
        never in the document, and one the document also held with identical text
        stays in the store through the import when the document drops it, so
        neither is a removal (D41).
        """
        arriving = {f.id for f in incoming}
        counts = self._judged_counts()
        provided = {f.id for f in self.imported_findings()}
        found = [
            Removal(finding_id=f.id, old_hash=f.content_hash, comparisons=counts[f.id])
            for f in self.document_findings()
            if f.id not in arriving and f.id not in provided and counts.get(f.id, 0) > 0
        ]
        return tuple(sorted(found, key=lambda r: r.finding_id))

    def import_collisions(self, incoming: Iterable[Finding]) -> tuple[tuple[str, str, str], ...]:
        """Document findings that reuse an imported finding's identifier with other text.

        Returned as `(id, imported hash, incoming hash)`. A load refuses these by
        name (D41): accepting one as a revision would rewrite text another store's
        judgments were made against, and an identifier cannot name two texts.
        """
        imported = {f.id: f.content_hash for f in self.imported_findings()}
        return tuple(
            sorted(
                (f.id, imported[f.id], f.content_hash)
                for f in incoming
                if f.id in imported and imported[f.id] != f.content_hash
            )
        )

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
            },
            record,
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
            },
            record,
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

    def imports(self) -> tuple[ImportRecorded, ...]:
        """Every anchor-set import recorded, in the order they happened."""
        return tuple(e for e in self.log() if isinstance(e, ImportRecorded))

    def incomplete_imports(self) -> tuple[ImportRecorded, ...]:
        """Imports whose record was written and whose work was not finished (D41).

        Exact, because the record names how many comparisons follow it: fewer
        comparison records carrying its version than that is an interruption part-
        way through them. And a store holding an import and no cuts was interrupted
        before adopting them, since only the first import into a store without cuts
        adopts any, and nothing can clear cuts once set -- so it is that import.
        Retracted records count, since a retraction does not un-append one.
        """
        records = self.imports()
        if not records:
            return ()
        carried: dict[str, int] = {}
        for entry in self.log():
            if isinstance(entry, Comparison) and entry.origin:
                carried[entry.origin] = carried.get(entry.origin, 0) + 1
        short = [r for r in records if carried.get(r.anchor_set_version, 0) < r.appended]
        if not self.cuts() and records[-1] not in short:
            short.append(records[-1])
        return tuple(short)

    def log(self) -> tuple[LogEntry, ...]:
        """Everything recorded, in the order it happened.

        Parsed once per change to the file rather than once per call (D45): the
        stat key is taken before the read, so an append by another handle between
        the two leaves a key that no longer matches and the next call reads again.
        """
        path = self.path / LOG_FILE
        key = _stat_key(path)
        if self._log_memo is not None and self._log_memo[0] == key:
            return self._log_memo[1]
        parsed = self._parse_log(path)
        self._log_memo = (key, parsed)
        return parsed

    def _parse_log(self, path: Path) -> tuple[LogEntry, ...]:
        out: list[LogEntry] = []
        where = LOG_FILE
        for line in self._read_lines(path):
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
                        outcome=as_enum(
                            field(raw, "outcome", where, error=StoreSchemaError),
                            Outcome,
                            where,
                            error=StoreSchemaError,
                        ),
                        rater_id=rater,
                        session_id=session,
                        timestamp=stamp,
                        origin=as_str(raw.get("origin", ""), where, error=StoreSchemaError),
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
            elif kind == KIND_IMPORT:
                out.append(self._import_record(raw, seq, rater, session, stamp, where))
            else:
                msg = f"{where}: unknown log entry kind {kind!r}"
                raise StoreSchemaError(msg)
        return tuple(out)

    @staticmethod
    def _import_record(
        raw: dict[str, object], seq: int, rater: str, session: str, stamp: str, where: str
    ) -> ImportRecorded:
        err = StoreSchemaError

        def listed(key: str) -> list[object]:
            return as_list(field(raw, key, where, error=err), where, error=err)

        def number(key: str) -> int:
            return as_int(field(raw, key, where, error=err), where, error=err)

        def text(key: str) -> str:
            return as_str(field(raw, key, where, error=err), where, error=err)

        return ImportRecorded(
            seq=seq,
            anchor_set_version=text("anchor_set_version"),
            source_log_hash=text("source_log_hash"),
            findings=tuple(parse_finding(f, where, error=err) for f in listed("findings")),
            shared=tuple(as_str(s, where, error=err) for s in listed("shared")),
            cuts=tuple(parse_cut(c, where, error=err) for c in listed("cuts")),
            appended=number("appended"),
            skipped=number("skipped"),
            rater_id=rater,
            session_id=session,
            timestamp=stamp,
        )

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
        _write_json(self.path / CUTS_FILE, {"cuts": [cut_payload(c) for c in cuts]})

    def cuts(self) -> tuple[Cut, ...]:
        where = CUTS_FILE
        raw = _read_json(self.path / CUTS_FILE)
        entries = as_list(raw.get("cuts", []), where, error=StoreSchemaError)
        return tuple(parse_cut(entry, where, error=StoreSchemaError) for entry in entries)

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
        return split_lines(path.read_text(encoding="utf-8"))
