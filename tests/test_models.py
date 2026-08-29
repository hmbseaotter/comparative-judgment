"""Contract tests.

These assert properties the rest of the package relies on without re-checking:
that records cannot be mutated after construction, that identity tracks the text
actually judged, and that the band/cut orderings stay aligned.
"""

from __future__ import annotations

import dataclasses

import pytest

from comparative_judgment.core.models import (
    BANDS,
    CUT_ORDER,
    Comparison,
    Cut,
    CutName,
    DetectableBy,
    Finding,
    Outcome,
    Tier,
    content_hash,
)


def _finding(fid: str = "F-1", observation: str = "obs", consequence: str = "cons") -> Finding:
    evidence = ("line 12: a", "line 40: b")
    return Finding(
        id=fid,
        content_hash=content_hash(
            observation=observation, evidence=evidence, consequence=consequence
        ),
        observation=observation,
        evidence=evidence,
        consequence=consequence,
        detectable_by=DetectableBy.JUDGE,
        tier=Tier.DEFECT,
    )


class TestImmutability:
    """Records are frozen. A mutable record can disagree with its stored form."""

    @pytest.mark.parametrize(
        ("obj", "attr", "value"),
        [
            (_finding(), "observation", "changed"),
            (
                Comparison(1, "a", "b", Outcome.LEFT, "r", "s", "2026-08-28T00:00:00Z"),
                "outcome",
                Outcome.RIGHT,
            ),
            (Cut(CutName.HIGH_MEDIUM, "a", "b"), "above_id", "z"),
        ],
    )
    def test_records_reject_mutation(self, obj: object, attr: str, value: object) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, attr, value)


class TestContentHash:
    """Identity is an id *at a particular text*.

    The hash is what lets a load notice the text moved and refuse until a human
    accepts it (D18). It is not itself a fork: forking automatically would orphan
    every judgment about a finding whose spelling someone corrected.
    """

    def test_same_text_same_hash(self) -> None:
        assert _finding().content_hash == _finding().content_hash

    def test_edited_observation_changes_hash(self) -> None:
        assert _finding(observation="a").content_hash != _finding(observation="b").content_hash

    def test_edited_consequence_changes_hash(self) -> None:
        assert _finding(consequence="a").content_hash != _finding(consequence="b").content_hash

    def test_evidence_fragment_boundaries_do_not_collide(self) -> None:
        """['ab', 'c'] must not hash the same as ['a', 'bc'].

        Naive concatenation would collide, and the schema requires fragments from
        different points in a call to stay visibly separate — a collision would
        erase exactly that distinction.
        """
        left = content_hash(observation="o", evidence=("ab", "c"), consequence="k")
        right = content_hash(observation="o", evidence=("a", "bc"), consequence="k")
        assert left != right

    def test_surrounding_whitespace_is_not_identity(self) -> None:
        bare = content_hash(observation="o", evidence=("e",), consequence="k")
        padded = content_hash(observation="  o \n", evidence=("  e ",), consequence=" k ")
        assert bare == padded


class TestOutcomeSemantics:
    """A tie is a decision, not a missing answer."""

    def test_left_wins(self) -> None:
        c = Comparison(1, "a", "b", Outcome.LEFT, "r", "s", "t")
        assert c.winner_loser() == ("a", "b")

    def test_right_wins(self) -> None:
        c = Comparison(1, "a", "b", Outcome.RIGHT, "r", "s", "t")
        assert c.winner_loser() == ("b", "a")

    def test_tie_has_no_winner(self) -> None:
        c = Comparison(1, "a", "b", Outcome.TIE, "r", "s", "t")
        assert c.winner_loser() is None


class TestBandCutAlignment:
    """Placement walks CUT_ORDER and indexes BANDS; the two must stay in step."""

    def test_one_more_band_than_cut(self) -> None:
        assert len(BANDS) == len(CUT_ORDER) + 1

    def test_orderings_are_unique(self) -> None:
        assert len(set(BANDS)) == len(BANDS)
        assert len(set(CUT_ORDER)) == len(CUT_ORDER)

    def test_every_cut_name_is_used(self) -> None:
        assert set(CUT_ORDER) == set(CutName)


class TestTierSemantics:
    """Question entries are non-defects; nothing may quietly treat them as rateable."""

    def test_tiers_are_exactly_defect_and_question(self) -> None:
        assert {t.value for t in Tier} == {"defect", "question"}
