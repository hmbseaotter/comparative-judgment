"""Anchor sets: export, import, and every way an import refuses (D41-D43).

The case this is built for is the consuming harness's phase 7: a fresh store for
new findings imports an established anchor set and places each newcomer against
the imported cuts. So the acceptance criterion -- an exported set imports into an
empty store and reports its bridging count -- is here, and so is what makes that
safe to rely on: the imported scale is the exported one bit for bit, every
refusal leaves the store byte-identical, an interrupted import is finished by
running it again, and nothing is banded without a rater's assignment.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from comparative_judgment.core import anchors, severity
from comparative_judgment.core.errors import (
    AnchorSetError,
    BandsNotAssignedError,
    CutError,
    DisconnectedComparisonsError,
    ImportConflictError,
    IncompleteImportError,
    OutputWriteError,
    StoreSchemaError,
)
from comparative_judgment.core.findings import parse_findings
from comparative_judgment.core.models import (
    AnchorComparison,
    Comparison,
    Cut,
    CutName,
    ImportRecorded,
    Outcome,
)
from comparative_judgment.core.session import Session
from comparative_judgment.core.store import LOG_FILE, Store

NOTE = "Critical means the caller acts on a false statement about their booking."
IDS = [f"F-{i:02d}" for i in range(8)]


def _fixed_clock() -> str:
    return "2026-09-18T12:00:00Z"


def _entry(finding_id: str, observation: str = "") -> str:
    text = observation or f"Observation for {finding_id}."
    return (
        f"  - id: {finding_id}\n"
        f"    observation: {json.dumps(text)}\n"
        f"    evidence: ['line 1: fragment for {finding_id}']\n"
        f"    consequence: Consequence for {finding_id}.\n"
        "    detectable_by: judge\n"
        "    tier: defect\n"
    )


def _document(ids: list[str], texts: dict[str, str] | None = None) -> str:
    texts = texts or {}
    return "findings:\n" + "".join(_entry(i, texts.get(i, "")) for i in ids)


def _store(path: Path, ids: list[str] | None = None, texts: dict[str, str] | None = None) -> Store:
    store = Store.create(path, clock=_fixed_clock)
    if ids:
        store.put_findings(parse_findings(_document(ids, texts)).admitted)
    return store


def _session(store: Store, rater: str = "rater-1") -> Session:
    return Session(store, rater_id=rater, appearance_target=4)


def _source(tmp_path: Path, texts: dict[str, str] | None = None) -> tuple[Session, list[str]]:
    """Eight findings ranked by a full bootstrap, with cuts leaving two non-anchors."""
    session = _session(_store(tmp_path / "source", IDS, texts))
    while session.next_pair() is not None:
        session.record(Outcome.LEFT)
    ranked = [e.finding_id for e in session.fit().ranked()]
    session.set_cuts(
        [
            Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1], calibration_note=NOTE),
            Cut(CutName.HIGH_MEDIUM, ranked[3], ranked[4]),
            Cut(CutName.MEDIUM_LOW, ranked[6], ranked[7]),
        ]
    )
    return session, ranked


def _exported(tmp_path: Path, name: str = "anchors.json") -> tuple[Session, list[str], Path]:
    source, ranked = _source(tmp_path)
    path = tmp_path / name
    source.export_anchor_set(path)
    return source, ranked, path


def _snapshot(store_path: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(store_path.iterdir())}


def _thetas(session: Session) -> dict[str, str]:
    return {e.finding_id: e.theta.hex() for e in session.fit().estimates}


def _rewrite(path: Path, edit: dict[str, object] | None = None, *, reversion: bool = True) -> None:
    """Edit an anchor-set file's content and, by default, restamp its version to match.

    Restamping is what makes the content checks reachable: without it the version
    check refuses first, and a test of the hash or tier check would pass for the
    wrong reason.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if edit:
        payload.update(edit)
    if reversion:
        content = {key: payload[key] for key in ("findings", "comparisons", "cuts")}
        payload["anchor_set_version"] = anchors._version_of(content)
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")


def _place_newcomers(session: Session) -> int:
    """Place every unplaced finding, returning how many comparisons that took."""
    spent = 0
    while session.next_pair() is not None:
        session.record(Outcome.RIGHT)
        spent += 1
    return spent


