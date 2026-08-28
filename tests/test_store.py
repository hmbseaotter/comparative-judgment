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

from comparative_judgment.core.errors import (
    NoStorePathError,
    StoreSchemaError,
    UnknownItemError,
)
from comparative_judgment.core.models import (
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
from comparative_judgment.core.store import LOG_FILE, META_FILE, Store


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
    """An unrecognised layout refuses to write rather than guessing."""

    def test_unknown_schema_version_names_the_mismatch(self, tmp_path: Path) -> None:
        Store.create(tmp_path / "s", clock=_fixed_clock)
        meta_path = tmp_path / "s" / META_FILE
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["schema_version"] = "999"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        with pytest.raises(StoreSchemaError, match="999"):
            Store.open(tmp_path / "s")


class TestMalformedStore:
    """A corrupt store is a named refusal, not a KeyError three frames deeper.

    The store is append-only and shared across sessions, so the failure a reader
    sees should say which file and which field gave up.
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
        further up: if the store's own serialisation wandered, no downstream
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
