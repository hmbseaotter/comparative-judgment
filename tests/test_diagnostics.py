"""Standard errors, misfit and soft regions, tested on inputs whose answers are known.

The build prompt asks for the statistics to be proven here before any front end
shows them, because each can return plausible numbers while being quietly wrong.
So the standard errors are checked against a numerical Hessian of the objective
the fit maximizes rather than against themselves, and misfit is checked on the
shapes that decide whether it can see an intransitive rater at all: the triad on
its own, its consistent counterpart, and the triad planted in an otherwise
consistent scale.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import numpy as np
import pytest

from comparative_judgment.core.errors import OutputWriteError
from comparative_judgment.core.findings import parse_findings
from comparative_judgment.core.fit import LAMBDA, FitResult, fit
from comparative_judgment.core.graph import components
from comparative_judgment.core.models import (
    CUT_ORDER,
    Comparison,
    Cut,
    CutName,
    Outcome,
    SoftRegion,
)
from comparative_judgment.core.session import Session
from comparative_judgment.core.stats import (
    REGION_COMPARISONS,
    ItemFit,
    covariance,
    information,
    item_misfit,
    soft_regions,
    standard_errors,
)
from comparative_judgment.core.store import Store


def _beats(seq: int, winner: str, loser: str) -> Comparison:
    return Comparison(seq, winner, loser, Outcome.LEFT, "r", "s", "2026-09-18T12:00:00Z")


def _tie(seq: int, left: str, right: str) -> Comparison:
    return Comparison(seq, left, right, Outcome.TIE, "r", "s", "2026-09-18T12:00:00Z")


def _penalized_log_likelihood(theta: dict[str, float], comparisons: list[Comparison]) -> float:
    """The objective the fit maximizes, written out independently of the code under test."""

    def log_sigmoid(value: float) -> float:
        return -math.log1p(math.exp(-value)) if value >= 0 else value - math.log1p(math.exp(value))

    total = 0.0
    for comparison in comparisons:
        pair = comparison.winner_loser()
        if pair is not None:
            total += log_sigmoid(theta[pair[0]] - theta[pair[1]])
    for value in theta.values():
        total += LAMBDA * log_sigmoid(value) + LAMBDA * log_sigmoid(-value)
    return total


def _known(item: ItemFit) -> tuple[float, float]:
    """Infit and outfit, for a finding the test knows has decided comparisons."""
    assert item.infit is not None and item.outfit is not None
    return item.infit, item.outfit


def _chain(size: int, reach: int = 2, prefix: str = "I") -> tuple[list[str], list[Comparison]]:
    """A consistent scale: each finding beats the next `reach` below it."""
    ids = [f"{prefix}{i:02d}" for i in range(size)]
    out: list[Comparison] = []
    for i in range(size):
        for step in range(1, reach + 1):
            if i + step < size:
                out.append(_beats(len(out) + 1, ids[i], ids[i + step]))
    return ids, out


def _with_triad(comparisons: list[Comparison], a: str, c: str) -> list[Comparison]:
    """Reverse the one comparison `a` beat `c` in, closing a > b > c > a."""
    return [
        _beats(x.seq, x.right_id, x.left_id) if (x.left_id, x.right_id) == (a, c) else x
        for x in comparisons
    ]


class TestStandardErrors:
    """D39: from the regularized information, which is the fit's own objective's."""

    @pytest.fixture
    def scale(self) -> tuple[FitResult, list[Comparison]]:
        ids, comparisons = _chain(6)
        comparisons = [*comparisons, _tie(99, "I01", "I04"), _beats(100, "I05", "I02")]
        return fit(ids, comparisons), comparisons

    def test_the_fit_sits_at_the_maximum_of_the_objective_the_information_describes(
        self, scale: tuple[FitResult, list[Comparison]]
    ) -> None:
        """A zero gradient here is what licenses reading the Hessian at this point."""
        result, comparisons = scale
        theta = result.theta()
        step = 1e-6
        for item in theta:
            up = {**theta, item: theta[item] + step}
            down = {**theta, item: theta[item] - step}
            gradient = (
                _penalized_log_likelihood(up, comparisons)
                - _penalized_log_likelihood(down, comparisons)
            ) / (2 * step)
            assert abs(gradient) < 1e-6, (item, gradient)

    def test_the_information_is_the_numerical_negative_hessian(
        self, scale: tuple[FitResult, list[Comparison]]
    ) -> None:
        result, comparisons = scale
        theta = result.theta()
        ids = [e.finding_id for e in result.estimates]
        step = 1e-4
        numeric = np.zeros((len(ids), len(ids)))
        for i, a in enumerate(ids):
            for j, b in enumerate(ids):

                def at(da: float, db: float, a: str = a, b: str = b) -> float:
                    shifted = dict(theta)
                    shifted[a] += da
                    shifted[b] += db
                    return _penalized_log_likelihood(shifted, comparisons)

                numeric[i, j] = -(
                    at(step, step) - at(step, -step) - at(-step, step) + at(-step, -step)
                ) / (4 * step * step)
        assert np.allclose(information(result, comparisons), numeric, atol=1e-5)

    def test_an_unjudged_finding_has_exactly_the_priors_standard_error(self) -> None:
        """Information 2 * 0.5 * 0.25 at the origin, so 2.0 exactly: the prior, saying so."""
        ids, comparisons = _chain(4)
        result = fit([*ids, "ZZ"], comparisons)
        assert standard_errors(result, comparisons)["ZZ"] == 2.0

    def test_a_finding_with_only_ties_has_the_priors_standard_error_too(self) -> None:
        ids, comparisons = _chain(4)
        comparisons = [*comparisons, _tie(50, "ZZ", "I01")]
        result = fit([*ids, "ZZ"], comparisons)
        assert standard_errors(result, comparisons)["ZZ"] == 2.0

    def test_a_finding_that_wins_everything_gets_a_finite_standard_error(self) -> None:
        ids = ["W", "X1", "X2", "X3"]
        comparisons = [_beats(i, "W", x) for i, x in enumerate(ids[1:], start=1)]
        errors = standard_errors(fit(ids, comparisons), comparisons)
        assert math.isfinite(errors["W"]) and 0 < errors["W"] < 2.0

    def test_more_comparisons_narrow_the_standard_error(self) -> None:
        ids, few = _chain(6, reach=1)
        _, many = _chain(6, reach=3)
        assert (
            standard_errors(fit(ids, many), many)["I03"]
            < (standard_errors(fit(ids, few), few)["I03"])
        )

    def test_the_covariance_is_symmetric_with_a_positive_diagonal(
        self, scale: tuple[FitResult, list[Comparison]]
    ) -> None:
        result, comparisons = scale
        matrix = covariance(result, comparisons)
        assert np.allclose(matrix, matrix.T)
        assert (np.diagonal(matrix) > 0).all()

    def test_no_findings_means_no_standard_errors(self) -> None:
        assert standard_errors(fit([], []), []) == {}

    def test_standard_errors_are_byte_identical_across_two_fits(
        self, scale: tuple[FitResult, list[Comparison]]
    ) -> None:
        """Across two runs on one machine: a linear-algebra call can differ between BLAS builds."""
        _, comparisons = scale
        ids = sorted({c.left_id for c in comparisons} | {c.right_id for c in comparisons})
        first = standard_errors(fit(ids, comparisons), comparisons)
        second = standard_errors(fit(ids, comparisons), comparisons)
        assert {k: v.hex() for k, v in first.items()} == {k: v.hex() for k, v in second.items()}

    def test_the_order_judgments_were_recorded_in_does_not_move_a_bit(
        self, scale: tuple[FitResult, list[Comparison]]
    ) -> None:
        _, comparisons = scale
        ids = sorted({c.left_id for c in comparisons} | {c.right_id for c in comparisons})
        shuffled = list(comparisons)
        random.Random(3).shuffle(shuffled)
        first = standard_errors(fit(ids, comparisons), comparisons)
        second = standard_errors(fit(ids, shuffled), shuffled)
        assert {k: v.hex() for k, v in first.items()} == {k: v.hex() for k, v in second.items()}


class TestMisfit:
    """D37, and D2's promise: an intransitive rater is visible as misfit."""

    def test_a_triad_on_its_own_reads_as_the_models_expectation(self) -> None:
        """Three equal findings and three coin flips: the model's best account, fitting exactly."""
        ids = ["A", "B", "C"]
        triad = [_beats(1, "A", "B"), _beats(2, "B", "C"), _beats(3, "C", "A")]
        fits = item_misfit(fit(ids, triad), triad)
        for item in ids:
            assert fits[item].infit == pytest.approx(1.0)
            assert fits[item].outfit == pytest.approx(1.0)

    def test_the_triad_raises_misfit_over_the_same_findings_judged_consistently(self) -> None:
        """The acceptance criterion's control: one outcome flipped, everything else equal."""
        ids = ["A", "B", "C"]
        triad = [_beats(1, "A", "B"), _beats(2, "B", "C"), _beats(3, "C", "A")]
        consistent = [_beats(1, "A", "B"), _beats(2, "B", "C"), _beats(3, "A", "C")]
        cyclic = item_misfit(fit(ids, triad), triad)
        ordered = item_misfit(fit(ids, consistent), consistent)
        for item in ids:
            cyclic_infit, cyclic_outfit = _known(cyclic[item])
            ordered_infit, ordered_outfit = _known(ordered[item])
            assert cyclic_infit > ordered_infit
            assert cyclic_outfit > ordered_outfit
            assert ordered_infit < 0.5, "consistent sparse data should read well below 1"

    def test_a_triad_planted_in_a_consistent_scale_raises_its_findings_to_the_top(self) -> None:
        ids, consistent = _chain(10)
        planted = _with_triad(consistent, "I03", "I05")
        before = item_misfit(fit(ids, consistent), consistent)
        after = item_misfit(fit(ids, planted), planted)
        triad = {"I03", "I04", "I05"}
        for item in triad:
            infit_before, outfit_before = _known(before[item])
            infit_after, outfit_after = _known(after[item])
            assert infit_after > infit_before
            assert outfit_after > outfit_before
        assert set(sorted(ids, key=lambda item: -_known(after[item])[0])[:3]) == triad

    def test_ties_change_neither_statistic(self) -> None:
        """Excluded from the fit, so excluded from its residuals; the tie rate reports them."""
        ids, comparisons = _chain(6)
        with_ties = [*comparisons, _tie(90, "I01", "I02"), _tie(91, "I02", "I03")]
        assert item_misfit(fit(ids, comparisons), comparisons) == item_misfit(
            fit(ids, with_ties), with_ties
        )

    def test_a_finding_with_no_decided_comparison_has_no_misfit(self) -> None:
        ids, comparisons = _chain(4)
        comparisons = [*comparisons, _tie(9, "ZZ", "I00")]
        fits = item_misfit(fit([*ids, "ZZ"], comparisons), comparisons)
        assert fits["ZZ"].infit is None and fits["ZZ"].outfit is None

    def test_a_comparison_naming_a_finding_the_fit_does_not_hold_is_skipped(self) -> None:
        """An accepted removal leaves comparisons behind; the fit skips them, and so does this."""
        ids, comparisons = _chain(4)
        orphaned = [*comparisons, _beats(9, "GONE", "I00")]
        result = fit(ids, orphaned)
        assert item_misfit(result, orphaned) == item_misfit(fit(ids, comparisons), comparisons)
        assert standard_errors(result, orphaned) == standard_errors(
            fit(ids, comparisons), comparisons
        )


