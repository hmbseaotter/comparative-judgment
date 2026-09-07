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


def _private_reaches(source: str) -> list[str]:
    """Reads of another object's private attribute, in one module's source.

    `self._x` is excluded: a class using its own internals is not reaching past
    a seam. Dunders are excluded because `__class__` and friends are protocol,
    not privacy.

    Takes the source rather than a path, so the control can run this over a
    module it has written rather than over one that exists.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Attribute):
            continue
        if not node.attr.startswith("_") or node.attr.startswith("__"):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            continue
        found.append(f"line {node.lineno}: .{node.attr}")
    return found


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

    Every form this module forbids is written out here and nowhere else, for
    the reason the declaration gives: a literal table beside the pattern would
    be found by the check that reads this file. `_constructor_lines` locates
    this function so the scan can skip exactly it.
    """
    irregular = {
        "license": "licence",
        "defense": "defence",
        "center": "centre",
        "catalog": "catalogue",
        "judgment": "judgement",
        "analyze": "analyse",
        "analyzed": "analysed",
        "analyzing": "analysing",
    }
    if american in irregular:
        return irregular[american]
    if american.startswith("behavior"):
        return "behaviour" + american[len("behavior") :]
    return american.replace("iz", "is")


#: The US forms whose pairs no substitution reaches, so the pattern has to name
#: them. Kept as US spellings; `_other_variety` is where they become the other
#: form, and it is the one function this module's own scan skips.
IRREGULAR = (
    "license",
    "defense",
    "center",
    "catalog",
    "judgment",
    "analyze",
    "analyzed",
    "analyzing",
)

#: The *shapes* of the variety this repository does not use, rather than a list
#: of its words. D29 records why: the first version of this guard listed what a
#: sweep had found, which made it exactly as wide as that sweep -- and the sweep
#: had missed ten forms sitting in the files it had just read.
#:
#: Anchored on word boundaries. Unanchored, the `-our` shape is found inside
#: ordinary words such as `resource`, which is how the first attempt failed.
#: Identifiers are handled by splitting them before matching instead -- see
#: `_flagged`. `-wise` is excluded by the pattern rather than by a list, since
#: there is no other variety of `pairwise` and such a list grows with every
#: compound anybody writes.
_OTHER_VARIETY = re.compile(
    r"\b(?:"
    r"[a-z]{3,}(?:isation|isations|ised|ises|ising|iser|isers|isable|(?<!w)ise)"
    r"|[a-z]{3,}(?:our|ours|oured|ouring|oural|ourally|ourer|ourers"
    r"|ourite|ourites|ourful|ourless)"
    r"|(?:travel|signal|label|relabel|cancel|marvel|counsel|fuel|dial)(?:led|ling|ler)"
    r"|" + "|".join(_other_variety(word) for word in IRREGULAR) + r")\b",
    re.IGNORECASE,
)

#: Words the pattern catches that are spelled the same in both varieties. Every
#: entry is here because a dictionary agrees it is not a difference -- `advised`
#: is not the other variety of anything -- and never because converting one was
#: inconvenient. An exemption nobody has to justify is one that grows.
#:
#: Most of these are the price of the bare `-ise`, which is what catches an
#: infinitive. That ending is also ordinary English, and paying for it in
#: declared exceptions is the trade: a pattern narrow enough to need no list
#: would miss the words this guard exists for.
#:
#: `analyses` is absent from the pattern rather than listed here, because it is
#: both a verb form and the plural of `analysis` and no single entry could be
#: right for both readings.
_SAME_IN_BOTH = frozenset(
    {
        "advise",
        "advised",
        "advises",
        "advising",
        "appraised",
        "chastise",
        "comprise",
        "comprised",
        "compromise",
        "compromised",
        "concise",
        "demise",
        "despise",
        "despised",
        "devise",
        "devised",
        "disguise",
        "disguised",
        "enterprise",
        "excise",
        "exercise",
        "exercised",
        "exercises",
        "exercising",
        "expertise",
        "franchise",
        "franchised",
        "imprecise",
        "improvise",
        "improvised",
        "incise",
        "merchandise",
        "paradise",
        "praised",
        "precise",
        "premise",
        "promise",
        "promised",
        "promises",
        "promising",
        "raised",
        "raises",
        "raising",
        "revise",
        "revised",
        "revises",
        "revising",
        "supervise",
        "supervised",
        "surprise",
        "surprised",
        "surprises",
        "surprising",
        "televise",
        "treatise",
        "unexercised",
        "unraised",
    }
)

