"""The refusals that stop data loss, and the error paths that reach a user.

Every case here was a real defect found by an independent audit of 0.5.0 — a
session that did not write the code, read the spec and the source, then ran the
tool against constructed inputs. Each one was reachable, silent, and invisible
from inside a green suite.

The pattern worth carrying forward is what they had in common. Three were
write-before-validate: the store was mutated and *then* the input was checked,
in a tool whose stated failure model is that an unrecoverable error writes
nothing. Three more were a guard whose coverage was narrower than the rule it
enforced — real, tested, and blind to the case that mattered. None of them was a
mistake in the mathematics, which was correct throughout and is the part that had
been checked hardest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from comparative_judgment.cli import main
from comparative_judgment.core.errors import (
    FindingSchemaError,
    RetractionError,
    StoreExistsError,
    StoreSchemaError,
    UnknownItemError,
)
from comparative_judgment.core.findings import parse_findings
from comparative_judgment.core.models import Band, BandAssignment, Cut, CutName, Outcome
from comparative_judgment.core.session import Session
from comparative_judgment.core.severity import build_payload
from comparative_judgment.core.store import Store

NOTE = "Critical means the caller acts on a false statement about their booking."


def _doc(ids: tuple[str, ...]) -> str:
    return "findings:\n" + "".join(
        f"  - id: {i}\n"
        f"    observation: observation for {i}\n"
        f"    evidence: ['line 1: fragment for {i}']\n"
        f"    consequence: consequence for {i}\n"
        f"    detectable_by: assert\n"
        f"    tier: defect\n"
        for i in ids
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "findings.yaml").write_text(
        _doc(("F-01", "F-02", "F-03", "F-04")), encoding="utf-8"
    )
    return tmp_path


def _store(workspace: Path) -> str:
    return str(workspace / ".cj-store")


def _loaded(workspace: Path) -> str:
    store = _store(workspace)
    main(["init", "--store", store])
    main(["load", "--store", store, "--findings", str(workspace / "findings.yaml")])
    return store


def _judged(workspace: Path, *, target: int = 3) -> tuple[str, list[str]]:
    store = _loaded(workspace)
    session = Session.open(Path(store), rater_id="r", appearance_target=target)
    while session.next_pair() is not None:
        session.record(Outcome.LEFT)
    return store, [e.finding_id for e in session.fit().ranked()]


class TestInitDoesNotDestroyAStore:
    """`init` rewrites cuts.json empty, under a name that reads as safe."""

    def test_init_over_an_existing_store_is_refused(self, workspace: Path) -> None:
        store = _loaded(workspace)
        assert main(["init", "--store", store]) == 1

    def test_the_cuts_survive_a_refused_re_init(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The three cuts are the only absolute judgments the tool ever asks for."""
        store, ranked = _judged(workspace)
        session = Session.open(Path(store), rater_id="r", appearance_target=3)
        session.set_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1], calibration_note=NOTE),
                Cut(CutName.HIGH_MEDIUM, ranked[1], ranked[2]),
                Cut(CutName.MEDIUM_LOW, ranked[2], ranked[3]),
            ]
        )
        before = (Path(store) / "cuts.json").read_bytes()

        assert main(["init", "--store", store]) == 1
        assert "StoreExistsError" in capsys.readouterr().err
        assert (Path(store) / "cuts.json").read_bytes() == before
        assert len(Session.open(Path(store), rater_id="r").cuts()) == 3

    def test_the_load_summary_survives_too(self, workspace: Path) -> None:
        """The excluded-question count is what D10 exists to keep visible."""
        (workspace / "findings.yaml").write_text(
            _doc(("F-01", "F-02")) + "  - id: Q-01\n    observation: is this intended?\n"
            "    evidence: ['line 3: ambiguous']\n    consequence: unclear\n"
            "    detectable_by: human\n    tier: question\n",
            encoding="utf-8",
        )
        store = _loaded(workspace)
        assert main(["init", "--store", store]) == 1
        session = Session.open(Path(store), rater_id="r")
        assert session.progress().excluded_questions == 1

    def test_force_re_initializes_an_unjudged_store(self, workspace: Path) -> None:
        store = _loaded(workspace)
        assert main(["init", "--store", store, "--force"]) == 0

    def test_force_still_refuses_once_a_judgment_exists(self, workspace: Path) -> None:
        """`--force` is for a false start, not for discarding recorded work."""
        store, _ = _judged(workspace)
        with pytest.raises(StoreExistsError, match="recorded judgments"):
            Store.create(Path(store), force=True)