class TestExport:
    def test_two_exports_of_an_unchanged_store_are_byte_identical(self, tmp_path: Path) -> None:
        source, _ = _source(tmp_path)
        first, second = tmp_path / "a.json", tmp_path / "b.json"
        assert source.export_anchor_set(first) == source.export_anchor_set(second)
        assert first.read_bytes() == second.read_bytes()
        assert b"\r\n" not in first.read_bytes()

    def test_the_version_is_the_hash_of_what_the_file_carries(self, tmp_path: Path) -> None:
        source, _ = _source(tmp_path)
        path = tmp_path / "a.json"
        exported = source.export_anchor_set(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["anchor_set_version"] == exported.version
        assert payload["source_comparison_log_hash"] == source._store.log_hash()
        assert len(payload["findings"]) == exported.findings == len(IDS)
        assert len(payload["comparisons"]) == exported.comparisons

    def test_only_judged_findings_and_live_comparisons_travel(self, tmp_path: Path) -> None:
        source, _ = _source(tmp_path)
        store = source._store
        store.put_findings([*store.findings(), *parse_findings(_document(["F-UNJUDGED"])).admitted])
        source.undo()
        source._invalidate()
        payload_path = tmp_path / "a.json"
        exported = source.export_anchor_set(payload_path)
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        assert "F-UNJUDGED" not in {f["id"] for f in payload["findings"]}
        assert exported.comparisons == len(store.active_comparisons())
        assert exported.comparisons < len([e for e in store.log() if isinstance(e, Comparison)])

    def test_it_refuses_where_band_placement_refuses(self, tmp_path: Path) -> None:
        store = _store(tmp_path / "s", IDS)
        session = _session(store)
        session.record(Outcome.LEFT)
        with pytest.raises(CutError):
            session.export_anchor_set(tmp_path / "a.json")
        assert not (tmp_path / "a.json").exists()

    def test_it_refuses_a_disconnected_graph(self, tmp_path: Path) -> None:
        source, _ = _source(tmp_path)
        store = source._store
        store.put_findings([*store.findings(), *parse_findings(_document(["X-1", "X-2"])).admitted])
        store.append_comparison(
            left_id="X-1", right_id="X-2", outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
        source._invalidate()
        with pytest.raises(DisconnectedComparisonsError):
            source.export_anchor_set(tmp_path / "a.json")


class TestImportIntoAnEmptyStore:
    """The acceptance criterion, and what makes the imported scale the exported one."""

    def test_it_imports_and_reports_its_bridging_count(self, tmp_path: Path) -> None:
        source, _, path = _exported(tmp_path)
        target = _session(_store(tmp_path / "target"), rater="importer")
        outcome = target.import_anchor_set(path)
        assert outcome.applied and not outcome.resumed
        assert outcome.bridge is not None
        assert outcome.bridge.bridging == 0, "an empty store holds nothing to bridge to"
        assert outcome.bridge.new == len(IDS) and outcome.bridge.shared == 0
        assert outcome.appended == len(source._store.active_comparisons())
        assert outcome.cuts_adopted
        assert len(outcome.components) == 1

    def test_the_imported_scale_is_the_exported_one_bit_for_bit(self, tmp_path: Path) -> None:
        source, _, path = _exported(tmp_path)
        target = _session(_store(tmp_path / "target"))
        target.import_anchor_set(path)
        assert _thetas(target) == _thetas(source)
        assert target.cuts() == source.cuts()
        assert target.place().assignments == source.place().assignments

    def test_the_anchor_set_version_is_the_imported_one_and_the_run_id_moves(
        self, tmp_path: Path
    ) -> None:
        _, _, path = _exported(tmp_path)
        target_store = _store(tmp_path / "target")
        assert target_store.anchor_set_version == "1"
        before = severity.run_id(
            log_hash=target_store.log_hash(),
            anchor_set_version=target_store.anchor_set_version,
            cuts=(),
        )
        outcome = _session(target_store).import_anchor_set(path)
        assert target_store.anchor_set_version == outcome.anchor_set_version
        after = severity.run_id(
            log_hash=target_store.log_hash(),
            anchor_set_version=target_store.anchor_set_version,
            cuts=(),
        )
        assert after != before

    def test_the_import_is_recorded_under_the_importer_and_originals_keep_their_rater(
        self, tmp_path: Path
    ) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "target")
        _session(store, rater="importer").import_anchor_set(path)
        records = store.imports()
        assert len(records) == 1 and isinstance(records[0], ImportRecorded)
        assert records[0].rater_id == "importer"
        imported = [e for e in store.log() if isinstance(e, Comparison)]
        assert {c.rater_id for c in imported} == {"rater-1"}
        assert {c.origin for c in imported} == {records[0].anchor_set_version}

    def test_a_store_that_never_imports_keeps_its_anchor_set_version(self, tmp_path: Path) -> None:
        source, _ = _source(tmp_path)
        assert source._store.anchor_set_version == "1"


class TestPlacingAgainstImportedAnchors:
    def test_newcomers_placed_against_imported_anchors_raise_the_bridging_count(
        self, tmp_path: Path
    ) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "target", ["N-1", "N-2"])
        target = _session(store)
        outcome = target.import_anchor_set(path)
        assert outcome.bridge is not None and outcome.bridge.bridging == 0
        progress = target.progress()
        assert progress.placing and progress.comparisons_remaining == 6
        assert not progress.remaining_is_lower_bound

        spent = _place_newcomers(target)
        assert spent == 6
        bridge = target.diagnostics().anchor_sets[0]
        assert bridge.bridging == spent
        assert len(target.components()) == 1
        assert target.progress().comparisons_remaining == 0

    def test_a_load_after_an_import_reports_no_removal(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "target", ["N-1"])
        target = _session(store)
        target.import_anchor_set(path)
        _place_newcomers(target)
        document = tmp_path / "doc.yaml"
        document.write_text(_document(["N-1"]), encoding="utf-8")
        outcome = target.load(document)
        assert outcome.applied and outcome.pending_removals == ()
        assert {f.id for f in store.findings()} >= set(IDS)

    def test_a_load_refuses_an_identifier_an_import_holds_with_other_text(
        self, tmp_path: Path
    ) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "target", ["N-1"])
        target = _session(store)
        target.import_anchor_set(path)
        document = tmp_path / "doc.yaml"
        document.write_text(_document(["N-1", "F-00"], {"F-00": "Other text."}), encoding="utf-8")
        before = _snapshot(store.path)
        with pytest.raises(ImportConflictError, match="F-00"):
            target.load(document)
        assert _snapshot(store.path) == before

    def test_a_load_may_carry_an_imported_finding_with_identical_text(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "target", ["N-1"])
        target = _session(store)
        target.import_anchor_set(path)
        document = tmp_path / "doc.yaml"
        document.write_text(_document(["N-1", "F-00"]), encoding="utf-8")
        assert target.load(document).applied
        assert [f.id for f in store.findings()].count("F-00") == 1

    def test_undo_withdraws_only_a_judgment_made_in_this_store(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "target", ["N-1"])
        target = _session(store)
        target.import_anchor_set(path)
        before = _snapshot(store.path)
        assert target.undo() is None, "the only live comparisons were imported"
        assert _snapshot(store.path) == before

        target.record(Outcome.RIGHT)
        own = [c for c in store.active_comparisons() if not c.origin]
        retraction = target.undo()
        assert retraction is not None and retraction.retracts_seq == own[-1].seq


