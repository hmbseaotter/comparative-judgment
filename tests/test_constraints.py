"""Constraint tests — the properties asserted by what the code must NOT do.

These are the ones that fail silently if they regress. Nothing about a core
module importing a UI library, or a stray network call, or a model dependency
appearing in the lockfile would break any behavioral test; each would simply
make a documented guarantee untrue while everything still passed.
"""

from __future__ import annotations

import ast
import re
import socket
import tomllib
from collections.abc import Iterable
from pathlib import Path

import pytest

from comparative_judgment.cli import main
from comparative_judgment.core.models import Outcome
from comparative_judgment.core.session import Session
from comparative_judgment.core.store import Store

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The US spellings whose other-variety forms this repository converted away
#: from at D27. Stored in the US form and transformed below rather than listed
#: as the forms being forbidden, because this module is inside the scan's own
#: scope: writing them out here would mean the check finds the file that
#: defines it. A text checker cannot spell out what it forbids.
#:
#: Three words are deliberately absent -- analysis, realistic and optimistic.
#: They are spelled the same in both varieties, and the enumeration that
#: proposed the conversion reported all three because it matched a prefix
#: rather than a whole word.
US_SPELLINGS = frozenset(
    {
        "license",
        "regularization",
        "regularized",
        "unregularized",
        "normalization",
        "serialization",
        "initialize",
        "initializes",
        "initializing",
        "initializer",
        "initialization",
        "behavior",
        "behavioral",
        "behaviorally",
        "recognized",
        "optimizes",
        "maximizing",
    }
)

#: The file suffixes the conversion covered. LICENSE and uv.lock fall out of
#: this by suffix rather than by name: the first is a legal text and the second
#: is generated from package metadata, and neither should be rewritten to match
#: a house style.
TEXT_SUFFIXES = frozenset({".py", ".md", ".toml", ".yml", ".yaml"})

_NOT_SOURCE = frozenset(
    {".venv", ".git", "__pycache__", ".cj-store", ".ruff_cache", ".mypy_cache", ".pytest_cache"}
)
SRC = REPO_ROOT / "src" / "comparative_judgment"
CORE = SRC / "core"
UI = SRC / "ui"

#: Anything that would make this tool call a language model. The guarantee is
#: architectural, not a phase-one simplification: this tool's output is the
#: ground truth an LLM judge is measured against elsewhere, so a model inside it
#: would make the measuring instrument depend on the thing being measured.
#: Matched as substrings of a package name, not by equality, so `anthropic` also
#: catches `anthropic-bedrock` and `langchain` catches `langchain-community`. The
#: deny-list is the load-bearing artefact behind a guarantee described as
#: permanent, which is a reason to be generous with it rather than precise.
MODEL_PACKAGES = frozenset(
    {
        "anthropic",
        "openai",
        "google-generativeai",
        "google-genai",
        "vertexai",
        "cohere",
        "mistralai",
        "litellm",
        "langchain",
        "llama-index",
        "transformers",
        "huggingface-hub",
        "sentence-transformers",
        "ollama",
        "groq",
        "together",
        "replicate",
        "boto3",  # Bedrock
        "botocore",
    }
)


def _model_packages_in(names: Iterable[str]) -> list[str]:
    """Every supplied name that contains a denied package name."""
    return sorted(n for n in names if any(p in n for p in MODEL_PACKAGES))


UI_PACKAGES = frozenset({"textual", "rich", "tkinter", "PyQt5", "PySide6", "flask", "fastapi"})


def _locked_packages() -> list[dict[str, object]]:
    """Every `[[package]]` entry in the lockfile, parsed rather than grepped."""
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = lock.get("package", [])
    assert isinstance(packages, list)
    return [p for p in packages if isinstance(p, dict)]