class TestCutsValidateBeforeWriting:
    def test_an_unknown_anchor_leaves_cuts_json_untouched(self, workspace: Path) -> None:
        """The failure model says an unrecoverable error writes nothing."""
        store, ranked = _judged(workspace)
        before = (Path(store) / "cuts.json").read_bytes()
        code = main(
            [
                "cuts",
                "--store",
                store,
                "--target",
                "3",
                "--critical-high",
                f"{ranked[0]}:NOPE",
                "--high-medium",
                f"{ranked[1]}:{ranked[2]}",
                "--medium-low",
                f"{ranked[2]}:{ranked[3]}",
                "--critical-high-note",
                NOTE,
            ]
        )
        assert code == 1
        assert (Path(store) / "cuts.json").read_bytes() == before

    def test_a_missing_calibration_note_on_the_top_cut_is_refused(self, workspace: Path) -> None:
        """An internally perfect ordering can still sit a whole band too high."""
        store, ranked = _judged(workspace)
        code = main(
            [
                "cuts",
                "--store",
                store,
                "--target",
                "3",
                "--critical-high",
                f"{ranked[0]}:{ranked[1]}",
                "--high-medium",
                f"{ranked[1]}:{ranked[2]}",
                "--medium-low",
                f"{ranked[2]}:{ranked[3]}",
            ]
        )
        assert code == 1
        assert not Session.open(Path(store), rater_id="r").cuts()

    def test_the_calibration_note_reaches_the_severity_file(self, workspace: Path) -> None:
        store, ranked = _judged(workspace)
        session = Session.open(Path(store), rater_id="r", appearance_target=3)
        session.set_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1], calibration_note=NOTE),
                Cut(CutName.HIGH_MEDIUM, ranked[1], ranked[2]),
                Cut(CutName.MEDIUM_LOW, ranked[2], ranked[3]),
            ]
        )
        session.assign()
        out = workspace / "severity.json"
        session.export(out)
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["calibration"]["critical_high"] == NOTE

    def test_an_anchor_with_no_comparisons_is_refused(self, workspace: Path) -> None:
        """Its position is the prior's; a boundary there reports the prior."""
        from comparative_judgment.core.errors import UnknownItemError

        store = _loaded(workspace)
        session = Session.open(Path(store), rater_id="r", appearance_target=3)
        session.record(Outcome.LEFT)
        judged = [e.finding_id for e in session.estimates() if e.appearances > 0]
        untouched = next(e.finding_id for e in session.estimates() if e.appearances == 0)
        with pytest.raises(UnknownItemError, match="no comparisons"):
            session.set_cuts(
                [
                    Cut(CutName.CRITICAL_HIGH, judged[0], judged[1], calibration_note=NOTE),
                    Cut(CutName.HIGH_MEDIUM, judged[1], untouched),
                    Cut(CutName.MEDIUM_LOW, untouched, judged[0]),
                ]
            )


class TestCutsAreThreeAndNamed:
    def test_the_wrong_number_of_cuts_is_refused(self, workspace: Path) -> None:
        from comparative_judgment.core.errors import CutError

        store, ranked = _judged(workspace)
        session = Session.open(Path(store), rater_id="r", appearance_target=3)
        with pytest.raises(CutError, match="got 2"):
            session.set_cuts(
                [
                    Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1], calibration_note=NOTE),
                    Cut(CutName.HIGH_MEDIUM, ranked[1], ranked[2]),
                ]
            )

    def test_a_missing_top_cut_is_refused_by_name(self, workspace: Path) -> None:
        from comparative_judgment.core.errors import CutError

        store, ranked = _judged(workspace)
        session = Session.open(Path(store), rater_id="r", appearance_target=3)
        with pytest.raises(CutError, match="critical_high"):
            session.set_cuts(
                [
                    Cut(CutName.HIGH_MEDIUM, ranked[0], ranked[1], calibration_note=NOTE),
                    Cut(CutName.HIGH_MEDIUM, ranked[1], ranked[2]),
                    Cut(CutName.MEDIUM_LOW, ranked[2], ranked[3]),
                ]
            )