class TestFrozenBandsAcrossAnImport:
    """D42: an import assigns nothing; its bands and any it moves are proposals."""

    def test_imported_findings_arrive_as_first_time_proposals(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "target")
        target = _session(store)
        target.import_anchor_set(path)
        assert not any(e for e in store.log() if type(e).__name__ == "BandsAssigned")
        proposals = target.progress().proposals
        assert proposals and {p.assigned for p in proposals} == {None}
        with pytest.raises(BandsNotAssignedError):
            target.export(tmp_path / "severity.json")
        assert target.assign().applied
        target.export(tmp_path / "severity.json")

    def test_a_later_import_that_moves_an_assigned_band_proposes_the_change(
        self, tmp_path: Path
    ) -> None:
        source, ranked, first = _exported(tmp_path)
        store = _store(tmp_path / "target")
        target = _session(store)
        target.import_anchor_set(first)
        assert target.assign().applied

        # One judgment in the source lifts rank 5 over rank 2, swapping their bands.
        source._store.append_comparison(
            left_id=ranked[5],
            right_id=ranked[2],
            outcome=Outcome.LEFT,
            rater_id="r",
            session_id="s",
        )
        source._invalidate()
        second = tmp_path / "second.json"
        source.export_anchor_set(second)

        outcome = target.import_anchor_set(second)
        assert outcome.appended == 1 and outcome.shared_findings == tuple(sorted(IDS))
        moved = {p.finding_id for p in target.progress().proposals}
        assert moved == {ranked[2], ranked[5]}, "precondition: the comparison moved both"
        with pytest.raises(BandsNotAssignedError):
            target.export(tmp_path / "severity.json")
        assert target.assign().refused
        assert target.assign(accept_rebanding=True).applied