def _imports(path: Path) -> set[str]:
    """Every module name imported by a file, at any depth."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _other_variety(american: str) -> str:
    """The other variety's spelling of one of the words above.

    Three substitutions cover every word this repository converted, and they
    are applied rather than listed for the reason the declaration gives: a
    literal table of the forms being forbidden would be found by the check that
    reads this file.
    """
    if american == "license":
        return "licence"
    if american.startswith("behavior"):
        return "behaviour" + american[len("behavior") :]
    return american.replace("iz", "is")


#: Matched as substrings rather than on word boundaries, deliberately. `_` is a
#: word character, so a boundary match cannot see inside an identifier -- and
#: three identifiers are exactly what the conversion had to rename by hand. A
#: guard blind to the case its own change needed a human for would be narrower
#: than the rule it enforces.
_OTHER_VARIETY = re.compile(
    "|".join(sorted(_other_variety(word) for word in US_SPELLINGS)), re.IGNORECASE
)


def _constructor_lines() -> range:
    """The lines this module spends constructing the forms it forbids.

    `_other_variety` has to write two of them out, because they are not
    reachable by the substitution the rest share. Located with `ast` rather
    than by a marker comment, so moving or reformatting that function cannot
    silently widen the hole -- and it is one small function, which is the
    narrowest exemption that lets the guard scan its own module at all.
    """
    module = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_other_variety":
            assert node.end_lineno is not None
            return range(node.lineno, node.end_lineno + 1)
    raise AssertionError("_other_variety is not defined in this module")


def _repository_text_files() -> list[Path]:
    return sorted(
        path
        for path in REPO_ROOT.rglob("*")
        if path.suffix in TEXT_SUFFIXES and not _NOT_SOURCE & set(path.parts)
    )


def _decision_bookkeeping(text: str) -> list[str]:
    """What is wrong with the decision record's numbering and its stated totals.

    Takes the text rather than reading it, so the control can run this over a
    record it has mutated. A control that restates the arithmetic in its own
    assertion proves the arithmetic, not the check.
    """
    problems: list[str] = []
    numbers = sorted(int(n) for n in re.findall(r"^## D(\d+)\b", text, re.MULTILINE))
    if not numbers:
        return ["no decisions found; the heading shape has changed"]
    if numbers != list(range(1, len(numbers) + 1)):
        problems.append(f"numbering has a gap or a repeat: {numbers}")

    high = numbers[-1]
    if f"Decisions **D1\u2013D{high}** recorded" not in text:
        problems.append(f"Document status does not state D1\u2013D{high}")
    if f"Numbering continues from **D{high + 1}**" not in text:
        problems.append(f"Document status does not continue from D{high + 1}")

    header = text.split("<!-- rules-required-from:")[0]
    if f"D1\u2013D{high} recorded" in header:
        problems.append("the header restates the current high-water mark, which C-9 retired")
    return problems


class TestCoreIsUIAgnostic:
    """The seam that decides whether a web adapter is a new front end or a rewrite."""

    def test_core_imports_no_ui_library(self) -> None:
        offenders: list[str] = []
        for path in sorted(CORE.glob("*.py")):
            for imported in _imports(path):
                root = imported.split(".")[0]
                if root in UI_PACKAGES:
                    offenders.append(f"{path.name} imports {imported}")
        assert not offenders, (
            "core must not import a UI library; a browser adapter is expected later "
            f"and would inherit these: {offenders}"
        )

    @staticmethod
    def _front_ends() -> list[Path]:
        """Every module in the package that is not core.

        Derived from the layout rather than listed. An earlier version globbed
        `ui/` alone, which made the check green while `cli.py` -- equally a front
        end, and the one actually reaching past the seam -- broke the rule it
        claimed to enforce. A check narrower than its rule is worse than no check,
        because the green is what a reader trusts.
        """
        return sorted(
            path
            for path in SRC.rglob("*.py")
            if CORE not in path.parents and path.name != "__init__.py"
        )

    def test_the_seam_scan_covers_both_front_ends(self) -> None:
        """The scan's universe must contain the CLI, not only the terminal UI."""
        covered = {path.name for path in self._front_ends()}
        assert {"cli.py", "tui.py"} <= covered, covered

    def test_front_ends_reach_core_only_through_the_session_interface(self) -> None:
        """A front end may use the session API, the record types and the errors.

        Reaching past the session into the store, the fit or the pairing logic is
        how session state ends up in a widget -- the mistake that would make this
        terminal work throwaway. `errors` is allowed because catching a named
        refusal is part of the contract, not a reach around it: the alternative is
        a front end that cannot tell a refusal from a crash.
        """
        allowed = {
            "comparative_judgment.core.session",
            "comparative_judgment.core.models",
            "comparative_judgment.core.errors",
        }
        offenders: list[str] = []
        for path in self._front_ends():
            for imported in _imports(path):
                if imported.startswith("comparative_judgment.core") and imported not in allowed:
                    offenders.append(f"{path.name} imports {imported}")
        assert not offenders, f"front ends must go through the session API: {offenders}"

    def test_no_front_end_touches_a_private_session_attribute(self) -> None:
        """`session._store` is the reach-around the import scan cannot see."""
        offenders = [
            f"{path.name}: {line.strip()}"
            for path in self._front_ends()
            for line in path.read_text(encoding="utf-8").splitlines()
            if "session._" in line or "_session._" in line
        ]
        assert not offenders, offenders