class TestUnplacedItemsAreReported:
    def test_bands_names_them_rather_than_omitting_them(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An id absent with no explanation reads as an oversight."""
        (workspace / "findings.yaml").write_text(
            _doc(("F-01", "F-02", "F-03", "F-04", "F-05")), encoding="utf-8"
        )
        store = _loaded(workspace)
        session = Session.open(Path(store), rater_id="r", appearance_target=3)
        # Judge four of the five, leaving one untouched at the origin.
        for left, right in (
            ("F-01", "F-02"),
            ("F-02", "F-03"),
            ("F-03", "F-04"),
            ("F-01", "F-03"),
            ("F-02", "F-04"),
        ):
            session._store.append_comparison(
                left_id=left, right_id=right, outcome=Outcome.LEFT, rater_id="r", session_id="s"
            )
        session._invalidate()
        session.set_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, "F-01", "F-02", calibration_note=NOTE),
                Cut(CutName.HIGH_MEDIUM, "F-02", "F-03"),
                Cut(CutName.MEDIUM_LOW, "F-03", "F-04"),
            ]
        )
        assert main(["bands", "--store", store, "--target", "3"]) == 0
        out = capsys.readouterr().out
        assert "unplaced (1): F-05" in out
        assert "would report the prior" in out


class TestSequenceNumbersAreUnique:
    def test_two_handles_on_one_store_do_not_collide(self, tmp_path: Path) -> None:
        """A cached counter desynchronizes the moment a second handle appends.

        Reachable without concurrency being anyone's plan: a `cj compare` left
        open in one terminal while `cj load --accept-revisions` runs in another.
        """
        first = Store.create(tmp_path / "s")
        first.put_findings(parse_findings(_doc(("P", "Q"))).admitted)
        first.put_load_summary(())
        second = Store.open(tmp_path / "s")

        first.append_comparison(
            left_id="P", right_id="Q", outcome=Outcome.LEFT, rater_id="r", session_id="a"
        )
        second.append_comparison(
            left_id="P", right_id="Q", outcome=Outcome.RIGHT, rater_id="r", session_id="b"
        )
        first.append_comparison(
            left_id="P", right_id="Q", outcome=Outcome.LEFT, rater_id="r", session_id="a"
        )

        seqs = [entry.seq for entry in Store.open(tmp_path / "s").log()]
        assert seqs == [1, 2, 3]
        assert len(set(seqs)) == len(seqs)

    def test_retracting_one_leaves_the_others_active(self, tmp_path: Path) -> None:
        """The harm a duplicate seq caused: one retraction withdrew two records."""
        first = Store.create(tmp_path / "s")
        first.put_findings(parse_findings(_doc(("P", "Q"))).admitted)
        first.put_load_summary(())
        second = Store.open(tmp_path / "s")
        first.append_comparison(
            left_id="P", right_id="Q", outcome=Outcome.LEFT, rater_id="r", session_id="a"
        )
        second.append_comparison(
            left_id="P", right_id="Q", outcome=Outcome.RIGHT, rater_id="r", session_id="b"
        )
        second.append_retraction(retracts_seq=1, rater_id="r", session_id="b")
        active = Store.open(tmp_path / "s").active_comparisons()
        assert [c.seq for c in active] == [2]

    def test_a_record_longer_than_the_tail_window_still_resolves(self, tmp_path: Path) -> None:
        """The window grows rather than assuming one record fits inside it."""
        from comparative_judgment.core.store import _TAIL_WINDOW

        store = Store.create(tmp_path / "s")
        store.put_findings(parse_findings(_doc(("P", "Q"))).admitted)
        store.put_load_summary(())
        store.append_comparison(
            left_id="P",
            right_id="Q",
            outcome=Outcome.LEFT,
            rater_id="r" * (_TAIL_WINDOW * 2),
            session_id="a",
        )
        store.append_comparison(
            left_id="P", right_id="Q", outcome=Outcome.RIGHT, rater_id="r", session_id="a"
        )
        assert [entry.seq for entry in Store.open(tmp_path / "s").log()] == [1, 2]


class TestARemovedFindingIsRefused:
    def test_removing_a_judged_finding_is_refused_by_name(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """D18's harm through the adjacent door: an edit elsewhere in the file."""
        store, _ = _judged(workspace)
        (workspace / "findings.yaml").write_text(_doc(("F-01", "F-02", "F-03")), encoding="utf-8")
        code = main(["load", "--store", store, "--findings", str(workspace / "findings.yaml")])
        assert code == 1
        err = capsys.readouterr().err
        assert "F-04" in err
        assert "gone from the document" in err

    def test_the_judgments_survive_the_refusal(self, workspace: Path) -> None:
        store, _ = _judged(workspace)
        before = Session.open(Path(store), rater_id="r").estimates()
        (workspace / "findings.yaml").write_text(_doc(("F-01", "F-02", "F-03")), encoding="utf-8")
        main(["load", "--store", store, "--findings", str(workspace / "findings.yaml")])
        assert Session.open(Path(store), rater_id="r").estimates() == before

    def test_accepting_a_removal_records_it_in_the_log(self, workspace: Path) -> None:
        store, _ = _judged(workspace)
        (workspace / "findings.yaml").write_text(_doc(("F-01", "F-02", "F-03")), encoding="utf-8")
        before = Store.open(Path(store)).log_hash()
        code = main(
            [
                "load",
                "--store",
                store,
                "--findings",
                str(workspace / "findings.yaml"),
                "--rater",
                "saso",
                "--accept-removals",
            ]
        )
        assert code == 0
        recorded = Store.open(Path(store)).accepted_removals()
        assert len(recorded) == 1
        assert recorded[0].finding_id == "F-04"
        assert recorded[0].rater_id == "saso"
        assert recorded[0].comparisons > 0
        assert Store.open(Path(store)).log_hash() != before

    def test_an_unjudged_finding_may_be_removed_freely(self, workspace: Path) -> None:
        """Nothing was decided about it, so nothing can be lost."""
        store = _loaded(workspace)
        (workspace / "findings.yaml").write_text(_doc(("F-01", "F-02", "F-03")), encoding="utf-8")
        assert main(["load", "--store", store, "--findings", str(workspace / "findings.yaml")]) == 0

    def test_accepting_without_a_rater_is_refused(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The audit trail's "who" must not default on the path that writes one."""
        store, _ = _judged(workspace)
        (workspace / "findings.yaml").write_text(_doc(("F-01", "F-02", "F-03")), encoding="utf-8")
        code = main(
            [
                "load",
                "--store",
                store,
                "--findings",
                str(workspace / "findings.yaml"),
                "--accept-removals",
            ]
        )
        assert code == 1
        assert "--rater is required" in capsys.readouterr().err


class TestOrdinaryErrorsAreNamedNotTracebacks:
    """Every one of these produced a raw traceback before.

    The store cases are the sharper half: the log is flushed per record precisely
    so a crash cannot lose a judgment, and a crash *during* that write is exactly
    what leaves a truncated final line — which was then unreadable.
    """

    def test_a_missing_findings_file(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = _loaded(workspace)
        assert main(["load", "--store", store, "--findings", "absent.yaml"]) == 1
        assert "FindingsFileError" in capsys.readouterr().err

    def test_malformed_yaml(self, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
        store = _loaded(workspace)
        bad = workspace / "bad.yaml"
        bad.write_text("findings:\n  - id: F\n   bad: [\n", encoding="utf-8")
        assert main(["load", "--store", store, "--findings", str(bad)]) == 1
        assert "not valid YAML" in capsys.readouterr().err

    def test_a_corrupt_meta_file(self, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
        store = _loaded(workspace)
        (Path(store) / "meta.json").write_text("{not json", encoding="utf-8")
        assert main(["status", "--store", store, "--target", "3"]) == 1
        assert "not valid JSON" in capsys.readouterr().err

    def test_a_truncated_log_line(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = _loaded(workspace)
        (Path(store) / "comparisons.jsonl").write_text('{"kind": "compa', encoding="utf-8")
        assert main(["status", "--store", store, "--target", "3"]) == 1
        assert "not valid JSON" in capsys.readouterr().err

    def test_a_truncated_log_line_is_named_when_appending_too(self, workspace: Path) -> None:
        """The sequence number is read from that final line."""
        store = _loaded(workspace)
        (Path(store) / "comparisons.jsonl").write_text('{"kind": "compa', encoding="utf-8")
        opened = Store.open(Path(store))
        with pytest.raises(StoreSchemaError, match="not valid JSON"):
            opened.append_comparison(
                left_id="F-01",
                right_id="F-02",
                outcome=Outcome.LEFT,
                rater_id="r",
                session_id="s",
            )

    def test_a_missing_store_is_still_distinguished_from_a_missing_document(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Two different refusals, and the earlier test only ever reached one.

        Its fixture never ran `init`, so `Store.open` failed first and the case it
        was named for — a missing findings file — was never exercised.
        """
        assert main(["load", "--store", str(tmp_path / "nope"), "--findings", "x.yaml"]) == 1
        assert "StoreSchemaError" in capsys.readouterr().err


class TestRetractionsMustNameSomething:
    def test_a_retraction_of_a_nonexistent_seq_is_refused(self, tmp_path: Path) -> None:
        store = Store.create(tmp_path / "s")
        store.put_findings(parse_findings(_doc(("P", "Q"))).admitted)
        store.put_load_summary(())
        store.append_comparison(
            left_id="P", right_id="Q", outcome=Outcome.LEFT, rater_id="r", session_id="a"
        )
        with pytest.raises(RetractionError, match="999"):
            store.append_retraction(retracts_seq=999, rater_id="r", session_id="a")

    def test_a_second_retraction_of_the_same_comparison_is_refused(self, tmp_path: Path) -> None:
        """Inert to the fit, but it still changes the hash published as provenance."""
        store = Store.create(tmp_path / "s")
        store.put_findings(parse_findings(_doc(("P", "Q"))).admitted)
        store.put_load_summary(())
        store.append_comparison(
            left_id="P", right_id="Q", outcome=Outcome.LEFT, rater_id="r", session_id="a"
        )
        store.append_retraction(retracts_seq=1, rater_id="r", session_id="a")
        with pytest.raises(RetractionError):
            store.append_retraction(retracts_seq=1, rater_id="r", session_id="a")
        assert len(store.log()) == 2


class TestEveryTextFieldIsGuarded:
    def test_empty_evidence_is_refused(self) -> None:
        """The fragments are the field the schema exists to preserve."""
        document = (
            "findings:\n  - id: E1\n    observation: o\n    evidence: []\n"
            "    consequence: c\n    detectable_by: assert\n    tier: defect\n"
        )
        with pytest.raises(FindingSchemaError, match="no evidence"):
            parse_findings(document)

    def test_evidence_of_only_blank_fragments_is_refused(self) -> None:
        document = (
            "findings:\n  - id: E1\n    observation: o\n    evidence: ['   ']\n"
            "    consequence: c\n    detectable_by: assert\n    tier: defect\n"
        )
        with pytest.raises(FindingSchemaError, match="no evidence"):
            parse_findings(document)


class TestSelfReferentialCut:
    def test_a_cut_naming_one_finding_twice_is_refused(self, workspace: Path) -> None:
        """Reported as "inverted" before, which is a confusing name for it."""
        from comparative_judgment.core.errors import CutError

        store, ranked = _judged(workspace)
        session = Session.open(Path(store), rater_id="r", appearance_target=3)
        with pytest.raises(CutError):
            session.set_cuts(
                [
                    Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[0], calibration_note=NOTE),
                    Cut(CutName.HIGH_MEDIUM, ranked[1], ranked[2]),
                    Cut(CutName.MEDIUM_LOW, ranked[2], ranked[3]),
                ]
            )


class TestExportToAMissingDirectory:
    def test_it_is_a_named_refusal(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store, ranked = _judged(workspace)
        session = Session.open(Path(store), rater_id="r", appearance_target=3)
        session.set_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1], calibration_note=NOTE),
                Cut(CutName.HIGH_MEDIUM, ranked[1], ranked[2]),
                Cut(CutName.MEDIUM_LOW, ranked[2], ranked[3]),
            ]
        )
        session.assign()
        target = workspace / "no" / "such" / "dir" / "severity.json"
        assert main(["export", "--store", store, "--target", "3", "--out", str(target)]) == 1
        assert "cannot write" in capsys.readouterr().err
        assert not target.exists()


class TestTheRunIdIsDerived:
    def _exported(self, workspace: Path) -> dict[str, object]:
        store, ranked = _judged(workspace)
        session = Session.open(Path(store), rater_id="r", appearance_target=3)
        session.set_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1], calibration_note=NOTE),
                Cut(CutName.HIGH_MEDIUM, ranked[1], ranked[2]),
                Cut(CutName.MEDIUM_LOW, ranked[2], ranked[3]),
            ]
        )
        session.assign()
        out = workspace / "severity.json"
        session.export(out)
        loaded: dict[str, object] = json.loads(out.read_text(encoding="utf-8"))
        return loaded

    def test_the_severity_file_carries_one(self, workspace: Path) -> None:
        payload = self._exported(workspace)
        assert isinstance(payload["run_id"], str)
        assert payload["run_id"]

    def test_two_exports_over_an_unchanged_log_carry_the_same_id(self, workspace: Path) -> None:
        """Derived, not minted: no exclusion needs carving out of byte-identity."""
        first = self._exported(workspace)
        session = Session.open(workspace / ".cj-store", rater_id="r", appearance_target=3)
        second_path = workspace / "again.json"
        session.export(second_path)
        second = json.loads(second_path.read_text(encoding="utf-8"))
        assert first["run_id"] == second["run_id"]

    def test_changing_a_cut_changes_the_id(self, workspace: Path) -> None:
        """The cuts are not in the log; three boundaries are three results."""
        first = self._exported(workspace)
        session = Session.open(workspace / ".cj-store", rater_id="r", appearance_target=3)
        ranked = [e.finding_id for e in session.fit().ranked()]
        session.set_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1], calibration_note="different"),
                Cut(CutName.HIGH_MEDIUM, ranked[1], ranked[2]),
                Cut(CutName.MEDIUM_LOW, ranked[2], ranked[3]),
            ]
        )
        moved = workspace / "moved.json"
        session.export(moved)
        assert json.loads(moved.read_text(encoding="utf-8"))["run_id"] != first["run_id"]


class TestCostFigureCountsPlacedItems:
    def test_untouched_items_are_not_in_the_denominator(self, workspace: Path) -> None:
        """The figure is read part-way through, which is when they deflate it."""
        store = _loaded(workspace)
        session = Session.open(Path(store), rater_id="r", appearance_target=10)
        session.record(Outcome.LEFT)
        placed = sum(1 for e in session.estimates() if e.appearances > 0)
        assert placed == 2
        assert len(session.estimates()) == 4
        assert session.mean_comparisons_per_item() == pytest.approx(0.5)


class TestErrorsAreAllNamedRefusals:
    def test_every_module_error_descends_from_the_base(self) -> None:
        """An error that is not one of these escapes the CLI and prints a stack."""
        from comparative_judgment.core import errors

        base = errors.ComparativeJudgmentError
        defined = [
            value
            for name, value in vars(errors).items()
            if isinstance(value, type) and issubclass(value, Exception) and not name.startswith("_")
        ]
        assert defined
        assert all(issubclass(e, base) for e in defined)

    def test_no_module_raises_a_bare_builtin_error(self) -> None:
        """`raise ValueError` in core is a traceback waiting for a user."""
        import ast

        src = Path(__file__).resolve().parents[1] / "src" / "comparative_judgment"
        offenders: list[str] = []
        for path in sorted(src.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Raise) or node.exc is None:
                    continue
                call = node.exc
                name = (
                    call.func.id
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    else None
                )
                if name in {"ValueError", "KeyError", "TypeError", "RuntimeError"}:
                    offenders.append(f"{path.name}:{node.lineno} raises {name}")
        assert not offenders, offenders


class TestASeverityRowNeedsAFindingTheStoreHolds:
    def test_a_band_for_a_finding_the_store_does_not_hold_is_refused(self, tmp_path: Path) -> None:
        """Schema 2 writes each row's content hash and comparison counts from the store.

        A finding the store does not hold has neither, so a row for it would be a
        band with its basis missing -- the row schema 2 exists to make impossible.
        Refused as an unknown item, not as a write failure: nothing is wrong with
        where the file was asked to go, and a caller catching `SeverityWriteError`
        to report a bad output path would mislabel this.
        """
        store = Store.create(tmp_path / "s")
        store.put_findings(parse_findings(_doc(("P", "Q"))).admitted)
        store.put_load_summary(())
        stranger = BandAssignment(finding_id="ZZ", band=Band.LOW, theta=0.0)
        with pytest.raises(UnknownItemError, match="ZZ"):
            build_payload(assignments=(stranger,), store=store)
