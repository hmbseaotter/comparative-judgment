"""Findings-reader tests.

The two that matter most are the tier exclusion and the severity refusal: both
protect properties that fail silently rather than loudly if they regress. A
rated question row corrupts the anchor set with no visible symptom, and a
severity field in the findings document gives the value two sources of truth
that only disagree later.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from comparative_judgment.core.errors import FindingSchemaError
from comparative_judgment.core.findings import load_findings, parse_findings
from comparative_judgment.core.models import REQUIRED_FINDING_KEYS, DetectableBy, Tier


def _doc(body: str) -> str:
    return textwrap.dedent(body).lstrip()


VALID = _doc(
    """
    findings:
      - id: F-001
        call_ref: CALL-03
        observation: The agent announced a completed exchange before the tool ran.
        evidence:
          - "line 42: I've gone ahead and swapped those seats for you."
          - "line 51: exchange_seats -> ERROR ineligible_after_doors"
        consequence: The caller left believing they had different seats.
        detectable_by: assert
        tier: defect
      - id: F-002
        observation: The refund window was stated without any policy lookup.
        evidence:
          - "line 18: that's a fourteen day window"
        consequence: A policy claim nothing in the log can support.
        detectable_by: judge
        tier: defect
    """
)


class TestHappyPath:
    def test_admits_defects(self) -> None:
        result = parse_findings(VALID)
        assert [f.id for f in result.admitted] == ["F-001", "F-002"]

    def test_evidence_fragments_stay_separate(self) -> None:
        """Two fragments in, two out.

        This is the property that decided the format. If it regressed, the
        choice of YAML over a Markdown table would have been given away for
        nothing.
        """
        result = parse_findings(VALID)
        assert len(result.admitted[0].evidence) == 2

    def test_content_hash_is_computed(self) -> None:
        result = parse_findings(VALID)
        assert all(len(f.content_hash) == 64 for f in result.admitted)

    def test_enums_are_parsed(self) -> None:
        result = parse_findings(VALID)
        assert result.admitted[0].detectable_by is DetectableBy.ASSERT
        assert result.admitted[1].detectable_by is DetectableBy.JUDGE
        assert all(f.tier is Tier.DEFECT for f in result.admitted)

    def test_empty_document_is_not_an_error(self) -> None:
        # The former first line compared a pure function against itself and so
        # could not fail. These assert the actual contract.
        result = parse_findings("")
        assert result.admitted == ()
        assert result.excluded_questions == ()
        assert result.excluded_count == 0

    def test_reads_from_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "findings.yaml"
        path.write_text(VALID, encoding="utf-8")
        assert len(load_findings(path).admitted) == 2


class TestQuestionTierExclusion:
    """Question rows are non-defects and must never reach the anchor set."""

    MIXED = _doc(
        """
        findings:
          - id: F-001
            observation: A real defect.
            evidence: ["line 1: x"]
            consequence: Caller misinformed about money.
            detectable_by: assert
            tier: defect
          - id: Q-001
            observation: Is the verification gate scoped this way on purpose?
            evidence: ["line 9: y"]
            consequence: Unknown until the owning team answers.
            detectable_by: human
            tier: question
          - id: Q-002
            observation: Should this disclosure be required at all?
            evidence: ["line 12: z"]
            consequence: Unknown.
            detectable_by: human
            tier: question
        """
    )

    def test_questions_are_not_admitted(self) -> None:
        result = parse_findings(self.MIXED)
        assert [f.id for f in result.admitted] == ["F-001"]

    def test_excluded_ids_are_reported_not_dropped(self) -> None:
        result = parse_findings(self.MIXED)
        assert result.excluded_questions == ("Q-001", "Q-002")
        assert result.excluded_count == 2

    def test_no_question_survives_into_the_batch(self) -> None:
        result = parse_findings(self.MIXED)
        assert all(f.tier is Tier.DEFECT for f in result.admitted)


class TestRefusals:
    def test_severity_field_is_refused_by_name(self) -> None:
        doc = _doc(
            """
            findings:
              - id: F-001
                observation: o
                evidence: ["e"]
                consequence: c
                detectable_by: assert
                tier: defect
                severity: critical
            """
        )
        with pytest.raises(FindingSchemaError, match="severity"):
            parse_findings(doc)

    @pytest.mark.parametrize("missing", sorted(REQUIRED_FINDING_KEYS))
    def test_missing_required_key_names_the_key(self, missing: str) -> None:
        """Derived from the constant, not listed beside it.

        The hand-written list omitted `detectable_by` -- one of the six, and the
        only one with no case. Taking the parameters from `REQUIRED_FINDING_KEYS`
        means a key added there cannot arrive without a test.
        """
        lines = [
            "findings:",
            "  - id: F-001",
            "    observation: o",
            "    evidence: ['e']",
            "    consequence: c",
            "    detectable_by: assert",
            "    tier: defect",
        ]
        kept = [ln for ln in lines if not ln.strip().startswith(f"{missing}:")]
        kept = [
            ln.replace("  - id:", "  - _id:") if missing == "id" and "- id:" in ln else ln
            for ln in kept
        ]
        with pytest.raises(FindingSchemaError, match=missing):
            parse_findings("\n".join(kept))

    @pytest.mark.parametrize("blank", ["observation", "consequence"])
    def test_an_empty_text_field_is_refused_by_name(self, blank: str) -> None:
        """Both fields the guard loops over, not just the one that reached it.

        `findings.py` showed 100% line coverage with only the consequence case
        written, because the shared loop body was executed by that case. Line
        coverage counts lines, not cases -- worth remembering when reading any
        percentage in this project.
        """
        lines = [
            "findings:",
            "  - id: F-042",
            "    observation: something happened",
            '    evidence: ["e"]',
            "    consequence: someone is affected",
            "    detectable_by: assert",
            "    tier: defect",
        ]
        emptied = [
            f'    {blank}: "   "' if ln.strip().startswith(f"{blank}:") else ln for ln in lines
        ]
        with pytest.raises(FindingSchemaError, match=f"empty {blank}"):
            parse_findings("\n".join(emptied))

    def test_duplicate_id_is_refused(self) -> None:
        doc = _doc(
            """
            findings:
              - id: F-1
                observation: a
                evidence: ["e"]
                consequence: c
                detectable_by: assert
                tier: defect
              - id: F-1
                observation: b
                evidence: ["e"]
                consequence: c
                detectable_by: assert
                tier: defect
            """
        )
        with pytest.raises(FindingSchemaError, match="duplicate"):
            parse_findings(doc)

    def test_evidence_must_be_a_list(self) -> None:
        doc = _doc(
            """
            findings:
              - id: F-1
                observation: a
                evidence: "one string, not a list"
                consequence: c
                detectable_by: assert
                tier: defect
            """
        )
        with pytest.raises(FindingSchemaError, match="expected a list"):
            parse_findings(doc)