class TestSharedFindingsAndRoundTrips:
    def test_a_finding_the_store_holds_with_identical_text_is_shared(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "target", ["F-00", "N-1"])
        outcome = _session(store).import_anchor_set(path)
        assert outcome.shared_findings == ("F-00",)
        assert "F-00" not in outcome.new_findings
        assert [f.id for f in store.findings()].count("F-00") == 1

    def test_a_round_trip_skips_the_judgments_already_held(self, tmp_path: Path) -> None:
        source, _, first = _exported(tmp_path)
        middle = _session(_store(tmp_path / "middle", ["N-1"]), rater="rater-2")
        middle.import_anchor_set(first)
        placed = _place_newcomers(middle)
        back = tmp_path / "back.json"
        middle.export_anchor_set(back)

        held = len(source._store.active_comparisons())
        outcome = source.import_anchor_set(back)
        assert outcome.skipped == held
        assert outcome.appended == placed
        assert outcome.new_findings == ("N-1",)
        assert outcome.shared_findings == tuple(sorted(IDS))

    def test_the_same_judgment_twice_in_one_second_travels_twice(self, tmp_path: Path) -> None:
        """Matched by count, not membership: a store holding one copy still receives the second.

        The distinguishing case needs the importer to hold one copy already. Into an
        empty store, counting and membership append the same things, and a test of
        that case would pass whichever the code did.
        """
        source, ranked = _source(tmp_path)

        def judge_again() -> None:
            source._store.append_comparison(
                left_id=ranked[5],
                right_id=ranked[4],
                outcome=Outcome.LEFT,
                rater_id="rater-1",
                session_id="same",
            )
            source._invalidate()

        judge_again()
        first = tmp_path / "first.json"
        source.export_anchor_set(first)
        target = _session(_store(tmp_path / "target"))
        target.import_anchor_set(first)

        judge_again()  # the identical judgment again, in the same (fixed-clock) second
        second = tmp_path / "second.json"
        source.export_anchor_set(second)
        outcome = target.import_anchor_set(second)
        assert outcome.appended == 1, "the second copy is a second judgment"
        assert _thetas(target) == _thetas(source)

    def test_text_holding_a_line_separator_survives_the_store_and_an_import(
        self, tmp_path: Path
    ) -> None:
        """`str.splitlines()` would cut the record at U+2028; the log now splits on LF alone."""
        text = "First half" + chr(0x2028) + "second half."
        source, _ = _source(tmp_path, texts={"F-03": text})
        assert next(f for f in source._store.findings() if f.id == "F-03").observation == text
        path = tmp_path / "a.json"
        source.export_anchor_set(path)
        store = _store(tmp_path / "target")
        _session(store).import_anchor_set(path)
        reopened = Store.open(store.path)
        assert next(f for f in reopened.findings() if f.id == "F-03").observation == text


