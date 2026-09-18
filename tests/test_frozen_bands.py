"""Frozen bands: an assignment fixes a band, and a refit proposes rather than relabels (D36).

A band is derived from the fit, so any comparison, retraction or re-set cut can
move one -- and in the consuming harness, placing one finding re-banded three
others with nothing saying so. The consumer cannot be handed the old band beside
the new fit either: it refuses a row whose band disagrees with its `theta`. So the
freeze is a refusal. `assign` records every banded finding's band under a rater's
name, and `export` refuses while the fit places any banded finding other than as
assigned.

Each scenario states its own precondition before testing the rule. A re-banding
test whose comparison happened not to move anything would pass for the wrong
reason, which is this repository's most-repeated finding: a guard green and blind
at the same time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from comparative_judgment.cli import main
from comparative_judgment.core import bands, severity
from comparative_judgment.core.errors import (
    BandsNotAssignedError,
    CutError,
    DisconnectedComparisonsError,
    StoreSchemaError,
)
from comparative_judgment.core.findings import parse_findings
from comparative_judgment.core.models import (
    Band,
    BandAssignment,
    BandsAssigned,
    Cut,
    CutName,
    Outcome,
    ProposedBand,
)
from comparative_judgment.core.session import ASSIGN_SESSION_ID, Session
from comparative_judgment.core.store import LOG_FILE, Store

NOTE = "Critical means the caller acts on a false statement about their booking."


def _fixed_clock() -> str:
    return "2026-09-18T12:00:00Z"


def _entry(finding_id: str, text: str = "") -> str:
    return (
        f"  - id: {finding_id}\n"
        f"    observation: Observation for {finding_id}.{text}\n"
        f"    evidence: ['line 1: fragment for {finding_id}']\n"
        f"    consequence: Consequence for {finding_id}.\n"
        "    detectable_by: judge\n"
        "    tier: defect\n"
    )


def _document(ids: list[str], *, edited: str = "") -> str:
    return "findings:\n" + "".join(_entry(i, " Edited." if i == edited else "") for i in ids)


IDS = [f"F-{i:02d}" for i in range(8)]


def _bootstrapped(tmp_path: Path) -> tuple[Session, list[str]]:
    """Eight findings ranked by a full bootstrap, with cuts leaving two non-anchors.

    The cuts sit between ranks 0|1, 3|4 and 6|7, so ranks 2 and 5 are banded by a
    midpoint rather than by being an anchor, and one comparison can carry either
    across a boundary without inverting one.
    """
    store = Store.create(tmp_path / "s", clock=_fixed_clock)
    store.put_findings(parse_findings(_document(IDS)).admitted)
    session = Session(store, rater_id="rater-1", appearance_target=4)
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


def _assigned(tmp_path: Path) -> tuple[Session, list[str]]:
    session, ranked = _bootstrapped(tmp_path)
    outcome = session.assign()
    assert outcome.applied, "the first assignment wrote nothing, so every test built on it is void"
    return session, ranked


def _log_bytes(session: Session) -> bytes:
    return (session.store_path / LOG_FILE).read_bytes()


def _run_id(session: Session) -> str:
    store = session._store
    return severity.run_id(
        log_hash=store.log_hash(),
        anchor_set_version=store.anchor_set_version,
        cuts=store.cuts(),
    )


def _move_across_a_cut(session: Session, ranked: list[str]) -> None:
    """One comparison that lifts rank 5 over rank 2, swapping their bands."""
    session._store.append_comparison(
        left_id=ranked[5],
        right_id=ranked[2],
        outcome=Outcome.LEFT,
        rater_id="rater-1",
        session_id="test",
    )
    session._invalidate()


def _newcomer(session: Session, finding_id: str = "F-NEW") -> None:
    """Admit one unjudged finding and place it, as a second batch would."""
    session._store.put_findings(
        [*session._store.findings(), *parse_findings("findings:\n" + _entry(finding_id)).admitted]
    )
    session._invalidate()
    while session.next_pair() is not None:
        session.record(Outcome.RIGHT)


class TestTheFirstAssignment:
    def test_it_records_every_banded_finding_and_lets_export_through(self, tmp_path: Path) -> None:
        session, _ = _bootstrapped(tmp_path)
        placement = session.place()
        assert {p.assigned for p in placement.proposals} == {None}, (
            "before any assignment every banded finding should be proposed as a first one"
        )
        with pytest.raises(BandsNotAssignedError):
            session.export(tmp_path / "early.json")
        assert not (tmp_path / "early.json").exists()

        outcome = session.assign()
        assert outcome.applied and not outcome.rebanded
        record = outcome.record
        assert record is not None
        assert record.rater_id == "rater-1"
        assert record.session_id == ASSIGN_SESSION_ID
        hashes = {f.id: f.content_hash for f in session._store.findings()}
        assert [(b.finding_id, b.band, b.content_hash) for b in record.bands] == [
            (a.finding_id, a.band, hashes[a.finding_id])
            for a in sorted(placement.assignments, key=lambda a: a.finding_id)
        ]
        assert Store.open(session.store_path).log()[-1] == record, (
            "the record read back from disk is not the one written"
        )

        out = tmp_path / "severity.json"
        session.export(out)
        rows = json.loads(out.read_text(encoding="utf-8"))["severities"]
        assert {row["id"]: row["severity"] for row in rows} == {
            b.finding_id: b.band.value for b in record.bands
        }

    def test_an_assignment_with_nothing_new_or_changed_writes_nothing(self, tmp_path: Path) -> None:
        """An inert record would still move the log hash, and the run id with it."""
        session, _ = _assigned(tmp_path)
        before, run_before = _log_bytes(session), _run_id(session)

        again = session.assign()
        assert not again.applied and not again.refused
        assert again.first_time == () and again.rebanded == ()
        assert _log_bytes(session) == before
        assert _run_id(session) == run_before

    def test_assign_without_a_rater_is_refused_by_name(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        session, _ = _bootstrapped(tmp_path)
        before = _log_bytes(session)
        assert main(["assign", "--store", str(session.store_path)]) == 1
        assert "--rater is required" in capsys.readouterr().err
        assert _log_bytes(session) == before

        assert main(["assign", "--store", str(session.store_path), "--rater", "rater-2"]) == 0
        (record,) = [
            e for e in Store.open(session.store_path).log() if isinstance(e, BandsAssigned)
        ]
        assert record.rater_id == "rater-2"

    def test_assign_refuses_across_an_inverted_cut_and_writes_nothing(self, tmp_path: Path) -> None:
        session, ranked = _bootstrapped(tmp_path)
        session._store.put_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[1], ranked[0], calibration_note=NOTE),
                Cut(CutName.HIGH_MEDIUM, ranked[3], ranked[4]),
                Cut(CutName.MEDIUM_LOW, ranked[6], ranked[7]),
            ]
        )
        session._invalidate()
        before = _log_bytes(session)
        with pytest.raises(CutError):
            session.assign()
        assert _log_bytes(session) == before

    def test_assign_refuses_across_a_disconnected_graph_and_writes_nothing(
        self, tmp_path: Path
    ) -> None:
        store = Store.create(tmp_path / "s", clock=_fixed_clock)
        store.put_findings(parse_findings(_document(IDS[:4])).admitted)
        for left, right in (("F-00", "F-01"), ("F-02", "F-03")):
            store.append_comparison(
                left_id=left, right_id=right, outcome=Outcome.LEFT, rater_id="r", session_id="s"
            )
        store.put_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, "F-00", "F-01", calibration_note=NOTE),
                Cut(CutName.HIGH_MEDIUM, "F-01", "F-02"),
                Cut(CutName.MEDIUM_LOW, "F-02", "F-03"),
            ]
        )
        session = Session(store, rater_id="r")
        before = _log_bytes(session)
        with pytest.raises(DisconnectedComparisonsError):
            session.assign()
        assert _log_bytes(session) == before


class TestAChangeToAnAssignedBand:
    def test_a_comparison_that_moves_one_is_proposed_refused_and_accepted_only_by_the_flag(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        session, ranked = _assigned(tmp_path)
        exported = tmp_path / "before.json"
        session.export(exported)
        cited = json.loads(exported.read_text(encoding="utf-8"))

        _move_across_a_cut(session, ranked)
        placement = session.place()
        moved = {p.finding_id: (p.assigned, p.current) for p in placement.proposals}
        assert moved == {
            ranked[2]: (Band.HIGH, Band.MEDIUM),
            ranked[5]: (Band.MEDIUM, Band.HIGH),
        }, "precondition: the comparison was chosen to swap exactly these two across high_medium"

        # Reported, by the session and by both commands that show bands.
        assert session.progress().proposals == placement.proposals
        store = str(session.store_path)
        assert main(["status", "--store", store]) == 0
        assert main(["bands", "--store", store]) == 0
        out = capsys.readouterr().out
        assert out.count(f"{ranked[5]:<16} medium -> high") == 2
        assert out.count(f"{ranked[2]:<16} high -> medium") == 2

        # Refused by export, naming both, writing nothing.
        refused = tmp_path / "after.json"
        with pytest.raises(BandsNotAssignedError) as caught:
            session.export(refused)
        assert f"{ranked[5]}: medium -> high" in str(caught.value)
        assert f"{ranked[2]}: high -> medium" in str(caught.value)
        assert not refused.exists()

        # Refused by assign without the flag, naming both, writing nothing.
        before, run_before = _log_bytes(session), _run_id(session)
        outcome = session.assign()
        assert outcome.refused
        assert {p.finding_id for p in outcome.rebanded} == {ranked[2], ranked[5]}
        assert _log_bytes(session) == before
        assert main(["assign", "--store", store, "--rater", "rater-1"]) == 1
        assert f"{ranked[5]:<16} medium -> high" in capsys.readouterr().err
        assert _log_bytes(session) == before

        # Accepted with it: one record, and the hash and run id both move.
        accepted = session.assign(accept_rebanding=True)
        assert accepted.applied and accepted.record is not None
        assert accepted.record.seq == max(e.seq for e in session._store.log())
        assert _log_bytes(session) != before
        assert _run_id(session) != run_before
        session.export(refused)
        written = json.loads(refused.read_text(encoding="utf-8"))
        rows = {r["id"]: r["severity"] for r in written["severities"]}
        assert rows[ranked[5]] == "high" and rows[ranked[2]] == "medium"
        assert {r["id"]: r["severity"] for r in cited["severities"]}[ranked[5]] == "medium"

    def test_the_acceptance_changes_the_log_hash_by_itself(self, tmp_path: Path) -> None:
        """Measured across the acceptance alone, not across the comparison before it."""
        session, ranked = _assigned(tmp_path)
        _move_across_a_cut(session, ranked)
        hash_before, run_before = session._store.log_hash(), _run_id(session)
        assert session.assign(accept_rebanding=True).applied
        assert session._store.log_hash() != hash_before
        assert _run_id(session) != run_before

    def test_re_setting_a_cut_that_moves_one_is_refused_the_same_way(self, tmp_path: Path) -> None:
        session, ranked = _assigned(tmp_path)
        session.set_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1], calibration_note=NOTE),
                Cut(CutName.HIGH_MEDIUM, ranked[4], ranked[5]),
                Cut(CutName.MEDIUM_LOW, ranked[6], ranked[7]),
            ]
        )
        placement = session.place()
        assert [(p.finding_id, p.assigned, p.current) for p in placement.proposals] == [
            (ranked[4], Band.MEDIUM, Band.HIGH)
        ], "precondition: moving high_medium down one rank lifts exactly its new upper anchor"
        assert session.progress().proposals == placement.proposals

        before = _log_bytes(session)
        with pytest.raises(BandsNotAssignedError):
            session.export(tmp_path / "x.json")
        assert session.assign().refused
        assert _log_bytes(session) == before
        assert session.assign(accept_rebanding=True).applied

    def test_a_finding_whose_comparisons_are_all_retracted_is_proposed_to_no_band(
        self, tmp_path: Path
    ) -> None:
        session, _ = _assigned(tmp_path)
        _newcomer(session)
        assert session.assign().applied, "precondition: the newcomer's first band is assigned"
        assert "F-NEW" in session._store.assigned_bands()

        for comparison in session._store.active_comparisons():
            if "F-NEW" in (comparison.left_id, comparison.right_id):
                session._store.append_retraction(
                    retracts_seq=comparison.seq, rater_id="rater-1", session_id="test"
                )
        session._invalidate()

        placement = session.place()
        assert "F-NEW" in placement.unplaced
        (lost,) = [p for p in placement.proposals if p.finding_id == "F-NEW"]
        assert lost.assigned is not None and lost.current is None

        before = _log_bytes(session)
        outcome = session.assign()
        assert outcome.refused and "F-NEW" in {p.finding_id for p in outcome.rebanded}
        assert _log_bytes(session) == before
        with pytest.raises(BandsNotAssignedError):
            session.export(tmp_path / "x.json")


class TestFirstAssignmentsAndLapses:
    def test_a_newly_banded_finding_needs_no_flag_and_export_waits_for_it(
        self, tmp_path: Path
    ) -> None:
        session, _ = _assigned(tmp_path)
        _newcomer(session)
        placement = session.place()
        first = [p for p in placement.proposals if p.finding_id == "F-NEW"]
        assert len(first) == 1 and first[0].assigned is None and first[0].current is not None

        with pytest.raises(BandsNotAssignedError) as caught:
            session.export(tmp_path / "x.json")
        assert "F-NEW: unassigned ->" in str(caught.value)

        if any(p.assigned is not None for p in placement.proposals):
            pytest.fail(
                "precondition: placing the newcomer moved an assigned band, so this test "
                "would pass or fail on the flag rather than on the first assignment"
            )
        outcome = session.assign()
        assert outcome.applied and [p.finding_id for p in outcome.first_time] == ["F-NEW"]
        session.export(tmp_path / "x.json")

    def test_an_accepted_removal_lets_the_assignment_lapse(self, tmp_path: Path) -> None:
        """The removal is itself an audited acceptance, so it needs no second one."""
        session, _ = _assigned(tmp_path)
        _newcomer(session)
        assert session.assign().applied
        assert "F-NEW" in session._store.assigned_bands()

        document = tmp_path / "without-newcomer.yaml"
        document.write_text(_document(IDS), encoding="utf-8")
        refused = session.load(document)
        assert not refused.applied and refused.pending_removals, (
            "precondition: the newcomer is judged, so its removal needs accepting"
        )
        assert session.load(document, accept_removals=True).applied

        assert "F-NEW" not in session._store.assigned_bands()
        assert "F-NEW" not in {p.finding_id for p in session.place().proposals}

    def test_a_removal_nobody_accepted_keeps_the_assignment(self, tmp_path: Path) -> None:
        """Only an accepted removal lapses one; a band that vanished otherwise is proposed.

        With every comparison retracted the newcomer is unjudged, so the load
        drops it without asking. That route records no acceptance, so the band it
        was assigned is still frozen, and its loss is a proposal like any other.
        """
        session, _ = _assigned(tmp_path)
        _newcomer(session)
        assert session.assign().applied
        for comparison in session._store.active_comparisons():
            if "F-NEW" in (comparison.left_id, comparison.right_id):
                session._store.append_retraction(
                    retracts_seq=comparison.seq, rater_id="rater-1", session_id="test"
                )
        document = tmp_path / "without-newcomer.yaml"
        document.write_text(_document(IDS), encoding="utf-8")
        outcome = session.load(document)
        assert outcome.applied and not outcome.accepted_removals, (
            "precondition: an unjudged finding leaves the document without an acceptance"
        )

        assert "F-NEW" in session._store.assigned_bands()
        (lost,) = [p for p in session.place().proposals if p.finding_id == "F-NEW"]
        assert lost.current is None
        with pytest.raises(BandsNotAssignedError):
            session.export(tmp_path / "x.json")

    def test_an_accepted_revision_leaves_the_assignment_standing(self, tmp_path: Path) -> None:
        session, ranked = _assigned(tmp_path)
        frozen = session._store.assigned_bands()
        document = tmp_path / "edited.yaml"
        document.write_text(_document(IDS, edited=ranked[2]), encoding="utf-8")
        assert session.load(document, accept_revisions=True).accepted_revisions, (
            "precondition: the edit is to a judged finding, so it needs accepting"
        )

        assert session._store.assigned_bands() == frozen
        assert session.place().proposals == ()
        session.export(tmp_path / "x.json")


class TestTheProposalRule:
    def test_each_kind_of_disagreement_is_named_and_nothing_else(self) -> None:
        current = (
            BandAssignment("A", Band.HIGH, 1.0),
            BandAssignment("B", Band.MEDIUM, 0.0),
            BandAssignment("C", Band.LOW, -1.0),
        )
        assigned = {"A": Band.HIGH, "B": Band.HIGH, "D": Band.LOW}
        assert bands.proposals(current, assigned) == (
            ProposedBand("B", Band.HIGH, Band.MEDIUM),
            ProposedBand("C", None, Band.LOW),
            ProposedBand("D", Band.LOW, None),
        )

    def test_nothing_is_proposed_where_the_two_agree(self) -> None:
        current = (BandAssignment("A", Band.HIGH, 1.0),)
        assert bands.proposals(current, {"A": Band.HIGH}) == ()
        assert bands.proposals((), {}) == ()


class TestTheRecordOnDisk:
    def test_an_assignment_naming_something_that_is_not_a_band_is_a_named_refusal(
        self, tmp_path: Path
    ) -> None:
        session, _ = _assigned(tmp_path)
        path = session.store_path / LOG_FILE
        path.write_text(
            path.read_text(encoding="utf-8").replace('"band": "high"', '"band": "severe"', 1),
            encoding="utf-8",
            newline="\n",
        )
        with pytest.raises(StoreSchemaError, match="not a band"):
            Store.open(session.store_path).log()
