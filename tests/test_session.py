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

from comparative_judgment.core.errors import CutError
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
        with pytest.raises(ValueError, match="nothing left"):
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
        assert "|" in text
        assert markup.startswith("<section>") and "<li>" in markup

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