class TestImportRefusals:
    """Every refusal is named, and leaves the store byte-identical (D26)."""

    def _refused(
        self, store: Store, path: Path, error: type[Exception], match: str | None = None
    ) -> pytest.ExceptionInfo[Exception]:
        before = _snapshot(store.path)
        with pytest.raises(error, match=match) as caught:
            _session(store).import_anchor_set(path)
        assert _snapshot(store.path) == before
        return caught

    def test_a_file_that_is_not_json(self, tmp_path: Path) -> None:
        path = tmp_path / "a.json"
        path.write_text("{ not json", encoding="utf-8")
        self._refused(_store(tmp_path / "t"), path, AnchorSetError, "not valid JSON")

    def test_a_missing_file(self, tmp_path: Path) -> None:
        self._refused(_store(tmp_path / "t"), tmp_path / "absent.json", AnchorSetError)

    def test_an_unknown_schema(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        _rewrite(path, {"anchor_set_schema_version": "9"}, reversion=False)
        self._refused(_store(tmp_path / "t"), path, AnchorSetError, "schema")

    def test_a_file_edited_after_export(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["comparisons"] = payload["comparisons"][:-1]
        path.write_text(json.dumps(payload), encoding="utf-8")
        self._refused(_store(tmp_path / "t"), path, AnchorSetError, "edited after it was exported")

    def test_text_that_does_not_hash_to_its_stated_hash(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["findings"][0]["observation"] = "Quietly reworded."
        _rewrite(path, {"findings": payload["findings"]})
        self._refused(_store(tmp_path / "t"), path, AnchorSetError, "hashes to")

    def test_a_question_tier_finding(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["findings"][0]["tier"] = "question"
        _rewrite(path, {"findings": payload["findings"]})
        self._refused(_store(tmp_path / "t"), path, AnchorSetError, "question")

    def test_an_unknown_enum_is_a_named_refusal(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["comparisons"][0]["outcome"] = "sideways"
        _rewrite(path, {"comparisons": payload["comparisons"]})
        self._refused(_store(tmp_path / "t"), path, AnchorSetError, "sideways")

    def test_a_cut_missing(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        _rewrite(path, {"cuts": payload["cuts"][:2]})
        self._refused(_store(tmp_path / "t"), path, AnchorSetError, "three cuts")

    def test_the_same_set_imported_twice(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "t")
        _session(store).import_anchor_set(path)
        self._refused(store, path, ImportConflictError, "already imported")

    def test_an_identifier_this_store_holds_with_other_text(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "t", ["F-00"], {"F-00": "Different wording."})
        caught = self._refused(store, path, ImportConflictError, "F-00")
        assert "this store" in str(caught.value) and "file" in str(caught.value)

    def test_an_identifier_whose_removal_was_accepted_here(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "t", ["F-00", "Z-1"])
        session = _session(store)
        session.record(Outcome.LEFT)
        document = tmp_path / "doc.yaml"
        document.write_text(_document(["Z-1"]), encoding="utf-8")
        assert session.load(document, accept_removals=True).applied
        self._refused(store, path, ImportConflictError, "removal was accepted")

    def test_an_identifier_comparisons_still_name(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "t", ["F-00", "Z-1"])
        _session(store).record(Outcome.LEFT)
        store.put_findings(parse_findings(_document(["Z-1"])).admitted)
        self._refused(store, path, ImportConflictError, "still name it")

    def test_an_identifier_excluded_here_as_a_question(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "t")
        store.put_load_summary(["F-00"])
        self._refused(store, path, ImportConflictError, "question")

    def test_cuts_that_differ_from_the_stores(self, tmp_path: Path) -> None:
        _, ranked, path = _exported(tmp_path)
        other, _ = _source(tmp_path / "other")
        store = other._store
        store.put_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[2], calibration_note=NOTE),
                Cut(CutName.HIGH_MEDIUM, ranked[3], ranked[4]),
                Cut(CutName.MEDIUM_LOW, ranked[6], ranked[7]),
            ]
        )
        self._refused(store, path, ImportConflictError, "cuts differ")

    def test_a_merge_whose_cuts_would_not_hold(self, tmp_path: Path) -> None:
        """The check runs over whatever cuts the store will have, adopted or already there."""
        source, ranked, first = _exported(tmp_path)
        store = _store(tmp_path / "t")
        target = _session(store)
        target.import_anchor_set(first)
        for _ in range(6):
            store.append_comparison(
                left_id=ranked[1], right_id=ranked[0], outcome=Outcome.LEFT, rater_id="r",
                session_id="s",
            )  # fmt: skip
        source._store.append_comparison(
            left_id=ranked[6],
            right_id=ranked[5],
            outcome=Outcome.LEFT,
            rater_id="r",
            session_id="s",
        )
        source._invalidate()
        second = tmp_path / "second.json"
        source.export_anchor_set(second)
        self._refused(store, second, ImportConflictError, "break a cut")

    def test_judged_findings_the_placement_loop_could_not_bridge(self, tmp_path: Path) -> None:
        """D41(f): a store judged on its own, with no cuts, would be stranded."""
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "t", ["L-1", "L-2", "L-3"])
        local = _session(store)
        while local.next_pair() is not None:
            local.record(Outcome.LEFT)
        self._refused(store, path, ImportConflictError, "no\\s+comparison path")


class TestAnInterruptedImport:
    """The record goes first: an interruption is exact, blocks writes, and a re-run ends it."""

    def _interrupt_after(self, monkeypatch: pytest.MonkeyPatch, store: Store, kept: int) -> None:
        original = store.append_imported_comparisons

        def partial(
            comparisons: Sequence[AnchorComparison], *, origin: str
        ) -> tuple[Comparison, ...]:
            original(comparisons[:kept], origin=origin)
            raise RuntimeError("simulated crash")

        monkeypatch.setattr(store, "append_imported_comparisons", partial)

    def test_it_blocks_every_write_and_a_re_run_finishes_it_byte_for_byte(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, path = _exported(tmp_path)
        whole = _store(tmp_path / "whole", ["N-1"])
        _session(whole).import_anchor_set(path)

        store = _store(tmp_path / "broken", ["N-1"])
        target = _session(store)
        with monkeypatch.context() as patch:
            self._interrupt_after(patch, store, kept=3)
            with pytest.raises(RuntimeError):
                target.import_anchor_set(path)
        target._invalidate()

        assert [r.anchor_set_version for r in store.incomplete_imports()]
        assert "interrupted" in target.progress().blocked_reason
        assert target.progress().comparisons_remaining is None
        assert target.next_pair() is None
        for attempt in (
            lambda: target.record(Outcome.LEFT),
            target.undo,
            target.place,
            target.assign,
            lambda: target.export(tmp_path / "severity.json"),
            lambda: target.export_anchor_set(tmp_path / "again.json"),
            lambda: target.set_cuts(target.cuts()),
            lambda: target.load(tmp_path / "doc.yaml"),
        ):
            with pytest.raises(IncompleteImportError):
                attempt()

        outcome = target.import_anchor_set(path)
        assert outcome.resumed and outcome.cuts_adopted
        assert store.incomplete_imports() == ()
        assert (store.path / LOG_FILE).read_bytes() == (whole.path / LOG_FILE).read_bytes()
        assert store.cuts() == whole.cuts()

    def test_an_interruption_before_the_cuts_is_finished_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "t")
        target = _session(store)

        def refuse(cuts: object) -> None:
            raise RuntimeError("simulated crash")

        with monkeypatch.context() as patch:
            patch.setattr(store, "put_cuts", refuse)
            with pytest.raises(RuntimeError):
                target.import_anchor_set(path)
        target._invalidate()
        assert store.cuts() == () and store.incomplete_imports()
        outcome = target.import_anchor_set(path)
        assert outcome.resumed and outcome.cuts_adopted and outcome.appended > 0
        assert store.incomplete_imports() == () and len(store.cuts()) == 3

    def test_a_different_import_waits_for_the_interrupted_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source, ranked, first = _exported(tmp_path)
        store = _store(tmp_path / "t")
        target = _session(store)
        with monkeypatch.context() as patch:
            self._interrupt_after(patch, store, kept=1)
            with pytest.raises(RuntimeError):
                target.import_anchor_set(first)
        source._store.append_comparison(
            left_id=ranked[2],
            right_id=ranked[3],
            outcome=Outcome.LEFT,
            rater_id="r",
            session_id="s",
        )
        source._invalidate()
        second = tmp_path / "second.json"
        source.export_anchor_set(second)
        with pytest.raises(IncompleteImportError):
            target.import_anchor_set(second)


class TestTheImportRecord:
    def test_an_import_record_with_an_unknown_tier_is_a_named_refusal(self, tmp_path: Path) -> None:
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "t")
        _session(store).import_anchor_set(path)
        log = store.path / LOG_FILE
        log.write_text(
            log.read_text(encoding="utf-8").replace('"tier": "defect"', '"tier": "neither"', 1),
            encoding="utf-8",
            newline="\n",
        )
        with pytest.raises(StoreSchemaError, match="neither"):
            Store.open(store.path).log()


class TestAnchorSetFileRefusals:
    """Each of `read_anchor_set`'s checks, reached with the version restamped to match."""

    def _refused_file(self, tmp_path: Path, edit: dict[str, object], match: str) -> None:
        _, _, path = _exported(tmp_path)
        _rewrite(path, edit)
        with pytest.raises(AnchorSetError, match=match):
            anchors.read_anchor_set(path)

    def _payload(self, tmp_path: Path) -> dict[str, object]:
        _, _, path = _exported(tmp_path / "p")
        loaded: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def test_a_finding_carried_twice(self, tmp_path: Path) -> None:
        findings = self._payload(tmp_path)["findings"]
        assert isinstance(findings, list)
        self._refused_file(tmp_path, {"findings": [*findings, findings[0]]}, "more than once")

    def test_a_comparison_naming_a_finding_the_file_does_not_carry(self, tmp_path: Path) -> None:
        comparisons = self._payload(tmp_path)["comparisons"]
        assert isinstance(comparisons, list)
        stray = {**comparisons[0], "left_id": "NOT-CARRIED"}
        self._refused_file(tmp_path, {"comparisons": [*comparisons, stray]}, "does not carry")

    def test_a_comparison_of_a_finding_with_itself(self, tmp_path: Path) -> None:
        comparisons = self._payload(tmp_path)["comparisons"]
        assert isinstance(comparisons, list)
        itself = {**comparisons[0], "right_id": comparisons[0]["left_id"]}
        self._refused_file(tmp_path, {"comparisons": [*comparisons, itself]}, "with itself")

    def test_a_cut_anchor_with_no_comparison(self, tmp_path: Path) -> None:
        payload = self._payload(tmp_path)
        findings, cuts = payload["findings"], payload["cuts"]
        assert isinstance(findings, list) and isinstance(cuts, list)
        lonely = {**findings[0], "id": "LONELY"}
        moved = [{**cuts[0], "above_id": "LONELY"}, *cuts[1:]]
        self._refused_file(tmp_path, {"findings": [*findings, lonely], "cuts": moved}, "prior")

    def test_a_top_cut_without_its_calibration_note(self, tmp_path: Path) -> None:
        cuts = self._payload(tmp_path)["cuts"]
        assert isinstance(cuts, list)
        unnoted = [{**c, "calibration_note": ""} for c in cuts]
        self._refused_file(tmp_path, {"cuts": unnoted}, "calibration note")

    def test_an_unwritable_path_is_a_named_refusal(self, tmp_path: Path) -> None:
        source, _ = _source(tmp_path)
        with pytest.raises(OutputWriteError):
            source.export_anchor_set(tmp_path / "missing" / "anchors.json")


class TestCompletingAnImportAgainstAChangedLog:
    def test_a_log_that_no_longer_matches_the_record_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Completion recomputes what the first run chose; a tampered log cannot match it."""
        _, _, path = _exported(tmp_path)
        store = _store(tmp_path / "t")
        target = _session(store)
        with monkeypatch.context() as patch:
            TestAnInterruptedImport()._interrupt_after(patch, store, kept=1)
            with pytest.raises(RuntimeError):
                target.import_anchor_set(path)
        imported = json.loads(path.read_text(encoding="utf-8"))["comparisons"][-1]
        # A line claiming to predate the import, identical to one it still owes.
        planted = {"kind": "comparison", "seq": 0, **imported}
        log = store.path / LOG_FILE
        log.write_text(
            json.dumps(planted, sort_keys=True) + "\n" + log.read_text(encoding="utf-8"),
            encoding="utf-8",
            newline="\n",
        )
        target._invalidate()
        with pytest.raises(ImportConflictError, match="changed under it"):
            target.import_anchor_set(path)
