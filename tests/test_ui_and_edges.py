"""The terminal front end, and the branches coverage found unreached.

The TUI is tested for the two things that can actually regress: that it formats
a finding's fields into a pane, and that a keypress reaches the session. Driving
a live textual app would need an async harness and a new dependency, and would
mostly assert that textual works. What matters here is the delegation — the
front end holds no state, so its whole job is to call the session and redraw.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Final

import pytest

from comparative_judgment.core.errors import (
    CutError,
    FindingSchemaError,
    StoreSchemaError,
)
from comparative_judgment.core.findings import parse_findings
from comparative_judgment.core.graph import components
from comparative_judgment.core.models import (
    Comparison,
    Cut,
    CutName,
    DetectableBy,
    Finding,
    Outcome,
    Progress,
    Tier,
)
from comparative_judgment.core.session import Session
from comparative_judgment.core.shapes import as_dict, as_int, as_list, as_str, field
from comparative_judgment.core.store import Store
from comparative_judgment.ui.tui import ComparisonApp, _remaining, _render_card

DOC: Final[str] = "findings:\n" + "".join(
    f"  - id: F-{i}\n    observation: observation number {i}\n"
    f"    evidence: ['line {i}: alpha', 'line {i + 9}: beta']\n"
    f"    consequence: consequence number {i}\n"
    f"    detectable_by: assert\n    tier: defect\n"
    for i in range(4)
)


def _session(tmp_path: Path, *, target: int = 3) -> Session:
    store = Store.create(tmp_path / "s")
    loaded = parse_findings(DOC)
    store.put_findings(loaded.admitted)
    store.put_load_summary(loaded.excluded_questions)
    return Session(store, rater_id="r", appearance_target=target)


class TestRenderCard:
    """Formatting lives in the front end. This is the proof it is not in the core."""

    def _finding(self) -> Finding:
        return Finding(
            id="F-77",
            content_hash="h",
            observation="The agent promised a refund it never processed.",
            evidence=("line 4: consider it done", "line 9: refund -> ERROR"),
            consequence="Money the caller is expecting will not arrive.",
            detectable_by=DetectableBy.ASSERT,
            tier=Tier.DEFECT,
        )

    def test_every_field_reaches_the_pane(self) -> None:
        rendered = _render_card(self._finding(), "A", 2, 10)
        assert "F-77" in rendered
        assert "promised a refund" in rendered
        assert "will not arrive" in rendered

    def test_evidence_fragments_render_separately(self) -> None:
        """Two fragments must not be run together into one line.

        The row schema requires fragments from different points in a call to stay
        visibly separate, and this is the last place that can be lost.
        """
        rendered = _render_card(self._finding(), "A", 0, 10)
        assert rendered.count("•") == 2
        assert "consider it done" in rendered
        assert "refund -> ERROR" in rendered

    def test_appearance_progress_is_shown(self) -> None:
        assert "(2/10 appearances)" in _render_card(self._finding(), "A", 2, 10)


class _StubWidget:
    def __init__(self) -> None:
        self.text = ""

    def update(self, value: str) -> None:
        self.text = value


class TestKeypressesReachTheSession:
    """The front end holds no state; every action must delegate."""

    def _app(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ComparisonApp:
        app = ComparisonApp(_session(tmp_path))
        widgets: dict[str, _StubWidget] = {}

        def fake_query_one(selector: str, _type: object = None) -> _StubWidget:
            return widgets.setdefault(selector, _StubWidget())

        monkeypatch.setattr(app, "query_one", fake_query_one)
        return app

    def test_choosing_left_records_a_left_outcome(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = self._app(tmp_path, monkeypatch)
        app.action_choose_left()
        recorded = app._session._store.active_comparisons()
        assert len(recorded) == 1
        assert recorded[0].outcome is Outcome.LEFT

    def test_choosing_right_records_a_right_outcome(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = self._app(tmp_path, monkeypatch)
        app.action_choose_right()
        assert app._session._store.active_comparisons()[0].outcome is Outcome.RIGHT

    def test_tie_records_a_tie(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        app = self._app(tmp_path, monkeypatch)
        app.action_tie()
        assert app._session._store.active_comparisons()[0].outcome is Outcome.TIE

    def test_undo_retracts_and_the_log_keeps_the_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = self._app(tmp_path, monkeypatch)
        app.action_choose_left()
        app.action_undo()
        assert app._session._store.active_comparisons() == ()
        assert len(app._session._store.log()) == 2

    def test_undo_with_nothing_recorded_is_harmless(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = self._app(tmp_path, monkeypatch)
        app.action_undo()
        assert app._session._store.active_comparisons() == ()

    def test_the_pane_is_redrawn_after_a_judgment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = self._app(tmp_path, monkeypatch)
        app.on_mount()
        first = app.query_one("#left").text  # type: ignore[attr-defined]
        app.action_choose_left()
        assert app.query_one("#left").text != first  # type: ignore[attr-defined]

    def test_a_finished_batch_says_so_and_records_nothing_further(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = ComparisonApp(_session(tmp_path, target=1))
        widgets: dict[str, _StubWidget] = {}
        monkeypatch.setattr(
            app, "query_one", lambda s, _t=None: widgets.setdefault(s, _StubWidget())
        )
        while app._session.next_pair() is not None:
            app.action_choose_left()
        spent = len(app._session._store.active_comparisons())

        app.action_choose_left()  # a keypress after the end must be inert
        assert len(app._session._store.active_comparisons()) == spent
        app.on_mount()
        assert "complete" in widgets["#progress"].text


class TestRemainingAndTheClock:
    """D40 in the front end: spent beside remaining, and this sitting's time."""

    def _app(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[ComparisonApp, dict[str, _StubWidget]]:
        app = ComparisonApp(session)
        widgets: dict[str, _StubWidget] = {}
        monkeypatch.setattr(
            app, "query_one", lambda s, _t=None: widgets.setdefault(s, _StubWidget())
        )
        return app, widgets

    def test_the_progress_line_shows_spent_and_remaining(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, widgets = self._app(_session(tmp_path), monkeypatch)
        app.on_mount()
        assert "comparisons: 0" in widgets["#progress"].text
        assert "remaining: at least 6" in widgets["#progress"].text
        app.action_choose_left()
        assert "comparisons: 1" in widgets["#progress"].text
        assert "remaining: at least 5" in widgets["#progress"].text

    def test_the_clock_shows_the_sitting_from_the_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ticks = iter([10.0, 10.0, 85.0, 3_700.0])
        store = Store.create(tmp_path / "s")
        store.put_findings(parse_findings(DOC).admitted)
        session = Session(store, rater_id="r", monotonic=lambda: next(ticks))
        app, widgets = self._app(session, monkeypatch)
        app.on_mount()
        assert widgets["#clock"].text == "this sitting: 0:00"
        app._tick()
        assert widgets["#clock"].text == "this sitting: 1:15"
        app._tick()
        assert widgets["#clock"].text == "this sitting: 1:01:30"

    def test_the_clock_starts_once_the_first_frame_is_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, _ = self._app(_session(tmp_path), monkeypatch)
        started: list[tuple[float, object]] = []
        monkeypatch.setattr(app, "set_interval", lambda every, then: started.append((every, then)))
        app.on_ready()
        assert started == [(1.0, app._tick)]

    def test_a_blocked_batch_shows_no_estimate(self) -> None:
        blocked = Progress(
            admitted=2,
            excluded_questions=0,
            comparisons_spent=1,
            ties=0,
            min_appearances=1,
            mean_appearances=1.0,
            appearance_target=3,
            complete=False,
            blocked_reason="inverted",
        )
        assert _remaining(blocked) == "remaining: -"
        exact = dataclasses.replace(blocked, blocked_reason="", comparisons_remaining=4)
        assert _remaining(exact) == "remaining: 4"


class TestShapesRefusals:
    """Every narrowing helper names what it found, not just that it failed."""

    @pytest.mark.parametrize(
        ("helper", "bad"),
        [(as_dict, []), (as_list, {}), (as_str, 5), (as_int, "five")],
    )
    def test_wrong_type_names_the_type_it_found(self, helper: object, bad: object) -> None:
        with pytest.raises(StoreSchemaError, match="expected"):
            helper(bad, "somewhere.json", error=StoreSchemaError)  # type: ignore[operator]

    def test_a_bool_is_not_an_integer(self) -> None:
        """bool subclasses int; a boolean sequence number is corrupt, not a number."""
        with pytest.raises(StoreSchemaError, match="bool"):
            as_int(True, "log.jsonl", error=StoreSchemaError)

    def test_a_missing_field_names_the_field_and_the_error_type(self) -> None:
        with pytest.raises(FindingSchemaError, match="observation"):
            field({}, "observation", "findings.yaml", error=FindingSchemaError)


class TestSessionEdges:
    def test_cost_figure_with_no_findings_is_zero_not_a_division_error(
        self, tmp_path: Path
    ) -> None:
        store = Store.create(tmp_path / "empty")
        assert Session(store, rater_id="r").mean_comparisons_per_item() == 0.0

    def test_placement_stops_when_a_cut_has_inverted(self, tmp_path: Path) -> None:
        """A boundary that no longer means anything cannot guide placement.

        It offers no pair rather than guessing -- and the compensating half is
        asserted here too. The earlier version of this test checked only that
        `next_pair()` returned `None`, with a docstring claiming `place()` reports
        the inversion by name and no assertion making that so. The suite therefore
        encoded the *swallowing* as correct while the front end rendered that same
        `None` as "batch complete".
        """
        session = _session(tmp_path)
        while session.next_pair() is not None:
            session.record(Outcome.LEFT)
        ranked = [e.finding_id for e in session._fit().ranked()]
        session._store.put_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1]),
                Cut(CutName.HIGH_MEDIUM, ranked[1], ranked[2]),
                # Deliberately inverted: the lower-ranked finding named as above.
                Cut(CutName.MEDIUM_LOW, ranked[3], ranked[2]),
            ]
        )
        session._invalidate()
        assert session.next_pair() is None

        with pytest.raises(CutError, match="medium_low"):
            session.place()

        progress = session.progress()
        assert progress.blocked_reason
        assert "medium_low" in progress.blocked_reason
        assert not progress.complete

    def test_the_front_end_says_blocked_not_complete(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rater sits in front of this surface, and it said "complete"."""
        session = _session(tmp_path)
        while session.next_pair() is not None:
            session.record(Outcome.LEFT)
        ranked = [e.finding_id for e in session._fit().ranked()]
        session._store.put_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1]),
                Cut(CutName.HIGH_MEDIUM, ranked[1], ranked[2]),
                Cut(CutName.MEDIUM_LOW, ranked[3], ranked[2]),
            ]
        )
        session._invalidate()

        app = ComparisonApp(session)
        widgets: dict[str, _StubWidget] = {}
        monkeypatch.setattr(
            app, "query_one", lambda s, _t=None: widgets.setdefault(s, _StubWidget())
        )
        app.on_mount()
        assert "BLOCKED" in widgets["#progress"].text
        assert "complete" not in widgets["#progress"].text
        assert "medium_low" in widgets["#left"].text

    def test_finishing_placement_is_not_reported_as_an_appearance_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After cuts exist, "every item at N appearances" is not what happened.

        The completion message was unconditional, so the placement path — which
        stops after about three comparisons per item, deliberately — reported the
        bootstrap's finishing condition instead of its own.
        """
        session = _session(tmp_path)
        while session.next_pair() is not None:
            session.record(Outcome.LEFT)
        ranked = [e.finding_id for e in session._fit().ranked()]
        session._store.put_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1]),
                Cut(CutName.HIGH_MEDIUM, ranked[1], ranked[2]),
                Cut(CutName.MEDIUM_LOW, ranked[2], ranked[3]),
            ]
        )
        session._invalidate()
        assert session.next_pair() is None

        app = ComparisonApp(session)
        widgets: dict[str, _StubWidget] = {}
        monkeypatch.setattr(
            app, "query_one", lambda s, _t=None: widgets.setdefault(s, _StubWidget())
        )
        app.on_mount()
        assert "placement complete" in widgets["#progress"].text
        assert "appearances" not in widgets["#progress"].text
        assert "cj bands" in widgets["#left"].text


