"""Session API tests.

Two of these are doing more work than they look. The *derived cursor* tests
assert that resuming offers the comparison an uninterrupted session would have
offered — there is no position file that can disagree with the log. The
*two-formatter* test asserts the core returns structured data rather than
display strings, which is the property that decides whether a web adapter is a
new front end or a rewrite.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from comparative_judgment.core.errors import CutError, NothingToJudgeError
from comparative_judgment.core.findings import parse_findings
from comparative_judgment.core.models import (
    Cut,
    CutName,
    Outcome,
    PairForReview,
    Retraction,
)
from comparative_judgment.core.session import Session
from comparative_judgment.core.store import Store


def _fixed_clock() -> str:
    return "2026-08-28T12:00:00Z"


def _document(count: int) -> str:
    entries = "\n".join(
        textwrap.dedent(
            f"""
              - id: F-{i:02d}
                observation: Finding {i} did something wrong on the call.
                evidence:
                  - "line {i}: first fragment"
                  - "line {i + 40}: second fragment"
                consequence: Consequence number {i} for the person on the call.
                detectable_by: judge
                tier: defect
            """
        ).rstrip()
        for i in range(count)
    )
    return "findings:\n" + entries


@pytest.fixture
def session(tmp_path: Path) -> Session:
    store = Store.create(tmp_path / "store", clock=_fixed_clock)
    loaded = parse_findings(_document(6))
    store.put_findings(loaded.admitted)
    store.put_load_summary(loaded.excluded_questions)
    return Session(store, rater_id="rater-1", appearance_target=4)


class TestDerivedCursor:
    """There is no stored position; the next pair is derived from the log."""

    def test_two_calls_return_the_same_pair(self, session: Session) -> None:
        first, second = session.next_pair(), session.next_pair()
        assert first is not None and second is not None
        assert (first.left.id, first.right.id) == (second.left.id, second.right.id)

    def test_a_resumed_session_offers_the_same_pair(self, session: Session) -> None:
        """A restart is indistinguishable from never having stopped."""
        session.record(Outcome.LEFT)
        expected = session.next_pair()
        assert expected is not None

        resumed = Session(
            Store.open(session._store.path, clock=_fixed_clock),
            rater_id="rater-1",
            appearance_target=4,
        )
        actual = resumed.next_pair()
        assert actual is not None
        assert (actual.left.id, actual.right.id) == (expected.left.id, expected.right.id)

    def test_a_resumed_session_keeps_the_spent_count(self, session: Session) -> None:
        for _ in range(3):
            session.record(Outcome.LEFT)
        resumed = Session(
            Store.open(session._store.path, clock=_fixed_clock),
            rater_id="rater-1",
            appearance_target=4,
        )
        assert resumed.progress().comparisons_spent == 3


class TestRecording:
    def test_record_persists_before_returning(self, session: Session) -> None:
        """Read the file directly, without reopening: it must already be there.

        The caller advances on return, so a merely-buffered judgment is one a
        crash discards with the rater none the wiser.
        """
        recorded = session.record(Outcome.RIGHT)
        raw = (session._store.path / "comparisons.jsonl").read_text(encoding="utf-8")
        assert f'"seq": {recorded.seq}' in raw
        assert '"outcome": "right"' in raw

    def test_record_advances_to_a_new_pair(self, session: Session) -> None:
        before = session.next_pair()
        session.record(Outcome.LEFT)
        after = session.next_pair()
        assert before is not None and after is not None
        assert (before.left.id, before.right.id) != (after.left.id, after.right.id)

    def test_recording_a_tie_is_accepted(self, session: Session) -> None:
        session.record(Outcome.TIE)
        assert session.progress().ties == 1

    def test_recording_past_the_end_is_refused(self, tmp_path: Path) -> None:
        store = Store.create(tmp_path / "s", clock=_fixed_clock)
        store.put_findings(parse_findings(_document(2)).admitted)
        finished = Session(store, rater_id="r", appearance_target=0)
        with pytest.raises(NothingToJudgeError, match="nothing left"):
            finished.record(Outcome.LEFT)


class TestUndo:
    def test_undo_withdraws_the_latest_judgment(self, session: Session) -> None:
        session.record(Outcome.LEFT)
        session.record(Outcome.RIGHT)
        assert session.progress().comparisons_spent == 2

        retraction = session.undo()
        assert isinstance(retraction, Retraction)
        assert session.progress().comparisons_spent == 1

    def test_undo_leaves_the_record_in_the_log(self, session: Session) -> None:
        session.record(Outcome.LEFT)
        session.undo()
        assert len(session._store.log()) == 2  # the comparison and its retraction

    def test_undo_restores_the_previous_pair(self, session: Session) -> None:
        original = session.next_pair()
        session.record(Outcome.LEFT)
        session.undo()
        restored = session.next_pair()
        assert original is not None and restored is not None
        assert (original.left.id, original.right.id) == (restored.left.id, restored.right.id)

    def test_undo_with_nothing_recorded_returns_none(self, session: Session) -> None:
        assert session.undo() is None


class TestStructuredOutput:
    """The core returns data, not display text."""

    def test_the_same_pair_renders_through_two_different_formatters(self, session: Session) -> None:
        """If this needs changing to add a formatter, the seam has leaked.

        A terminal and a browser must both be able to render the same object
        without the core knowing which asked. Returning preformatted strings is
        the subtlest way to bake a front end into the core, and it would not fail
        any other test here.
        """
        pair = session.next_pair()
        assert pair is not None

        def as_plain_text(p: PairForReview) -> str:
            return f"{p.left.observation} | {p.right.observation}"

        def as_markup(p: PairForReview) -> str:
            fragments = "".join(f"<li>{e}</li>" for e in p.left.evidence)
            return f"<section><p>{p.left.observation}</p><ul>{fragments}</ul></section>"

        text, markup = as_plain_text(pair), as_markup(pair)

        # The earlier version of this test asserted `"|" in text` and
        # `markup.startswith("<section>")` -- both true of strings the test had
        # just built with those literals in them, and true no matter what the
        # session returned. It could not fail, so its docstring's claim was
        # carried entirely by the prose.
        #
        # What actually has to hold: neither rendering appears in what the
        # session handed over. If the core ever returned display text, one of
        # these formatters would be laying out the other's output.
        assert text not in pair.left.observation
        assert markup not in pair.left.observation
        assert "|" not in pair.left.observation
        assert "<" not in pair.left.observation

        # And the fields arrive as typed pieces, not as one preformatted blob.
        assert isinstance(pair.left.evidence, tuple)
        assert all(isinstance(fragment, str) for fragment in pair.left.evidence)
        assert "\n" not in pair.left.observation

        # The two renderings are genuinely different views of the same object.
        assert text != markup
        assert pair.left.observation in text
        assert pair.left.observation in markup

    def test_evidence_reaches_the_front_end_as_separate_fragments(self, session: Session) -> None:
        pair = session.next_pair()
        assert pair is not None
        assert len(pair.left.evidence) == 2

    def test_the_pair_carries_progress_for_display(self, session: Session) -> None:
        pair = session.next_pair()
        assert pair is not None
        assert pair.appearance_target == 4
        assert pair.comparisons_spent == 0


class TestProgress:
    def test_reports_the_measured_cost_figures(self, session: Session) -> None:
        """The cost model rests on estimates; this is what measures them (D8)."""
        for _ in range(5):
            session.record(Outcome.LEFT)
        progress = session.progress()
        assert progress.comparisons_spent == 5
        assert progress.mean_appearances == pytest.approx(10 / 6)
        assert session.mean_comparisons_per_item() == pytest.approx(5 / 6)

    def test_reports_excluded_questions(self, tmp_path: Path) -> None:
        store = Store.create(tmp_path / "s", clock=_fixed_clock)
        document = _document(2) + textwrap.dedent(
            """
              - id: Q-01
                observation: Is this intentional?
                evidence: ["line 3: x"]
                consequence: Unknown until the owning team answers.
                detectable_by: human
                tier: question
            """
        )
        loaded = parse_findings(document)
        store.put_findings(loaded.admitted)
        store.put_load_summary(loaded.excluded_questions)
        assert Session(store, rater_id="r").progress().excluded_questions == 1

    def test_completes_only_when_every_item_reaches_the_target(self, session: Session) -> None:
        assert not session.progress().complete
        for _ in range(200):
            if session.next_pair() is None:
                break
            session.record(Outcome.LEFT)
        progress = session.progress()
        assert progress.complete
        assert progress.items_below_target == ()
        assert progress.min_appearances >= 4


class TestPlacement:
    def test_banding_without_cuts_is_refused(self, session: Session) -> None:
        with pytest.raises(CutError, match="no cuts"):
            session.place()

    def test_bands_every_compared_finding(self, session: Session) -> None:
        for _ in range(200):
            if session.next_pair() is None:
                break
            session.record(Outcome.LEFT)
        ranked = [e.finding_id for e in session._fit().ranked()]
        session._store.put_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1]),
                Cut(CutName.HIGH_MEDIUM, ranked[2], ranked[3]),
                Cut(CutName.MEDIUM_LOW, ranked[4], ranked[5]),
            ]
        )
        placement = session.place()
        assert len(placement.assignments) == 6
        assert placement.unplaced == ()
        assert len(placement.thresholds) == 3


class TestPlacementMode:
    """Second batch onward: place against cuts, do not re-rank.

    This is the scaling claim made concrete. Ranking a new finding among n
    anchors costs about log2(n) comparisons; locating it against three cuts costs
    about three, and its exact rank was never used for anything.
    """

    def _bootstrapped(self, tmp_path: Path) -> Session:
        store = Store.create(tmp_path / "s", clock=_fixed_clock)
        loaded = parse_findings(_document(6))
        store.put_findings(loaded.admitted)
        store.put_load_summary(loaded.excluded_questions)
        session = Session(store, rater_id="r", appearance_target=4)
        while session.next_pair() is not None:
            session.record(Outcome.LEFT)
        ranked = [e.finding_id for e in session._fit().ranked()]
        store.put_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[1]),
                Cut(CutName.HIGH_MEDIUM, ranked[2], ranked[3]),
                Cut(CutName.MEDIUM_LOW, ranked[4], ranked[5]),
            ]
        )
        return session

    def _add_newcomer(self, session: Session, finding_id: str = "F-NEW") -> None:
        """Put one unjudged finding into a store that already has cuts."""
        existing = list(session._store.findings())
        newcomer = parse_findings(
            "findings:\n"
            f"  - id: {finding_id}\n"
            "    observation: A newly reviewed call had the same problem.\n"
            "    evidence: ['line 7: fragment']\n"
            "    consequence: The caller is out of pocket.\n"
            "    detectable_by: judge\n"
            "    tier: defect"
        ).admitted
        session._store.put_findings([*existing, *newcomer])
        session._invalidate()

    def test_a_bootstrapped_batch_needs_no_further_comparisons(self, tmp_path: Path) -> None:
        session = self._bootstrapped(tmp_path)
        assert session.next_pair() is None

    def test_a_new_finding_is_placed_in_at_most_four_comparisons(self, tmp_path: Path) -> None:
        session = self._bootstrapped(tmp_path)
        before = session.progress().comparisons_spent

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

        spent = 0
        while (pair := session.next_pair()) is not None and spent < 20:
            assert "F-NEW" in (pair.left.id, pair.right.id)
            session.record(Outcome.RIGHT)
            spent += 1

        assert spent <= 4, f"placement should cost about three comparisons, took {spent}"
        assert session.progress().comparisons_spent == before + spent

    def test_a_finished_placement_reports_complete(self, tmp_path: Path) -> None:
        """C-4. `complete` was measured against the target placement replaced.

        Once cuts exist the loop places a newcomer in about three comparisons
        and stops, so the appearance target is no longer what finishing means.
        It was still what `complete` was computed from -- so a newcomer that
        `next_pair()` had nothing left to offer for, and that `bands` had
        already placed, was reported as an unfinished batch.

        The last assertion is the one that makes this a test of the *mode*
        rather than of an empty list: the appearance-target figure is still
        true and still populated. What changed is which of the two the
        finishing condition reads.
        """
        session = self._bootstrapped(tmp_path)
        self._add_newcomer(session)
        while session.next_pair() is not None:
            session.record(Outcome.RIGHT)

        progress = session.progress()
        assert progress.placing
        assert progress.items_unplaced == ()
        assert progress.complete, "nothing is left to judge, so this is not an unfinished batch"
        assert "F-NEW" in progress.items_below_target, (
            "the appearance-target figure should still name the placed item; if it does "
            "not, this test passes because the two criteria agree rather than because "
            "the right one is being read"
        )

    def test_an_unfinished_placement_is_not_reported_as_complete(self, tmp_path: Path) -> None:
        """The control, because `complete = True` would satisfy the test above.

        A newcomer short of the placement quota must be named and must keep the
        batch incomplete. Without this, the repair could have replaced a check
        that was wrong in one direction with one wrong in the other, and the
        suite would not have noticed.
        """
        session = self._bootstrapped(tmp_path)
        self._add_newcomer(session)
        session.record(Outcome.RIGHT)

        progress = session.progress()
        assert progress.placing
        assert progress.items_unplaced == ("F-NEW",)
        assert not progress.complete, "a newcomer short of its quota is not a finished batch"
        assert session.next_pair() is not None, (
            "the placement loop and the completion criterion disagree, which is the "
            "class of defect this pair exists to prevent"
        )

    def test_the_new_finding_receives_a_band(self, tmp_path: Path) -> None:
        session = self._bootstrapped(tmp_path)
        existing = list(session._store.findings())
        newcomer = parse_findings(
            "findings:\n"
            "  - id: F-NEW\n"
            "    observation: Another call, same failure.\n"
            "    evidence: ['line 7: fragment']\n"
            "    consequence: Money that will not arrive.\n"
            "    detectable_by: judge\n"
            "    tier: defect"
        ).admitted
        session._store.put_findings([*existing, *newcomer])
        session._invalidate()
        while session.next_pair() is not None:
            session.record(Outcome.LEFT)

        placed = {a.finding_id for a in session.place().assignments}
        assert "F-NEW" in placed

    def test_a_cut_spanning_a_finding_reports_it_in_placement_and_progress(
        self, tmp_path: Path
    ) -> None:
        """D34 through the session: a boundary drawn across a finding names it, refusing nothing.

        The critical_high cut is drawn from the first-ranked finding to the third,
        so the second lies between its anchors on this fit. Compared with
        `separation` over the placement's own assignments rather than with a
        restatement of the rule, and progress must carry the same report.
        """
        from comparative_judgment.core.bands import separation

        session = self._bootstrapped(tmp_path)
        ranked = [e.finding_id for e in session._fit().ranked()]
        session._store.put_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[0], ranked[2]),
                Cut(CutName.HIGH_MEDIUM, ranked[3], ranked[4]),
                Cut(CutName.MEDIUM_LOW, ranked[4], ranked[5]),
            ]
        )
        session._invalidate()

        placement = session.place()
        assert placement.separation == separation(session.cuts(), placement.assignments)
        assert ranked[1] in placement.separation[0].between, (
            "the finding ranked between the critical_high anchors is not reported between them"
        )
        assert session.progress().separation == placement.separation


class TestRemainingAndTheSittingClock:
    """D40: an estimate of comparisons remaining beside those spent, and the sitting's time."""

    def test_the_bootstrap_estimate_is_a_lower_bound_that_holds_at_every_step(
        self, session: Session
    ) -> None:
        estimates: list[int] = []
        while session.next_pair() is not None:
            progress = session.progress()
            assert progress.remaining_is_lower_bound
            assert progress.comparisons_remaining is not None
            estimates.append(progress.comparisons_remaining)
            session.record(Outcome.LEFT)
        spent = len(estimates)
        assert estimates[0] == 12, "six findings owing four appearances each, two per comparison"
        for step, estimate in enumerate(estimates):
            assert estimate <= spent - step, (step, estimate, spent)
        assert session.progress().comparisons_remaining == 0

    def test_the_placement_estimate_is_exact(self, tmp_path: Path) -> None:
        placing = TestPlacementMode()
        session = placing._bootstrapped(tmp_path)
        placing._add_newcomer(session, "F-NEW")
        placing._add_newcomer(session, "F-NEWER")
        seen: list[int | None] = []
        while session.next_pair() is not None:
            progress = session.progress()
            assert not progress.remaining_is_lower_bound
            seen.append(progress.comparisons_remaining)
            session.record(Outcome.LEFT)
        assert seen == list(range(len(seen), 0, -1)) and len(seen) == 6
        assert session.progress().comparisons_remaining == 0

    def test_a_blocked_batch_has_no_estimate(self, tmp_path: Path) -> None:
        session = TestPlacementMode()._bootstrapped(tmp_path)
        ranked = [e.finding_id for e in session._fit().ranked()]
        session._store.put_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, ranked[1], ranked[0]),
                Cut(CutName.HIGH_MEDIUM, ranked[2], ranked[3]),
                Cut(CutName.MEDIUM_LOW, ranked[4], ranked[5]),
            ]
        )
        progress = session.progress()
        assert progress.blocked_reason and progress.comparisons_remaining is None

    def test_fewer_than_two_findings_leave_nothing_to_compare(self, tmp_path: Path) -> None:
        store = Store.create(tmp_path / "one", clock=_fixed_clock)
        store.put_findings(parse_findings(_document(1)).admitted)
        assert Session(store, rater_id="r").progress().comparisons_remaining == 0

    def test_the_clock_measures_this_sitting_and_writes_nothing(self, tmp_path: Path) -> None:
        ticks = iter([100.0, 100.0, 175.5, 175.5])
        store = Store.create(tmp_path / "s", clock=_fixed_clock)
        store.put_findings(parse_findings(_document(4)).admitted)
        session = Session(store, rater_id="r", monotonic=lambda: next(ticks))
        before = session.progress()
        assert session.elapsed_seconds() == 0.0
        assert session.elapsed_seconds() == 75.5
        assert session.progress() == before, "progress must stay a function of the store"
        assert store.log() == ()