class TestSoftRegions:
    """D38: equal-evidence windows along the scale, within each component, ranked by infit."""

    def test_every_decided_comparison_lands_in_exactly_one_region(self) -> None:
        ids, comparisons = _chain(40)
        result = fit(ids, comparisons)
        regions = soft_regions(result, comparisons, components(ids, comparisons))
        assert sum(r.comparisons for r in regions) == len(comparisons)
        assert all(REGION_COMPARISONS <= r.comparisons < 2 * REGION_COMPARISONS for r in regions)
        assert [r.rank for r in regions] == list(range(1, len(regions) + 1))

    def test_a_component_with_too_few_comparisons_for_two_regions_is_one(self) -> None:
        ids, comparisons = _chain(5)
        regions = soft_regions(fit(ids, comparisons), comparisons, components(ids, comparisons))
        assert len(regions) == 1
        assert regions[0].comparisons == len(comparisons) < REGION_COMPARISONS

    def test_no_decided_comparison_means_no_region(self) -> None:
        ties = [_tie(1, "A", "B")]
        assert soft_regions(fit(["A", "B"], ties), ties, components(["A", "B"], ties)) == ()

    def test_a_stretch_judged_inconsistently_ranks_its_region_first(self) -> None:
        """The acceptance criterion: the report names *where* the scale is soft.

        Random opponents rather than the chain the other tests use. A chain in
        which each finding beats the next two gives every mid-scale finding two
        wins and two losses, and Bradley-Terry sees a finding only through its
        wins against its opponents' positions -- so the chain's middle fits level
        at the exact optimum, its order carried only at the ends, and a region's
        infit there reads position rather than consistency. Random opponents carry
        order along the whole scale, so what ranks a region first is the planted
        inconsistency.
        """
        rng = random.Random(0)
        ids = [f"I{i:02d}" for i in range(40)]
        consistent: list[Comparison] = []
        for i in range(40):
            for j in rng.sample([k for k in range(40) if k != i], 2):
                winner, loser = min(i, j), max(i, j)
                consistent.append(_beats(len(consistent) + 1, ids[winner], ids[loser]))
        stretch = [(30, 32), (31, 33), (32, 34), (33, 35), (30, 34)]
        planted = [
            *consistent,
            *(_beats(900 + k, ids[low], ids[high]) for k, (high, low) in enumerate(stretch)),
        ]
        soft = {ids[i] for pair in stretch for i in pair}

        def holding(regions: tuple[SoftRegion, ...], item: str) -> SoftRegion:
            return next(r for r in regions if item in r.findings)

        before = soft_regions(fit(ids, consistent), consistent, components(ids, consistent))
        after = soft_regions(fit(ids, planted), planted, components(ids, planted))
        assert len(after) >= 3
        assert len(soft & set(after[0].findings)) >= 5, after[0].findings
        assert after[0].infit > holding(before, "I32").infit
        assert after[0].infit > after[1].infit

    def test_regions_never_span_two_components(self) -> None:
        left_ids, left = _chain(25, prefix="L")
        right_ids, right = _chain(25, prefix="R")
        right = [_beats(c.seq + 1000, c.left_id, c.right_id) for c in right]
        ids, comparisons = [*left_ids, *right_ids], [*left, *right]
        groups = components(ids, comparisons)
        regions = soft_regions(fit(ids, comparisons), comparisons, groups)
        assert {r.component for r in regions} == {1, 2}
        for region in regions:
            members = set(groups[region.component - 1])
            assert set(region.findings) <= members

    def test_ties_and_cuts_are_placed_in_the_region_holding_them(self) -> None:
        ids, comparisons = _chain(40)
        comparisons = [*comparisons, _tie(500, "I01", "I02"), _tie(501, "I37", "I38")]
        result = fit(ids, comparisons)
        theta = result.theta()
        cut = (CutName.HIGH_MEDIUM, "I19", "I20", (theta["I19"] + theta["I20"]) / 2)
        regions = soft_regions(result, comparisons, components(ids, comparisons), (cut,))
        assert sum(r.ties for r in regions) == 2
        assert sum(len(r.cuts) for r in regions) == 1
        # A region's stretch runs from its first location up to the next region's.
        by_location = sorted(regions, key=lambda r: r.low)
        expected = max(
            (r for r in by_location if r.low <= cut[3]), key=lambda r: r.low, default=by_location[0]
        )
        assert expected.cuts == (CutName.HIGH_MEDIUM,)
        for region in regions:
            assert region.tie_rate == region.ties / (region.comparisons + region.ties)

    def test_a_tie_across_two_components_enters_no_region(self) -> None:
        left_ids, left = _chain(5, prefix="L")
        right_ids, right = _chain(5, prefix="R")
        comparisons = [*left, *right, _tie(99, "L00", "R00")]
        ids = [*left_ids, *right_ids]
        regions = soft_regions(fit(ids, comparisons), comparisons, components(ids, comparisons))
        assert sum(r.ties for r in regions) == 0

    def test_regions_are_identical_over_a_reordered_log(self) -> None:
        ids, comparisons = _chain(40)
        shuffled = list(comparisons)
        random.Random(5).shuffle(shuffled)
        first = soft_regions(fit(ids, comparisons), comparisons, components(ids, comparisons))
        second = soft_regions(fit(ids, shuffled), shuffled, components(ids, shuffled))
        assert first == second