#: Splits an identifier into the words a boundary-anchored pattern can see. `_`
#: is a word character and a camel hump is not a boundary, so a snake_case name
#: whose first component carries the suffix, and a CamelCase class name whose
#: second word does, are both invisible without this. Those are exactly the two
#: shapes the conversion had to rename by hand.
_IDENTIFIER_SEAM = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _flagged(text: str) -> list[str]:
    """The forms in `text` belonging to the variety this repository dropped."""
    split = _IDENTIFIER_SEAM.sub(" ", text).replace("_", " ")
    return [word for word in _OTHER_VARIETY.findall(split) if word.lower() not in _SAME_IN_BOTH]


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


#: What D14 moved out of phase 1, and the phrases the documents use to name it.
#: A line naming one of these has to say where the thing lives: carry a later
#: phase tag, sit inside a later phase's section, or write "phase 2". Anything
#: else is a phase-1 document promising a phase-2 feature.
_DEFERRED_BY_D14 = (
    "standard error",
    "session timer",
    "comparisons remaining",
    "estimated remaining",
    "elapsed session time",
)

#: Lines that name a deferred feature without making any claim about when it
#: arrives, keyed on a distinctive excerpt. Rewording one is exactly the edit
#: that should force it to be re-judged, so a key that survived rewording would
#: be the wrong key. Each entry says why it is not a phase claim -- an exemption
#: nobody has to justify is one that grows.
_NOT_A_PHASE_CLAIM: dict[str, str] = {
    "Deterministic (plain code, NO LLM)": (
        "An enumeration of what is computed without a model, across every phase. It says "
        "nothing about when each arrives, and naming phases in it would make a determinism "
        "guarantee read as a schedule."
    ),
    "the Bradley-Terry core is small": (
        "A sizing argument for choosing the model over a comparison sort. That the core is "
        "small including its standard errors is true whenever they are built."
    ),
}


