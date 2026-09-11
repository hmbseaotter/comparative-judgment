"""CLI tests.

The sweep found this surface at 17% coverage: the part a user actually touches,
essentially unverified. It had been exercised by hand end to end, which proves it
worked once on one machine — not that it keeps working.

The refusals matter most. Every one of them is a decision the tool makes on a
user's behalf, and a refusal that silently stops refusing is indistinguishable
from one that never existed.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from comparative_judgment.cli import main

#: What the top cut was drawn against. Required by `cj cuts`: a pairwise scale
#: has no origin, so an internally perfect ordering can sit a whole band too high
#: and nothing downstream would record that it does.
NOTE = "Critical means the caller acts on a false statement about their booking."

BASE_FINDINGS = textwrap.dedent(
    """
    findings:
      - id: F-01
        observation: The agent confirmed a completed exchange before the tool ran.
        evidence:
          - "line 42: I have moved you to row C."
          - "line 51: exchange_seats -> ERROR"
        consequence: The caller left believing they had different seats.
        detectable_by: assert
        tier: defect
      - id: F-02
        observation: A refund window was stated with no policy lookup.
        evidence: ["line 18: a fourteen day window"]
        consequence: A commitment nothing in the log supports.
        detectable_by: judge
        tier: defect
      - id: F-03
        observation: The booking reference was read back incorrectly.
        evidence: ["line 9: B-K-4-4-2"]
        consequence: The caller cannot quote a working reference.
        detectable_by: assert
        tier: defect
      - id: F-04
        observation: Two identical confirmations in consecutive turns.
        evidence: ["line 22: confirm that"]
        consequence: Irritation that stops when the call does.
        detectable_by: judge
        tier: defect
      - id: Q-01
        observation: Is the verification gate meant to be this narrow?
        evidence: ["line 12: verify = partial"]
        consequence: Unknown until the owning team answers.
        detectable_by: human
        tier: question
    """
).lstrip()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "findings.yaml").write_text(BASE_FINDINGS, encoding="utf-8")
    return tmp_path


def _store(workspace: Path) -> str:
    return str(workspace / ".cj-store")


def _disconnected(workspace: Path) -> None:
    """A store whose judged findings fall into two groups nothing joins.

    F-01/F-02 are judged against each other and F-03/F-04 against each other,
    with no comparison bridging the two. Bradley-Terry estimates differences, so
    the two groups have independent origins and their numbers are not comparable.

    The cuts are written through the store rather than through `set_cuts`, which
    refuses this state by design. That is the point: this constructs what the
    guard exists to catch, and a user reaches it by retracting the comparison
    that used to bridge the groups.
    """
    from comparative_judgment.core.models import Cut, CutName, Outcome
    from comparative_judgment.core.store import Store

    main(["init", "--store", _store(workspace)])
    main(["load", "--store", _store(workspace), "--findings", str(workspace / "findings.yaml")])
    store = Store.open(workspace / ".cj-store")
    for left, right in (("F-01", "F-02"), ("F-03", "F-04")):
        store.append_comparison(
            left_id=left, right_id=right, outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
    store.put_cuts(
        [
            Cut(CutName.CRITICAL_HIGH, "F-01", "F-03", calibration_note=NOTE),
            Cut(CutName.HIGH_MEDIUM, "F-03", "F-04"),
            Cut(CutName.MEDIUM_LOW, "F-04", "F-02"),
        ]
    )


class TestInitAndLoad:
    def test_init_creates_a_store(self, workspace: Path) -> None:
        assert main(["init", "--store", _store(workspace)]) == 0
        assert (workspace / ".cj-store" / "meta.json").is_file()

    def test_load_admits_defects_and_reports_exclusions(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["init", "--store", _store(workspace)])
        assert (
            main(
                [
                    "load",
                    "--store",
                    _store(workspace),
                    "--findings",
                    str(workspace / "findings.yaml"),
                ]
            )
            == 0
        )
        out = capsys.readouterr().out
        assert "admitted 4 finding(s)" in out
        assert "Q-01" in out

    def test_a_named_refusal_exits_non_zero_without_a_traceback(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Every refusal is a decision, not a crash, and must read like one."""
        code = main(["load", "--store", _store(workspace), "--findings", "nope.yaml"])
        assert code == 1
        err = capsys.readouterr().err
        assert "StoreSchemaError" in err
        assert "Traceback" not in err