def _fixed_clock() -> str:
    return "2026-09-18T12:00:00Z"


def _findings_document(ids: list[str]) -> str:
    return "findings:\n" + "".join(
        f"  - id: {i}\n"
        f"    observation: Observation for {i}.\n"
        f"    evidence: ['line 1: fragment for {i}']\n"
        f"    consequence: Consequence for {i}.\n"
        "    detectable_by: judge\n"
        "    tier: defect\n"
        for i in ids
    )


def _session_over(tmp_path: Path, ids: list[str], comparisons: list[Comparison]) -> Session:
    """A store holding `ids` and exactly `comparisons`, judged in the order given."""
    store = Store.create(tmp_path / "store", clock=_fixed_clock)
    store.put_findings(parse_findings(_findings_document(ids)).admitted)
    for c in comparisons:
        store.append_comparison(
            left_id=c.left_id,
            right_id=c.right_id,
            outcome=c.outcome,
            rater_id=c.rater_id,
            session_id=c.session_id,
        )
    return Session(store, rater_id="r")


class TestTheReport:
    """The acceptance criteria at the seam: what a front end is handed."""

    def test_it_names_standard_errors_misfit_and_the_tie_rate(self, tmp_path: Path) -> None:
        ids, comparisons = _chain(8)
        comparisons = [*comparisons, _tie(90, "I02", "I03")]
        report = _session_over(tmp_path, [*ids, "ZZ"], comparisons).diagnostics()
        by_id = {item.finding_id: item for item in report.items}
        assert set(by_id) == {*ids, "ZZ"}
        assert by_id["ZZ"].se == 2.0 and by_id["ZZ"].infit is None and by_id["ZZ"].tie_rate is None
        assert all(by_id[i].infit is not None and by_id[i].outfit is not None for i in ids)
        assert by_id["I02"].ties == 1 and by_id["I02"].tie_rate == pytest.approx(1 / 5)
        assert report.ties == 1
        assert report.tie_rate == pytest.approx(1 / len(comparisons))
        assert report.regions and report.regions[0].rank == 1
        assert report.regularization == LAMBDA

    def test_the_triad_raises_its_findings_misfit_in_the_report(self, tmp_path: Path) -> None:
        ids, consistent = _chain(10)
        planted = _with_triad(consistent, "I03", "I05")
        before = _session_over(tmp_path / "a", ids, consistent).diagnostics()
        after = _session_over(tmp_path / "b", ids, planted).diagnostics()
        earlier = {i.finding_id: i.infit for i in before.items}
        for item in after.items:
            if item.finding_id in {"I03", "I04", "I05"}:
                was, now = earlier[item.finding_id], item.infit
                assert was is not None and now is not None
                assert now > was

    def test_the_file_is_byte_identical_across_two_writes_and_asks_for_lf(
        self, tmp_path: Path
    ) -> None:
        ids, comparisons = _chain(8)
        session = _session_over(tmp_path, ids, comparisons)
        first, second = tmp_path / "one.json", tmp_path / "two.json"
        session.write_diagnostics(first)
        Session(Store.open(session.store_path), rater_id="r").write_diagnostics(second)
        assert first.read_bytes() == second.read_bytes()
        assert b"\r" not in first.read_bytes()
        payload = json.loads(first.read_text(encoding="utf-8"))
        assert payload["diagnostics_schema_version"] == "1"
        assert payload["comparison_log_hash"] == session._store.log_hash()
        assert {item["id"] for item in payload["items"]} == set(ids)

    def test_an_unwritable_path_is_a_named_refusal(self, tmp_path: Path) -> None:
        ids, comparisons = _chain(4)
        session = _session_over(tmp_path, ids, comparisons)
        with pytest.raises(OutputWriteError):
            session.write_diagnostics(tmp_path / "missing" / "report.json")

    def test_a_disconnected_graph_is_reported_and_not_refused(self, tmp_path: Path) -> None:
        left_ids, left = _chain(4, prefix="L")
        right_ids, right = _chain(4, prefix="R")
        report = _session_over(tmp_path, [*left_ids, *right_ids], [*left, *right]).diagnostics()
        assert len(report.components) == 2
        assert {r.component for r in report.regions} == {1, 2}
        assert all(c.imported == 0 and c.local == 4 for c in report.components)

    def test_an_inverted_cut_is_named_and_labels_no_region(self, tmp_path: Path) -> None:
        ids, comparisons = _chain(8)
        session = _session_over(tmp_path, ids, comparisons)
        session._store.put_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, "I01", "I00", calibration_note="note"),
                Cut(CutName.HIGH_MEDIUM, "I03", "I04"),
                Cut(CutName.MEDIUM_LOW, "I06", "I07"),
            ]
        )
        report = session.diagnostics()
        assert "inverted" in report.blocked_reason
        assert report.cuts == ()
        assert all(r.cuts == () for r in report.regions)

    def test_valid_cuts_are_reported_with_their_thresholds(self, tmp_path: Path) -> None:
        ids, comparisons = _chain(8)
        session = _session_over(tmp_path, ids, comparisons)
        session._store.put_cuts(
            [
                Cut(CutName.CRITICAL_HIGH, "I00", "I01", calibration_note="note"),
                Cut(CutName.HIGH_MEDIUM, "I03", "I04"),
                Cut(CutName.MEDIUM_LOW, "I06", "I07"),
            ]
        )
        report = session.diagnostics()
        assert [c.name for c in report.cuts] == list(CUT_ORDER)
        theta = {i.finding_id: i.theta for i in report.items}
        assert report.cuts[0].threshold == (theta["I00"] + theta["I01"]) / 2
        assert sum(len(r.cuts) for r in report.regions) == 3