class TestGraphMergeDirection:
    def test_components_do_not_depend_on_which_side_won(self) -> None:
        """Union-find merges toward the smaller root, so the result is stable.

        The two cases matter because they call union() with its arguments in
        opposite orders: "a beats z" unions (a, z) and takes no swap, while
        "z beats a" unions (z, a) and does. Both must give the same components,
        and the first version of this test exercised only one of them -- both
        its cases happened to resolve to union("a", "z").
        """
        a_wins = [Comparison(1, "a", "z", Outcome.LEFT, "r", "s", "t")]
        z_wins = [Comparison(1, "a", "z", Outcome.RIGHT, "r", "s", "t")]
        assert components(["a", "z"], a_wins) == (("a", "z"),)
        assert components(["a", "z"], z_wins) == (("a", "z"),)
        assert components(["z", "a"], z_wins) == (("a", "z"),)


class TestSelfComparisonGuard:
    def test_a_finding_cannot_be_compared_with_itself(self, tmp_path: Path) -> None:
        """It carries no judgment but would still enter the win matrix."""
        from comparative_judgment.core.errors import UnknownItemError

        session = _session(tmp_path)
        with pytest.raises(UnknownItemError, match="itself"):
            session._store.append_comparison(
                left_id="F-0", right_id="F-0", outcome=Outcome.LEFT, rater_id="r", session_id="s"
            )


class TestSequenceNumbering:
    def test_sequence_continues_across_a_reopen(self, tmp_path: Path) -> None:
        """The seq counter is cached in memory; a reopened store must not restart it."""
        session = _session(tmp_path)
        session.record(Outcome.LEFT)
        session.record(Outcome.LEFT)

        reopened = Store.open(session._store.path)
        third = reopened.append_comparison(
            left_id="F-0", right_id="F-1", outcome=Outcome.LEFT, rater_id="r", session_id="s"
        )
        assert third.seq == 3
        assert [e.seq for e in reopened.log()] == [1, 2, 3]