class TestRevisionRefusal:
    """A judged finding whose text changed must not silently keep its judgments."""

    def _judged(self, workspace: Path) -> None:
        from comparative_judgment.core.models import Outcome
        from comparative_judgment.core.session import Session
        from comparative_judgment.core.store import Store

        main(["init", "--store", _store(workspace)])
        main(["load", "--store", _store(workspace), "--findings", str(workspace / "findings.yaml")])
        session = Session(Store.open(workspace / ".cj-store"), rater_id="r", appearance_target=2)
        while session.next_pair() is not None:
            session.record(Outcome.LEFT)

    def test_reloading_unchanged_findings_is_fine(self, workspace: Path) -> None:
        self._judged(workspace)
        assert (
            main(
                [
                    "load",
                    "--store",
                    _store(workspace),
                    "--findings",
                    str(workspace / "findings.yaml"),
                ]
            )
            == 0
        )

    def test_a_changed_judged_finding_is_refused_by_name(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._judged(workspace)
        revised = BASE_FINDINGS.replace(
            "The booking reference was read back incorrectly.",
            "COMPLETELY DIFFERENT AND FAR MILDER PROBLEM.",
        )
        (workspace / "findings.yaml").write_text(revised, encoding="utf-8")

        code = main(
            ["load", "--store", _store(workspace), "--findings", str(workspace / "findings.yaml")]
        )
        assert code == 1
        err = capsys.readouterr().err
        assert "REFUSED" in err
        assert "F-03" in err
        assert "comparison(s) made against the old text" in err

    def test_accepting_a_revision_is_recorded_in_the_log(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The audit trail: who, when, which finding, which text it moved between."""
        from comparative_judgment.core.store import Store

        self._judged(workspace)
        revised = BASE_FINDINGS.replace(
            "The booking reference was read back incorrectly.",
            "COMPLETELY DIFFERENT AND FAR MILDER PROBLEM.",
        )
        (workspace / "findings.yaml").write_text(revised, encoding="utf-8")

        code = main(
            [
                "load",
                "--store",
                _store(workspace),
                "--findings",
                str(workspace / "findings.yaml"),
                "--rater",
                "saso",
                "--accept-revisions",
            ]
        )
        assert code == 0
        assert "accepted revision: F-03" in capsys.readouterr().out

        accepted = Store.open(workspace / ".cj-store").accepted_revisions()
        assert len(accepted) == 1
        record = accepted[0]
        assert record.finding_id == "F-03"
        assert record.rater_id == "saso"
        assert record.old_hash != record.new_hash
        assert record.timestamp.endswith("Z")

    def test_accepting_changes_the_log_hash(self, workspace: Path) -> None:
        """So a severity file naming that hash cannot conceal the acceptance."""
        from comparative_judgment.core.store import Store

        self._judged(workspace)
        before = Store.open(workspace / ".cj-store").log_hash()
        revised = BASE_FINDINGS.replace(
            "The booking reference was read back incorrectly.", "Rewritten entirely."
        )
        (workspace / "findings.yaml").write_text(revised, encoding="utf-8")
        main(
            [
                "load",
                "--store",
                _store(workspace),
                "--findings",
                str(workspace / "findings.yaml"),
                "--rater",
                "saso",
                "--accept-revisions",
            ]
        )
        assert Store.open(workspace / ".cj-store").log_hash() != before

    def test_an_unjudged_finding_may_change_freely(self, workspace: Path) -> None:
        """Nothing was decided about it, so nothing can be reinterpreted."""
        main(["init", "--store", _store(workspace)])
        main(["load", "--store", _store(workspace), "--findings", str(workspace / "findings.yaml")])
        revised = BASE_FINDINGS.replace("A refund window was stated", "Reworded entirely")
        (workspace / "findings.yaml").write_text(revised, encoding="utf-8")
        assert (
            main(
                [
                    "load",
                    "--store",
                    _store(workspace),
                    "--findings",
                    str(workspace / "findings.yaml"),
                ]
            )
            == 0
        )


class TestFullFlow:
    def _bootstrap(self, workspace: Path) -> list[str]:
        from comparative_judgment.core.models import Outcome
        from comparative_judgment.core.session import Session
        from comparative_judgment.core.store import Store

        main(["init", "--store", _store(workspace)])
        main(["load", "--store", _store(workspace), "--findings", str(workspace / "findings.yaml")])
        session = Session(Store.open(workspace / ".cj-store"), rater_id="r", appearance_target=3)
        while session.next_pair() is not None:
            session.record(Outcome.LEFT)
        return [e.finding_id for e in session._fit().ranked()]

    def test_status_reports_the_measured_cost_figures(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._bootstrap(workspace)
        assert main(["status", "--store", _store(workspace), "--target", "3"]) == 0
        out = capsys.readouterr().out
        assert "comparisons/item" in out
        assert "excluded questions  1" in out

    def test_status_reports_a_finished_placement_as_complete(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """C-4 at the surface the audit measured it at.

        The TUI compensated for this by reading `placing` and `cj status` did
        not, so the spec's acceptance line -- that after cuts exist the
        appearance target is not reported as the finishing condition -- was met
        in one front end and not the other. The test that named the defect
        asserted the TUI widget's text, which is how it stayed green while the
        CLI printed `complete no` for a finished placement.

        `--target 5` is deliberate. The bug is only visible where the appearance
        target exceeds the placement quota of three; at `--target 3` the two
        criteria agree and the check would pass either way.
        """
        from comparative_judgment.core.findings import parse_findings
        from comparative_judgment.core.models import Outcome
        from comparative_judgment.core.session import Session
        from comparative_judgment.core.store import Store

        ranked = self._bootstrap(workspace)
        store = _store(workspace)
        assert (
            main(
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
                    "--critical-high-note",
                    NOTE,
                ]
            )
            == 0
        )

        session = Session(Store.open(workspace / ".cj-store"), rater_id="r", appearance_target=5)
        existing = list(session._store.findings())
        newcomer = parse_findings(
            "findings:\n"
            "  - id: F-NEW\n"
            "    observation: A newly reviewed call had the same problem.\n"
            "    evidence: ['line 7: fragment']\n"
            "    consequence: The caller is out of pocket.\n"
            "    detectable_by: judge\n"
            "    tier: defect"
        ).admitted
        session._store.put_findings([*existing, *newcomer])
        session._invalidate()
        while session.next_pair() is not None:
            session.record(Outcome.RIGHT)

        capsys.readouterr()
        assert main(["status", "--store", store, "--target", "5"]) == 0
        out = capsys.readouterr().out

        assert "mode                placing against cuts" in out
        assert "complete            yes" in out, (
            f"a finished placement is reported as unfinished:\n{out}"
        )
        assert "below target" not in out, (
            "the appearance target is reported as the finishing condition after cuts "
            f"exist, which is the acceptance line this violates:\n{out}"
        )
        assert "unplaced" not in out

    def test_status_warns_about_disconnected_components(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Two judged groups with nothing joining them, which is the real case.

        An earlier version of this test asserted the warning against a batch with
        *no* comparisons at all, where every item was its own component. That
        passed while saying something false: an unstarted batch is not
        disconnected, it is unstarted, and warning about it trains a rater to
        ignore the one message that matters once judgments exist.
        """
        _disconnected(workspace)
        main(["status", "--store", _store(workspace)])
        out = capsys.readouterr().out
        assert "disconnected component" in out
        assert "F-01" in out and "F-03" in out

    def test_fit_names_the_components_it_cannot_compare(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _disconnected(workspace)
        assert main(["fit", "--store", _store(workspace)]) == 0
        out = capsys.readouterr().out
        assert "never compared against" in out

    def test_bands_and_export_refuse_across_components(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A boundary between two groups reports the prior, not a judgment."""
        _disconnected(workspace)
        store = _store(workspace)
        assert main(["bands", "--store", store]) == 1
        assert "DisconnectedComparisonsError" in capsys.readouterr().err

        out_path = workspace / "sev.json"
        assert main(["export", "--store", store, "--out", str(out_path)]) == 1
        assert not out_path.exists()

    def test_fit_prints_the_scale(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._bootstrap(workspace)
        assert main(["fit", "--store", _store(workspace), "--target", "3"]) == 0
        out = capsys.readouterr().out
        assert "converged in" in out
        assert "lambda = 0.5" in out

    def test_cuts_then_bands_then_export(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ranked = self._bootstrap(workspace)
        store = _store(workspace)
        assert (
            main(
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
                    "--critical-high-note",
                    NOTE,
                ]
            )
            == 0
        )
        assert main(["bands", "--store", store, "--target", "3"]) == 0
        out = capsys.readouterr().out
        assert "critical" in out and "low" in out

        target = workspace / "severity.json"
        assert main(["export", "--store", store, "--target", "3", "--out", str(target)]) == 0
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert {s["id"] for s in payload["severities"]} == {"F-01", "F-02", "F-03", "F-04"}
        assert payload["comparison_log_hash"]
        assert payload["anchor_set_version"] == "1"

        # Schema 2: every row says what it scored and how well determined it is.
        #
        # Both exist because a band on its own is a conclusion with its basis
        # stripped off. `content_hash` is the text the rater saw, so a finding
        # edited after export stops matching and the consumer can say so without
        # running the producing tool. `appearances` and `informative` are the
        # evidence behind the placement, and they differ: a tie is a judgment
        # the fit excludes, so ten appearances with eight ties is a band placed
        # on two results.
        from comparative_judgment.core.store import Store

        assert payload["schema_version"] == "2"
        store_findings = {f.id: f for f in Store.open(Path(store)).findings()}
        for row in payload["severities"]:
            assert row["content_hash"] == store_findings[row["id"]].content_hash, (
                f"{row['id']} carries a hash that is not the text the store judged"
            )
            assert row["appearances"] >= row["informative"] >= 0, (
                f"{row['id']} reports more decided comparisons than comparisons"
            )
            assert row["appearances"] > 0, (
                f"{row['id']} is banded on no comparisons at all, which `unplaced` exists to "
                "report instead"
            )

    def test_export_is_byte_identical_across_runs(self, workspace: Path) -> None:
        ranked = self._bootstrap(workspace)
        store = _store(workspace)
        main(
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
                "--critical-high-note",
                NOTE,
            ]
        )
        first, second = workspace / "a.json", workspace / "b.json"
        main(["export", "--store", store, "--target", "3", "--out", str(first)])
        main(["export", "--store", store, "--target", "3", "--out", str(second)])
        assert first.read_bytes() == second.read_bytes()

    def test_the_findings_file_is_never_written_to(self, workspace: Path) -> None:
        source = workspace / "findings.yaml"
        before = source.read_bytes()
        ranked = self._bootstrap(workspace)
        store = _store(workspace)
        main(
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
                "--critical-high-note",
                NOTE,
            ]
        )
        main(["export", "--store", store, "--target", "3", "--out", str(workspace / "s.json")])
        assert source.read_bytes() == before


class TestCutArgumentParsing:
    def test_a_malformed_cut_specification_is_refused(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["init", "--store", _store(workspace)])
        main(["load", "--store", _store(workspace), "--findings", str(workspace / "findings.yaml")])
        code = main(
            [
                "cuts",
                "--store",
                _store(workspace),
                "--critical-high",
                "F-01",  # missing the ABOVE:BELOW separator
                "--high-medium",
                "F-02:F-03",
                "--medium-low",
                "F-03:F-04",
            ]
        )
        assert code == 1
        assert "ABOVE:BELOW" in capsys.readouterr().err

    def test_too_few_findings_for_three_cuts_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        small = tmp_path / "few.yaml"
        small.write_text(
            "findings:\n"
            "  - id: F-1\n    observation: a\n    evidence: ['x']\n"
            "    consequence: c\n    detectable_by: assert\n    tier: defect\n",
            encoding="utf-8",
        )
        store = str(tmp_path / ".cj-store")
        main(["init", "--store", store])
        main(["load", "--store", store, "--findings", str(small)])
        code = main(
            [
                "cuts",
                "--store",
                store,
                "--critical-high",
                "F-1:F-1",
                "--high-medium",
                "F-1:F-1",
                "--medium-low",
                "F-1:F-1",
            ]
        )
        assert code == 1
        assert "at least four findings" in capsys.readouterr().err
