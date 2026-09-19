"""Store tests.

The properties that matter here are the ones a later phase depends on without
re-checking: that nothing is ever deleted, that a comparison is on disk before
the caller moves on, and that reading a store back gives exactly what was
written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from comparative_judgment.core.anchors import write_anchor_set
from comparative_judgment.core.diagnostics import write_diagnostics
from comparative_judgment.core.errors import (
    NoStorePathError,
    StoreSchemaError,
    UnknownItemError,
)
from comparative_judgment.core.models import (
    AnchorSet,
    Comparison,
    Cut,
    CutName,
    DetectableBy,
    Finding,
    Outcome,
    Retraction,
    Tier,
    content_hash,
)
from comparative_judgment.core.session import Session
from comparative_judgment.core.severity import write_severity_file
from comparative_judgment.core.store import FINDINGS_FILE, LOG_FILE, META_FILE, Store


def _finding(fid: str, tier: Tier = Tier.DEFECT) -> Finding:
    evidence = (f"line 1: {fid}", f"line 9: {fid}")
    return Finding(
        id=fid,
        content_hash=content_hash(observation=fid, evidence=evidence, consequence="c"),
        observation=f"observation for {fid}",
        evidence=evidence,
        consequence=f"consequence for {fid}",
        detectable_by=DetectableBy.JUDGE,
        tier=tier,
    )


def _fixed_clock() -> str:
    return "2026-08-28T12:00:00Z"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store.create(tmp_path / "store", clock=_fixed_clock)
    s.put_findings([_finding("F-1"), _finding("F-2"), _finding("F-3")])
    return s


class TestStorePath:
    """There is no implicit store location."""

    def test_create_without_path_is_refused_by_name(self) -> None:
        with pytest.raises(NoStorePathError):
            Store.create(None)

    def test_open_without_path_is_refused_by_name(self) -> None:
        with pytest.raises(NoStorePathError):
            Store.open(None)

    def test_open_missing_store_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(StoreSchemaError):
            Store.open(tmp_path / "nothing-here")


class TestSchemaGuard:
    """An unrecognized layout refuses to write rather than guessing."""

    def test_unknown_schema_version_names_the_mismatch(self, tmp_path: Path) -> None:
        Store.create(tmp_path / "s", clock=_fixed_clock)
        meta_path = tmp_path / "s" / META_FILE
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["schema_version"] = "999"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        with pytest.raises(StoreSchemaError, match="999"):
            Store.open(tmp_path / "s")


class TestMalformedStore:
    """Well-formed JSON of the wrong SHAPE is a named refusal, not a KeyError.

    The store is append-only and shared across sessions, so the failure a reader
    sees should say which file and which field gave up.

    Scope worth stating, because this docstring used to claim more than the cases
    delivered: every case here feeds valid JSON that says the wrong thing.
    Syntactically invalid JSON — the corruption a crash actually produces, by
    truncating the line being flushed — lives in `test_refusals.py`, which is
    where it was missing entirely.
    """

    def test_unknown_log_kind_is_refused(self, store: Store) -> None:
        (store.path / LOG_FILE).write_text(
            json.dumps(
                {
                    "kind": "nonsense",
                    "seq": 1,
                    "rater_id": "r",
                    "session_id": "s",
                    "timestamp": "t",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with pytest.raises(StoreSchemaError, match="nonsense"):
            store.log()

    def test_missing_field_names_the_field(self, store: Store) -> None:
        (store.path / LOG_FILE).write_text(
            json.dumps({"kind": "comparison", "seq": 1}) + "\n", encoding="utf-8"
        )
        with pytest.raises(StoreSchemaError, match="rater_id"):
            store.log()

    def test_wrong_type_names_the_file(self, store: Store) -> None:
        (store.path / "cuts.json").write_text(json.dumps({"cuts": "not-a-list"}), encoding="utf-8")
        with pytest.raises(StoreSchemaError, match=r"cuts\.json"):
            store.cuts()


class TestFindingsRoundTrip:
    def test_findings_survive_a_round_trip(self, store: Store) -> None:
        reopened = Store.open(store.path)
        assert reopened.findings() == store.findings()

    def test_evidence_fragments_stay_separate(self, store: Store) -> None:
        """Three fragments in must be three fragments out.

        This is the property that ruled out a Markdown table as the interchange
        format; losing it here would give that decision away silently.
        """
        f = Finding(
            id="F-9",
            content_hash="h",
            observation="o",
            evidence=("frag one", "frag two", "frag three"),
            consequence="c",
            detectable_by=DetectableBy.ASSERT,
            tier=Tier.DEFECT,
        )
        store.put_findings([f])
        (restored,) = Store.open(store.path).findings()
        assert restored.evidence == ("frag one", "frag two", "frag three")


class TestAppendOnlyLog:
    def test_comparison_round_trips(self, store: Store) -> None:
        store.append_comparison(
            left_id="F-1", right_id="F-2", outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
        (entry,) = Store.open(store.path).log()
        assert isinstance(entry, Comparison)
        assert (entry.left_id, entry.right_id, entry.outcome) == ("F-1", "F-2", Outcome.LEFT)

    def test_comparison_is_on_disk_before_the_call_returns(self, store: Store) -> None:
        """Persisted, not buffered.

        The caller advances to the next pair on return, so a merely-buffered
        comparison is one a crash discards with the rater none the wiser. Reading
        the file directly, without closing the store, is the assertion.
        """
        store.append_comparison(
            left_id="F-1", right_id="F-2", outcome=Outcome.RIGHT, rater_id="r", session_id="s"
        )
        on_disk = (store.path / LOG_FILE).read_text(encoding="utf-8")
        assert '"outcome": "right"' in on_disk

    def test_retraction_removes_from_the_fit_but_not_from_the_log(self, store: Store) -> None:
        first = store.append_comparison(
            left_id="F-1", right_id="F-2", outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
        store.append_comparison(
            left_id="F-2", right_id="F-3", outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
        store.append_retraction(retracts_seq=first.seq, rater_id="r", session_id="s")

        reopened = Store.open(store.path)
        assert len(reopened.log()) == 3
        assert any(isinstance(e, Retraction) for e in reopened.log())
        active = reopened.active_comparisons()
        assert [c.seq for c in active] == [2]

    def test_seq_is_dense_and_increasing(self, store: Store) -> None:
        for _ in range(3):
            store.append_comparison(
                left_id="F-1", right_id="F-2", outcome=Outcome.TIE, rater_id="r", session_id="s"
            )
        assert [e.seq for e in store.log()] == [1, 2, 3]

    def test_comparison_against_an_unknown_item_is_refused(self, store: Store) -> None:
        with pytest.raises(UnknownItemError, match="F-404"):
            store.append_comparison(
                left_id="F-1", right_id="F-404", outcome=Outcome.LEFT, rater_id="r", session_id="s"
            )


class TestCuts:
    def test_cuts_round_trip_as_anchor_pairs(self, store: Store) -> None:
        cuts = (
            Cut(CutName.CRITICAL_HIGH, "F-1", "F-2", "harm persists after the call"),
            Cut(CutName.HIGH_MEDIUM, "F-2", "F-3"),
        )
        store.put_cuts(cuts)
        assert Store.open(store.path).cuts() == cuts

    def test_a_cut_stores_no_scale_value(self, store: Store) -> None:
        """A cut is two ids, never a number.

        If a threshold ever appears in this file, the meaning of a cut has
        silently changed from "between these two findings" to "at this value" —
        which a pairwise scale cannot support across refits.
        """
        store.put_cuts([Cut(CutName.MEDIUM_LOW, "F-2", "F-3")])
        raw = json.loads((store.path / "cuts.json").read_text(encoding="utf-8"))
        assert set(raw["cuts"][0]) == {"name", "above_id", "below_id", "calibration_note"}


class TestDeterminism:
    def test_two_stores_built_identically_have_identical_bytes(self, tmp_path: Path) -> None:
        """Same inputs, same files.

        Byte-identity here is what makes the reproducibility claim testable
        further up: if the store's own serialization wandered, no downstream
        determinism assertion would mean anything.
        """
        digests: list[str] = []
        for name in ("a", "b"):
            s = Store.create(tmp_path / name, clock=_fixed_clock)
            s.put_findings([_finding("F-1"), _finding("F-2")])
            s.append_comparison(
                left_id="F-1", right_id="F-2", outcome=Outcome.LEFT, rater_id="r", session_id="s"
            )
            digests.append(
                "".join(
                    (s.path / f).read_text(encoding="utf-8")
                    for f in (META_FILE, "findings.jsonl", LOG_FILE, "cuts.json")
                )
            )
        assert digests[0] == digests[1]


class TestLineEndings:
    """A store's bytes and its log hash do not depend on the platform (D35)."""

    def test_the_log_hash_does_not_depend_on_the_line_endings_that_wrote_the_log(
        self, store: Store
    ) -> None:
        """The same history names the same hash, on every platform.

        The hash covers the raw bytes so that ordering and retractions count, and
        a platform's line separator is neither. The log is written with LF, then
        with CRLF, the endings a Windows writer used to produce, then with one
        CRLF among LF lines, and all three must name one hash.
        """
        first = store.append_comparison(
            left_id="F-1", right_id="F-2", outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
        store.append_comparison(
            left_id="F-2", right_id="F-3", outcome=Outcome.TIE, rater_id="r", session_id="s"
        )
        store.append_retraction(retracts_seq=first.seq, rater_id="r", session_id="s")
        log = store.path / LOG_FILE
        lf = log.read_bytes().replace(b"\r\n", b"\n")
        assert lf.count(b"\n") == 3, "the log does not hold the three records written"

        log.write_bytes(lf)
        written_with_lf = store.log_hash()
        log.write_bytes(lf.replace(b"\n", b"\r\n"))
        assert store.log_hash() == written_with_lf, "a CRLF log names a different hash"
        log.write_bytes(lf.replace(b"\n", b"\r\n", 1))
        assert store.log_hash() == written_with_lf, "a log of mixed endings names a different hash"

    def test_every_file_is_written_with_lf_whatever_the_platform(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Asked for, and not merely observed.

        A write that names no line ending takes the platform's, so on Linux it
        produces LF whether or not the writer asked, and a check of the bytes
        alone passes there with the defect in place. Every writing call is
        recorded and must ask for LF; the bytes are checked as well, which is how
        the defect looked on the machine that found it.
        """
        requested: list[tuple[str, str | None]] = []
        original_write_text = Path.write_text
        original_open = Path.open

        def write_text(
            self: Path,
            data: str,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> int:
            requested.append((self.name, newline))
            return original_write_text(
                self, data, encoding=encoding, errors=errors, newline=newline
            )

        def open_(
            self: Path,
            mode: str = "r",
            buffering: int = -1,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> object:
            if "w" in mode or "a" in mode:
                requested.append((self.name, newline))
            return original_open(self, mode, buffering, encoding, errors, newline)

        monkeypatch.setattr(Path, "write_text", write_text)
        monkeypatch.setattr(Path, "open", open_)
        written = Store.create(tmp_path / "store", clock=_fixed_clock)
        written.put_findings([_finding("F-1"), _finding("F-2"), _finding("F-3")])
        written.append_comparison(
            left_id="F-1", right_id="F-2", outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
        written.put_cuts([Cut(CutName.MEDIUM_LOW, "F-2", "F-3")])
        write_severity_file(tmp_path / "severity.json", assignments=(), store=written)
        # The two files phase 2 writes: D35 covers every writer, not only the first four.
        diagnostics = Session(written, rater_id="r").diagnostics()
        write_diagnostics(tmp_path / "diagnostics.json", diagnostics)
        write_anchor_set(
            tmp_path / "anchors.json",
            AnchorSet(
                version="v",
                source_log_hash="h",
                findings=(_finding("F-1"),),
                comparisons=(),
                cuts=(),
            ),
        )
        monkeypatch.undo()

        names = {name for name, _ in requested}
        outputs = ("severity.json", "diagnostics.json", "anchors.json")
        assert {META_FILE, "findings.jsonl", LOG_FILE, "cuts.json", *outputs} <= names
        assert [entry for entry in requested if entry[1] != "\n"] == []
        for path in (*written.path.iterdir(), *(tmp_path / name for name in outputs)):
            assert b"\r" not in path.read_bytes(), f"{path.name} carries a carriage return"


class TestParseMemo:
    """D45: parsed once per change, and never trusted without looking (D19's lesson)."""

    def test_a_handle_sees_another_handles_append(self, store: Store) -> None:
        store.put_findings([_finding("F-1"), _finding("F-2")])
        other = Store.open(store.path, clock=_fixed_clock)
        assert store.log() == ()
        other.append_comparison(
            left_id="F-1", right_id="F-2", outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
        assert len(store.log()) == 1, "a memo that missed another handle's append"

    def test_a_handle_sees_another_handles_findings(self, store: Store) -> None:
        store.put_findings([_finding("F-1")])
        assert [f.id for f in store.findings()] == ["F-1"]
        Store.open(store.path).put_findings([_finding("F-1"), _finding("F-2")])
        assert [f.id for f in store.findings()] == ["F-1", "F-2"]

    def test_an_unchanged_file_is_not_parsed_again(self, store: Store) -> None:
        store.put_findings([_finding("F-1"), _finding("F-2")])
        store.append_comparison(
            left_id="F-1", right_id="F-2", outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
        assert store.log() is store.log()
        assert store.document_findings() is store.document_findings()

    def test_a_write_through_the_handle_is_seen_at_once(self, store: Store) -> None:
        store.put_findings([_finding("F-1"), _finding("F-2")])
        first = store.findings()
        store.put_findings([_finding("F-2")])
        assert store.findings() != first


class TestLineSplitting:
    def test_a_line_separator_inside_a_record_does_not_split_it(self, store: Store) -> None:
        """`str.splitlines()` splits on U+2028 and U+0085, which `json.dumps` writes raw."""
        text = "before" + chr(0x2028) + "after" + chr(0x85) + "end"
        finding = Finding(
            id="F-1",
            content_hash=content_hash(observation=text, evidence=("e",), consequence="c"),
            observation=text,
            evidence=("e",),
            consequence="c",
            detectable_by=DetectableBy.JUDGE,
            tier=Tier.DEFECT,
        )
        store.put_findings([finding])
        raw = (store.path / FINDINGS_FILE).read_text(encoding="utf-8")
        assert len(raw.splitlines()) > 1, "precondition: the old reader would have split it"
        assert Store.open(store.path).findings()[0].observation == text

    def test_an_unknown_outcome_in_the_log_is_a_named_refusal(self, store: Store) -> None:
        store.put_findings([_finding("F-1"), _finding("F-2")])
        store.append_comparison(
            left_id="F-1", right_id="F-2", outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
        log = store.path / LOG_FILE
        log.write_text(
            log.read_text(encoding="utf-8").replace('"left"', '"sideways"'),
            encoding="utf-8",
            newline="\n",
        )
        with pytest.raises(StoreSchemaError, match="sideways"):
            Store.open(store.path).log()


class TestTheMemoAcrossOwnAppends:
    """An own append extends the memo only on proof, and the result equals a fresh parse."""

    def test_an_extended_memo_equals_a_fresh_parse(self, store: Store) -> None:
        store.put_findings([_finding("F-1"), _finding("F-2"), _finding("F-3")])
        store.log()
        first = store.append_comparison(
            left_id="F-1", right_id="F-2", outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
        store.append_comparison(
            left_id="F-2", right_id="F-3", outcome=Outcome.TIE, rater_id="r", session_id="s"
        )
        store.append_retraction(retracts_seq=first.seq, rater_id="r", session_id="s")
        assert store._log_memo is not None, "the memo was dropped rather than extended"
        assert store.log() == Store.open(store.path).log()

    def test_an_interleaved_append_drops_the_memo_rather_than_extending_it(
        self, store: Store, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Another handle's line lands between this handle's stat and its write."""
        store.put_findings([_finding("F-1"), _finding("F-2")])
        store.log()
        other = Store.open(store.path, clock=_fixed_clock)
        original_open = Path.open
        interleaved: list[bool] = []

        def open_(
            self: Path,
            mode: str = "r",
            buffering: int = -1,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> object:
            if mode == "a" and self.name == LOG_FILE and not interleaved:
                interleaved.append(True)
                other.append_comparison(
                    left_id="F-2",
                    right_id="F-1",
                    outcome=Outcome.LEFT,
                    rater_id="o",
                    session_id="o",
                )
            return original_open(self, mode, buffering, encoding, errors, newline)

        monkeypatch.setattr(Path, "open", open_)
        store.append_comparison(
            left_id="F-1", right_id="F-2", outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
        monkeypatch.undo()
        assert interleaved, "precondition: the other handle's append ran"
        assert len(store.log()) == 2
        assert store.log() == Store.open(store.path).log()
