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
      comparisons.jsonl  append-only; comparisons and retractions interleaved
      cuts.json          the three band cuts, as anchor pairs

JSONL for the log because append-only is the whole point. JSON for machine state.
YAML appears nowhere in here: it is the format of the *human-authored* findings
document this store reads from, not of the store itself.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from comparative_judgment.core.errors import (
    NoStorePathError,
    StoreSchemaError,
    UnknownItemError,
)
from comparative_judgment.core.models import (
    SCHEMA_VERSION,
    Comparison,
    Cut,
    CutName,
    DetectableBy,
    Finding,
    Outcome,
    Retraction,
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

#: Discriminator on log lines. Comparisons and retractions share one file so that
#: `seq` is a single ordering over everything that happened, in the order it
#: happened — two files would need their orders reconciled on every read.
KIND_COMPARISON: Final[str] = "comparison"
KIND_RETRACTION: Final[str] = "retraction"

Clock = Callable[[], str]


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
    construction order into the file.
    """
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, object]:
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    return as_dict(loaded, path.name, error=StoreSchemaError)


class Store:
    """An open store directory.

    Construct via :meth:`create` or :meth:`open`; the initialiser deliberately
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
    ) -> Store:
        target = cls._require_path(path)
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
        (self.path / FINDINGS_FILE).write_text(body + "\n" if body else "", encoding="utf-8")

    def findings(self) -> tuple[Finding, ...]:
        out: list[Finding] = []
        where = FINDINGS_FILE
        for line in self._read_lines(self.path / FINDINGS_FILE):
            raw = as_dict(json.loads(line), where, error=StoreSchemaError)
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
        return len(self._read_lines(self.path / LOG_FILE)) + 1

    def _append_log(self, payload: dict[str, object]) -> None:
        """Append one line and flush it to disk before returning.

        The caller advances to the next pair on return, so a comparison that is
        merely buffered is a comparison a crash silently discards — and the rater
        would have no way to know which judgment vanished.
        """
        with (self.path / LOG_FILE).open("a", encoding="utf-8") as handle:
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

    def log(self) -> tuple[Comparison | Retraction, ...]:
        """Everything recorded, in the order it happened."""
        out: list[Comparison | Retraction] = []
        where = LOG_FILE
        for line in self._read_lines(self.path / LOG_FILE):
            raw = as_dict(json.loads(line), where, error=StoreSchemaError)
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
        """
        raw = (self.path / LOG_FILE).read_bytes() if (self.path / LOG_FILE).is_file() else b""
        return hashlib.sha256(raw).hexdigest()

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
    def _read_lines(path: Path) -> list[str]:
        if not path.is_file():
            return []
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