def _phase_promises(text: str) -> list[str]:
    """Lines promising a D14-deferred feature without saying it is deferred.

    D14's Rule named "the spec linter's phase-tag agreement check" as its
    enforcement. This repository has no linter and no `tools/` directory, so
    that Rule named something that does not exist -- which is why the audit
    found four such lines by reading, and why working them turned up three more.

    Takes the text rather than reading it, so the control can run this over a
    document it has mutated.
    """
    problems: list[str] = []
    phase = 1
    for number, line in enumerate(text.splitlines(), 1):
        heading = re.match(r"### phase (\d)", line)
        if heading:
            phase = int(heading.group(1))
        if phase > 1:
            continue
        lowered = line.lower()
        named = [phrase for phrase in _DEFERRED_BY_D14 if phrase in lowered]
        if not named:
            continue
        if any(tag in line for tag in ("[P2]", "[P3]", "[P4]")) or "phase 2" in lowered:
            continue
        if any(key in line for key in _NOT_A_PHASE_CLAIM):
            continue
        problems.append(f"line {number} promises {named[0]!r} with no later phase named")
    return problems


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

    def test_no_front_end_reaches_past_the_seam_by_attribute(self) -> None:
        """The other half of the rule the import scan states (D32).

        `test_front_ends_reach_core_only_through_the_session_interface` reads
        imports, and `session._store.cuts()` needs no import at all. So a front
        end could take the store out of the session it was handed, put its
        contents in a widget, and pass a scan whose docstring says that is
        exactly what must not happen. A guard narrower than the rule it
        enforces, which is this repository's most-repeated finding.

        The convention this completes was unstated until D32. Tests reach into
        `session._store` and `session._fit()` freely and deliberately -- 43
        times when this was written -- because a test is allowed to know how
        the thing works. Production code is not, and the difference is the
        seam.
        """
        offenders = [
            f"{path.relative_to(SRC)} {reach}"
            for path in self._front_ends()
            for reach in _private_reaches(path.read_text(encoding="utf-8"))
        ]
        assert not offenders, (
            "a front end reaches past the session seam by attribute, which the "
            "import scan cannot see:\n  " + "\n  ".join(offenders)
        )

    def test_the_attribute_seam_check_finds_a_planted_reach(self) -> None:
        """The control. Every front end passing today proves nothing about it.

        Three plants, one per way the check could be wrong. The reach it exists
        for must be caught; `self._x` must not be, or every class in the package
        becomes an offender; and a dunder must not be, or `__class__` does.
        """
        assert _private_reaches("cuts = session._store.cuts()\n") == ["line 1: ._store"]
        assert not _private_reaches("class A:\n    def f(self):\n        return self._x\n")
        assert not _private_reaches("name = value.__class__.__name__\n")

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

    def test_no_dependency_is_expressed_only_as_a_floor(self) -> None:
        """C-8. The lockfile pinned and the declaration did not.

        A floor in the declaration is a claim that any later version will do,
        in a tool whose output is the ground truth another project measures
        against. The lockfile made the installed set exact and the declaration
        said something weaker, so the two documents disagreed about the same
        question and only one of them was checked.

        Dev dependencies are included, not only runtime ones. `ruff`, `mypy`
        and `pytest` decide whether a build passes, so a floor there is a gate
        whose behavior can change without anything in this repository changing.
        """
        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        declared: list[str] = list(config["project"]["dependencies"])
        for group in config.get("dependency-groups", {}).values():
            declared.extend(str(item) for item in group)

        assert declared, "no dependencies declared at all"
        unpinned = [
            requirement
            for requirement in declared
            if "==" not in requirement or ">" in requirement or "<" in requirement
        ]
        assert not unpinned, f"expressed as a floor or a range rather than a pin: {unpinned}"

    def test_ci_runs_the_coverage_gate_the_configuration_declares(self) -> None:
        """C-10. A floor nothing invokes is a number in a file.

        `fail_under` lives in pyproject so that one place holds it, but coverage
        is opt-in: pytest without `--cov` never loads the plugin, and the floor
        is then configuration that never runs. That is exactly the shape this
        repository has already been caught by once -- a type check whose
        declared scope and enforced scope were different, and only the narrower
        one ever ran.

        So this asserts the two ends meet: the floor is configured here, and the
        workflow asks for the measurement that applies it.
        """
        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        floor = config["tool"]["coverage"]["report"]["fail_under"]
        assert isinstance(floor, int) and floor >= 90, (
            f"the coverage floor is {floor!r}, which is not a gate worth having"
        )

        workflow = (REPO_ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
        assert "--cov=comparative_judgment" in workflow, (
            "the workflow's test step does not ask for coverage, so fail_under is "
            "configuration nothing applies"
        )

    def test_the_pin_guard_rejects_a_floor(self) -> None:
        """The control, because a repository that is already pinned proves nothing.

        Both shapes are planted: a bare floor, and the subtler one -- a pin with
        a range bolted on, which contains `==` and would satisfy a check that
        only looked for that.
        """

        def unpinned(declared: list[str]) -> list[str]:
            return [
                requirement
                for requirement in declared
                if "==" not in requirement or ">" in requirement or "<" in requirement
            ]

        assert unpinned(["numpy>=2.0"]) == ["numpy>=2.0"]
        assert unpinned(["numpy==2.5.2,<3"]) == ["numpy==2.5.2,<3"], (
            "a pin with a range attached passes a check that only looks for =="
        )
        assert unpinned(["numpy==2.5.2"]) == []

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
                    f"{path.relative_to(REPO_ROOT)}:{number}: {word}" for word in _flagged(line)
                ]
        assert not offenders, (
            "this repository converted to US spelling at D27, and these did not. Convert "
            "each -- or, where a word is spelled the same in both varieties and the "
            "pattern has merely caught its shape, add it to _SAME_IN_BOTH, which is a "
            "list of things a dictionary agrees about and not a list of things that were "
            "inconvenient:\n  " + "\n  ".join(offenders)
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
            assert _flagged(planted), f"the guard does not find {american}'s pair"

        assert not _flagged("analysis realistic optimistic pairwise imprecise"), (
            "the guard flags words spelled the same in both varieties, which is the "
            "false positive this pattern most needs to avoid"
        )
        assert not _flagged("advised revised exercising promised surprising"), (
            "the allowlist is not applied, so every -ised word in ordinary prose is "
            "reported as a spelling to convert"
        )

        # D29: the shapes the first, list-based version of this guard missed.
        # Planted by shape rather than by name, so a future word of the same
        # shape is caught without anybody adding it anywhere.
        for shape in ("local" + "isation", "signal" + "ling", "general" + "ised"):
            assert _flagged(shape), (
                f"{shape!r} is the shape D29 was written about and the guard misses it, "
                "so the pattern has been narrowed back into a list"
            )

        assert _flagged(_other_variety("regularized") + "_wins"), (
            "the guard cannot see inside an identifier, so it is blind to exactly the "
            "case the conversion had to rename by hand"
        )

        lines = Path(__file__).read_text(encoding="utf-8").splitlines()
        span = _constructor_lines()
        concealed = _flagged("\n".join(lines[span.start - 1 : span.stop - 1]))
        assert len(concealed) == len(IRREGULAR) + 1, (
            f"the exemption conceals {len(concealed)} spellings rather than the "
            f"{len(IRREGULAR)} irregular pairs plus the one built by rule. Bounding what "
            "it hides rather than how many lines it spans is the point: an exemption "
            "nobody has to justify is one that grows, and line count is not what makes "
            "one dangerous."
        )

    def test_no_phase_one_line_promises_a_feature_d14_deferred(self) -> None:
        """C-3, and the check D14's Rule said existed.

        D14 moved per-item standard errors, the session timer and the
        comparisons-remaining estimate to phase 2, and its own Consequences say
        the phase tags carry that and "the prose should not contradict". The
        prose contradicted it in seven places, three of them carrying `[P1]`
        and two of them in the phase-1 scope list itself.

        The rule this enforces is deliberately not "do not mention them". It is
        *say where they live*: a line naming one must carry a later phase tag,
        sit in a later phase's section, or write "phase 2". A document may
        discuss a deferred feature all it likes as long as a reader cannot
        finish the sentence believing it is here.
        """
        spec = (REPO_ROOT / "specs" / "comparative-judgment.md").read_text(encoding="utf-8")
        problems = _phase_promises(spec)
        assert not problems, (
            "the specification promises in phase 1 what D14 moved to phase 2:\n  "
            + "\n  ".join(problems)
        )

    def test_the_phase_promise_check_finds_a_planted_line(self) -> None:
        """The control, and both halves of the rule are planted.

        A line naming a deferred feature with no phase must be caught, and the
        three ways of naming the phase must each be accepted -- otherwise the
        check would pass by rejecting nothing or by rejecting everything, and
        the difference is invisible from a green suite.

        The exemption table is checked too. An entry for a line that no longer
        exists is an exemption nobody has to justify, which is how such a table
        stops meaning anything.
        """
        planted = "- WHILE [P1] a session is running, it SHALL show comparisons remaining.\n"
        assert _phase_promises(planted), "a bare phase-1 promise is not caught"

        for excused in (
            "- WHILE [P2] a session is running, it SHALL show comparisons remaining.\n",
            "- The estimate of comparisons remaining is phase 2 work.\n",
            "### phase 2 - diagnostics\n- Includes: per-item standard errors.\n",
        ):
            assert not _phase_promises(excused), f"a line naming its phase is rejected: {excused!r}"

        spec = (REPO_ROOT / "specs" / "comparative-judgment.md").read_text(encoding="utf-8")
        for key in _NOT_A_PHASE_CLAIM:
            assert key in spec, (
                f"{key!r} is exempted from the phase check and is not in the specification, "
                "so the exemption covers nothing and should be removed"
            )

    def test_no_acceptance_criterion_carries_a_tick(self) -> None:
        """C-6. A box nothing computes is a claim that decays.

        Thirteen of sixty-seven were ticked and nothing produced them, so each
        recorded what somebody believed while typing it. They were wrong in both
        directions: several unticked criteria were implemented and tested, and
        the 0.6.0 entry announcing the boxes cites *a criterion ticked against
        something adjacent to it* as the very defect they were meant to fix.

        D30 removed them rather than building the verifier that would make them
        mean something. This is what keeps them gone: re-ticking one is a build
        failure, and anyone who wants ticks back has to make them computed.
        """
        spec = (REPO_ROOT / "specs" / "comparative-judgment.md").read_text(encoding="utf-8")
        ticked = [line.strip() for line in spec.splitlines() if line.startswith("- [x]")]
        assert not ticked, (
            "acceptance criteria carry ticks again. D30 removed them because nothing "
            "computes them; if these are meant to be computed now, the thing that "
            "computes them is what this test should be reading:\n  " + "\n  ".join(ticked)
        )

    def test_the_criteria_are_still_there_to_be_ticked(self) -> None:
        """The control. An empty document satisfies the test above.

        `- [x]` disappearing because the criteria disappeared reads identically
        to `- [x]` disappearing because they were unticked, and only one of
        those is what D30 did. So the unticked boxes are counted, and the count
        is asserted to be a plausible number of criteria rather than merely
        non-zero.
        """
        spec = (REPO_ROOT / "specs" / "comparative-judgment.md").read_text(encoding="utf-8")
        unticked = [line for line in spec.splitlines() if line.startswith("- [ ]")]
        assert len(unticked) >= 50, (
            f"only {len(unticked)} acceptance criteria remain, which is too few for this "
            "specification; the tick guard above would pass on a document that had lost "
            "its criteria entirely"
        )

    def test_a_spec_push_asks_the_harness_to_check_the_interface(self) -> None:
        """C-5 / D15. The scanner runs there, and the drift starts here.

        `tools/check_spec_interface.py` lives in the consuming harness and runs
        only in the harness's CI, so an edit to this repository's spec that
        breaks the shared findings interface is green here and stays green
        until that repository happens to build. D15 recorded the gap and left
        it: *"judgment, not checkable until it is wired into a hook"*.

        This asserts the wiring, not the run. Whether the dispatch succeeds
        depends on a secret this test cannot see, and the workflow warns
        loudly when it is missing -- but the job, its narrowing to spec
        changes, and the repository it names are all things a future edit
        could quietly drop, and those are checkable from here.
        """
        workflow = (REPO_ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
        for fragment, why in (
            ("notify-harness:", "the dispatch job is gone"),
            ("voice-agent-eval-harness", "the dispatch no longer names the harness"),
            ("HARNESS_DISPATCH_TOKEN", "the dispatch no longer reads its token"),
            ("^specs/", "the dispatch no longer narrows to spec changes"),
            (
                "workflow_dispatch",
                "the dispatch can no longer be exercised by a manual run, so the only "
                "way to test it is to push the change it exists to check",
            ),
        ):
            assert fragment in workflow, (
                f"{why}, so a spec change here can break the shared findings interface "
                "and nothing will say so until the harness next builds"
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