class TestNoModelDependency:
    def test_no_language_model_package_is_declared(self) -> None:
        manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        declared = list(manifest["project"]["dependencies"])
        for group in manifest.get("dependency-groups", {}).values():
            declared.extend(group)
        names = {
            entry.split(">")[0].split("=")[0].split("[")[0].strip().lower() for entry in declared
        }
        assert not _model_packages_in(names)

    def test_no_language_model_package_is_locked(self) -> None:
        """The manifest is the intent; the lockfile is what would install."""
        # `str(...)` rather than a cast: a lockfile entry's `name` is `object`
        # to the type checker, and this file's own subject is that a declared
        # constraint must be enforced rather than asserted. `mypy` as configured
        # in `pyproject.toml` covers `tests` and failed here, while CI ran
        # `--strict src` and passed -- the declared scope and the enforced scope
        # were different, and only the narrower one ever ran.
        locked = {str(package["name"]).lower() for package in _locked_packages()}
        assert not _model_packages_in(locked)


class TestNoNetwork:
    def test_no_module_in_the_package_imports_a_network_library(self) -> None:
        """A static scan, because it cannot be evaded by an unwalked code path.

        The runtime check below drives one route through the tool; this covers
        every module whether a test reaches it or not, which is the stronger of
        the two forms and the reason both are here.
        """
        network = {"socket", "http", "urllib", "urllib3", "requests", "httpx", "asyncio", "ssl"}
        offenders = [
            f"{path.relative_to(SRC)} imports {imported}"
            for path in sorted(SRC.rglob("*.py"))
            for imported in _imports(path)
            if imported.split(".")[0] in network
        ]
        assert not offenders, offenders

    def test_a_full_run_opens_no_socket(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail the run if anything reaches for a socket, rather than trusting it.

        Driven through `cli.main` end to end -- init, load, compare, cuts, fit,
        bands, export -- rather than through a hand-built `Session`. The earlier
        version never touched the CLI, the disk-reading findings loader or the
        severity writer, so "a full run" covered roughly half the modules.
        """

        def refuse(*args: object, **kwargs: object) -> None:
            msg = "this tool makes no network request of any kind"
            raise AssertionError(msg)

        monkeypatch.setattr(socket, "socket", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)
        monkeypatch.setattr(socket, "getaddrinfo", refuse)

        document = "\n".join(
            [
                "findings:",
                *[
                    f"  - id: F-{i}\n"
                    f"    observation: obs {i}\n"
                    f"    evidence: ['line {i}: x']\n"
                    f"    consequence: cons {i}\n"
                    "    detectable_by: assert\n"
                    "    tier: defect"
                    for i in range(4)
                ],
            ]
        )
        findings_path = tmp_path / "findings.yaml"
        findings_path.write_text(document, encoding="utf-8")
        store_path = str(tmp_path / ".cj-store")

        assert main(["init", "--store", store_path]) == 0
        assert main(["load", "--store", store_path, "--findings", str(findings_path)]) == 0

        session = Session(Store.open(tmp_path / ".cj-store"), rater_id="r", appearance_target=3)
        while session.next_pair() is not None:
            session.record(Outcome.LEFT)
        assert session.progress().complete

        ranked = [e.finding_id for e in session.fit().ranked()]
        assert main(["status", "--store", store_path, "--target", "2"]) == 0
        assert main(["fit", "--store", store_path, "--target", "2"]) == 0
        assert (
            main(
                [
                    "cuts",
                    "--store",
                    store_path,
                    "--target",
                    "2",
                    "--critical-high",
                    f"{ranked[0]}:{ranked[1]}",
                    "--high-medium",
                    f"{ranked[1]}:{ranked[2]}",
                    "--medium-low",
                    f"{ranked[2]}:{ranked[3]}",
                    "--critical-high-note",
                    "calibrated against the written consequence definitions",
                ]
            )
            == 0
        )
        assert main(["bands", "--store", store_path, "--target", "2"]) == 0
        assert (
            main(
                [
                    "export",
                    "--store",
                    store_path,
                    "--target",
                    "2",
                    "--out",
                    str(tmp_path / "severity.json"),
                ]
            )
            == 0
        )


class TestRepositoryHygiene:
    def test_gitignore_covers_the_conventional_store(self) -> None:
        """The docs name .cj-store/; the ignore file must name the same path.

        If these drift the protection covers nothing, which is the failure mode
        that made this a stated rule rather than a habit.
        """
        ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert ".cj-store/" in ignored

        from comparative_judgment.cli import CONVENTIONAL_STORE

        assert f"{CONVENTIONAL_STORE}/" in ignored

    def test_gitignore_covers_credentials_and_caches(self) -> None:
        ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (".env", "__pycache__/", "*.pyc"):
            assert pattern in ignored

    def test_readme_states_the_sensitivity_and_the_store_convention(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "sensitive" in readme.lower()
        assert ".cj-store" in readme
        assert "no implicit store path" in readme.lower()

    def test_lockfile_pins_exact_versions(self) -> None:
        """Pins, not floors -- the defect this project inherited as a lesson.

        Every package, parsed. The previous version asserted `'version = "' in
        lock` (true of any non-empty lockfile) and then inspected line index 2 of
        package block index 1 -- one line of one arbitrary package, silently
        repointed by adding or reordering a single entry. A positional index
        inside the file whose job is catching silent drift.
        """
        packages = _locked_packages()
        assert packages, "uv.lock parsed to nothing"
        unpinned = [
            f"{package['name']}: {package.get('version')!r}"
            for package in packages
            if not isinstance(package.get("version"), str)
            or any(op in str(package["version"]) for op in (">", "<", "*", "^", "~"))
        ]
        assert not unpinned, f"expressed as a floor or range rather than a pin: {unpinned}"

    def test_license_exists_and_is_apache(self) -> None:
        # The US form is a builtin and the form this repository converted away
        # from is not, so the rename would have introduced a shadow that the
        # older spelling happened to avoid.
        text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        assert "Apache License" in text
        assert "Copyright 2026 Saso Gale" in text


class TestDocumentation:
    """Properties of the written record that no behavioral test would notice."""

    DECISIONS = REPO_ROOT / "specs" / "comparative-judgment.decisions.md"

    def test_no_other_variety_spelling_returns(self) -> None:
        """D27 was a choice, and a choice with nothing behind it drifts back.

        The harness made the same conversion at `745d1a6` and added no guard, so
        its version is held by whoever next notices. This one is held by a list.

        Scoped by suffix rather than by an enumeration of files, so a document
        added tomorrow in a covered format is checked from the moment it exists
        rather than from whenever somebody remembers to add it.
        """
        scanned = _repository_text_files()
        assert len(scanned) >= 20, (
            f"the scan reached {len(scanned)} files, which is too few to be reading the "
            "repository; the suffix list or the skip set has drifted"
        )

        here = Path(__file__).resolve()
        constructor = _constructor_lines()

        offenders: list[str] = []
        for path in scanned:
            exempt = constructor if path.resolve() == here else range(0)
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if number in exempt:
                    continue
                offenders += [
                    f"{path.relative_to(REPO_ROOT)}:{number}: {word}"
                    for word in _OTHER_VARIETY.findall(line)
                ]
        assert not offenders, (
            "this repository converted to US spelling at D27, and these did not:\n  "
            + "\n  ".join(offenders)
        )

    def test_the_spelling_guard_finds_a_planted_word(self) -> None:
        """The guard above has only ever returned green.

        A pattern with a typo in it, or a scope reaching no file, reports a
        clean repository in exactly the words a clean repository produces. So
        the pattern is run against text that must match and text that must not.

        The third assertion is the one that matters most: it plants the case the
        conversion needed a human for, and would fail if this pattern were ever
        narrowed to word boundaries.
        """
        for american in US_SPELLINGS:
            planted = _other_variety(american)
            assert _OTHER_VARIETY.findall(planted), f"the guard does not find {american}'s pair"

        assert not _OTHER_VARIETY.findall("analysis realistic optimistic"), (
            "the guard flags words spelled the same in both varieties, which is the "
            "false positive the word list was built to avoid"
        )

        assert _OTHER_VARIETY.findall(_other_variety("regularized") + "_wins"), (
            "the guard cannot see inside an identifier, so it is blind to exactly the "
            "case the conversion had to rename by hand"
        )

        lines = Path(__file__).read_text(encoding="utf-8").splitlines()
        span = _constructor_lines()
        concealed = _OTHER_VARIETY.findall("\n".join(lines[span.start - 1 : span.stop - 1]))
        assert len(concealed) == 2, (
            f"the exemption conceals {len(concealed)} spellings rather than the two the "
            "constructor has to write out. Bounding what it hides rather than how many "
            "lines it spans is the point: an exemption nobody has to justify is one "
            "that grows, and line count is not what makes it dangerous."
        )

    def test_the_decision_record_states_its_own_high_water_mark(self) -> None:
        """The count of decisions, computed rather than maintained.

        The audit found it written twice -- in the header block and in
        `Document status` -- which is one place too many, since a number in two
        places is a number that goes stale in one of them. The header now points
        at the section, and the section is checked here.

        Gaplessness is asserted alongside it because the two failures look the
        same from a distance: a record can state the right total and still have
        skipped a number, and a reader citing `D19` wants that to mean one thing.
        """
        problems = _decision_bookkeeping(self.DECISIONS.read_text(encoding="utf-8"))
        assert not problems, "the decision record disagrees with itself:\n  " + "\n  ".join(
            problems
        )

    def test_the_high_water_guard_notices_a_stale_total(self) -> None:
        """The bookkeeping check, planted against the defect it was written for.

        Two plants, because the guard makes two different claims. Removing the
        last decision's heading must make the stated total wrong -- that is C-9's
        defect, a number left behind by the thing it counts. Renumbering a
        heading must make the sequence non-gapless without changing the total,
        which the first plant would not catch.
        """
        text = self.DECISIONS.read_text(encoding="utf-8")
        assert not _decision_bookkeeping(text), "the record is not clean, so neither plant is valid"

        numbers = sorted(int(n) for n in re.findall(r"^## D(\d+)\b", text, re.MULTILINE))
        high = numbers[-1]

        dropped = text.replace(f"## D{high} ", f"## Retired D{high} ", 1)
        assert any("Document status" in p for p in _decision_bookkeeping(dropped)), (
            "dropping the last decision left the stated total unchallenged"
        )

        renumbered = text.replace(f"## D{high} ", f"## D{high + 1} ", 1)
        assert any("gap or a repeat" in p for p in _decision_bookkeeping(renumbered)), (
            "renumbering a decision did not break gaplessness"
        )
